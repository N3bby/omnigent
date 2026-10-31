"""The route the chat loads the files agents show from, and the sandbox reads behind it.

``GET /v1/sessions/{id}/chat-media?path=<path>`` answers with the bytes of a
file a reply shows with image syntax, the path as the reply wrote it, for the
web UI's inline media (``0044-chat-media-copies.patch``):

1. From the session's sandbox, as the filesystem route's ``download=true``
   would, while the sandbox can be reached. The server's copy is refreshed
   when the file changed (``omnigent_chat_media``).
2. Otherwise, or when the sandbox no longer has the file, from the copy.

Anyone who can read the session can load a file in its workspace; one
outside it, like ``/tmp/shot.png``, only the session's owner, as in the file
viewer. The answer says where it came from in ``X-Omnigent-Chat-Media``:
``live`` or ``copy``.

The server image installs this file as the top-level ``omnigent_chat_media_api``
module, and ``0027-chat-media-copies.patch`` mounts the router under ``/v1``.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import urllib.parse
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

import omnigent_chat_media as media
from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_OWNER, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger(__name__)

MAX_PATH_CHARS = 4096
MAX_CACHED_ENVIRONMENTS = 500
"""Sessions whose workspace root and home directory stay in memory."""
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=None, pool=None)


@dataclass
class _Live:
    """A file the sandbox is sending, its body not read yet."""

    response: httpx.Response
    resolved: media.Resolved
    content_type: str


def create_chat_media_router(
    *,
    conversation_store: ConversationStore,
    artifact_store: Any,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> APIRouter:
    """
    Build the chat media route, and start keeping copies at each turn's end.

    :param conversation_store: Sessions, for the access checks and the replies.
    :param artifact_store: Where the copies go; ``None`` serves only the sandbox.
    :param auth_provider: The server's auth, or ``None`` without auth.
    :param permission_store: Session permissions, or ``None``.
    :returns: A router to mount under ``/v1``.
    """
    router = APIRouter()
    copies = media.MediaCopies(artifact_store) if artifact_store is not None else None
    # (root, home) per session, from the sandbox; neither moves while it lives.
    environments: OrderedDict[str, tuple[str | None, str | None]] = OrderedDict()

    async def runner_client(session_id: str) -> httpx.AsyncClient | None:
        """The session's runner, or ``None`` while it's asleep, gone or unbound."""
        from omnigent.server.routes._sessions.helpers import (
            _get_runner_client_for_resource_access,
        )

        conversation = await asyncio.to_thread(conversation_store.get_conversation, session_id)
        if conversation is None:
            return None
        try:
            return await _get_runner_client_for_resource_access(
                session_id, conversation=conversation
            )
        except (OmnigentError, HTTPException):
            return None

    async def workspace_paths(
        client: httpx.AsyncClient, session_id: str
    ) -> tuple[str | None, str | None]:
        """The sandbox's workspace root and home directory, as the web UI gets them."""
        cached = environments.get(session_id)
        if cached is not None:
            environments.move_to_end(session_id)
            return cached
        resp = await client.get(
            f"/v1/sessions/{session_id}/resources/environments/default", timeout=10.0
        )
        if resp.status_code != 200:
            return None, None
        body = resp.json()
        metadata = body.get("metadata") if isinstance(body, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}
        root, home = metadata.get("root"), metadata.get("home")
        paths = (root if isinstance(root, str) else None, home if isinstance(home, str) else None)
        if paths[0] is not None:
            environments[session_id] = paths
            while len(environments) > MAX_CACHED_ENVIRONMENTS:
                environments.popitem(last=False)
        return paths

    async def open_live(session_id: str, cited: str) -> _Live | None:
        """
        Start reading *cited* from the session's sandbox.

        :returns: The file on its way, which the caller must close, or
            ``None`` when the sandbox can't be reached, the path can't name a
            file there, or it has no such file.
        """
        client = await runner_client(session_id)
        if client is None:
            return None
        try:
            root, home = (
                await workspace_paths(client, session_id)
                if media.needs_environment(cited)
                else (None, None)
            )
            resolved = media.resolve(cited, root, home)
            if resolved is None:
                return None
            # The filesystem route's encoding: only an absolute path's leading
            # slash is encoded, so no proxy merges a "//".
            quoted = urllib.parse.quote(resolved.path.lstrip("/"))
            segment = f"%2F{quoted}" if resolved.absolute else quoted
            resp = await client.send(
                client.build_request(
                    "GET",
                    f"/v1/sessions/{session_id}/resources/environments/default"
                    f"/filesystem/{segment}?download=true",
                    timeout=_DOWNLOAD_TIMEOUT,
                ),
                stream=True,
            )
        except (httpx.HTTPError, ConnectionError, OmnigentError, HTTPException, ValueError):
            _logger.debug("chat media %s of %s: sandbox unreachable", cited, session_id,
                          exc_info=True)
            return None
        if resp.status_code != 200 or "content-disposition" not in resp.headers:
            await resp.aclose()
            return None
        content_type = (
            resp.headers.get("content-type")
            or mimetypes.guess_type(resolved.path)[0]
            or "application/octet-stream"
        )
        return _Live(resp, resolved, content_type)

    async def read_whole(live: _Live) -> tuple[list[bytes], bool]:
        """
        Read the file up to :data:`media.MAX_COPY_BYTES`.

        :returns: The chunks read, and whether that's the whole file. When it
            isn't, the rest is still to come from ``live.response``.
        """
        chunks: list[bytes] = []
        total = 0
        length = live.response.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > media.MAX_COPY_BYTES:
            return chunks, False
        async for chunk in live.response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total > media.MAX_COPY_BYTES:
                return chunks, False
        return chunks, True

    async def fetch(session_id: str, cited: str) -> media.Fetched | None:
        """Read a whole file for a copy, at a turn's end."""
        live = await open_live(session_id, cited)
        if live is None:
            return None
        try:
            chunks, whole = await read_whole(live)
        finally:
            await live.response.aclose()
        if not whole:
            return None
        return media.Fetched(b"".join(chunks), live.content_type, live.resolved.absolute)

    def recent_replies(session_id: str, limit: int) -> list[tuple[float, str]]:
        """The session's newest assistant replies, as ``(created_at, markdown)``."""
        page = conversation_store.list_items(session_id, limit=limit, order="desc")
        replies = []
        for item in page.data:
            data = item.data
            if item.type != "message" or getattr(data, "role", None) != "assistant":
                continue
            if getattr(data, "is_meta", False):
                continue
            text = "\n\n".join(
                block["text"]
                for block in data.content
                if block.get("type") == "output_text" and isinstance(block.get("text"), str)
            )
            if text:
                replies.append((float(item.created_at), text))
        return replies

    media.configure(copies, recent_replies, fetch)

    def media_headers(content_type: str, where: str) -> dict[str, str]:
        # The bytes are whatever the agent wrote: never let a browser that
        # opens this URL directly run them as a page on this origin.
        return {
            "Content-Type": content_type,
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "X-Omnigent-Chat-Media": where,
        }

    @router.get("/sessions/{session_id}/chat-media", include_in_schema=False)
    async def chat_media(
        request: Request,
        session_id: str,
        path: str = Query(..., min_length=1, max_length=MAX_PATH_CHARS),
    ) -> Response:
        """A file a reply shows: from the sandbox, or the server's copy."""
        user_id = get_user_id(request, auth_provider)
        await require_access(user_id, session_id, LEVEL_READ, permission_store, conversation_store)
        cited = media.normalize(path)

        async def require_owner() -> None:
            await require_access(
                user_id, session_id, LEVEL_OWNER, permission_store, conversation_store
            )

        live = await open_live(session_id, cited) if cited else None
        if live is not None:
            try:
                if live.resolved.absolute:
                    await require_owner()
                chunks, whole = await read_whole(live)
            except BaseException:
                await live.response.aclose()
                raise
            if whole:
                await live.response.aclose()
                data = b"".join(chunks)
                if copies is not None:
                    try:
                        await asyncio.to_thread(
                            copies.put,
                            session_id,
                            cited,
                            data,
                            content_type=live.content_type,
                            owner_only=live.resolved.absolute,
                        )
                    except Exception:  # noqa: BLE001 - the chat still gets the file
                        _logger.warning("could not keep chat media %s of %s", cited, session_id,
                                        exc_info=True)
                return Response(data, headers=media_headers(live.content_type, "live"))

            async def rest() -> AsyncIterator[bytes]:
                try:
                    for chunk in chunks:
                        yield chunk
                    async for chunk in live.response.aiter_bytes():
                        yield chunk
                finally:
                    await live.response.aclose()

            return StreamingResponse(rest(), headers=media_headers(live.content_type, "live"))

        kept = await asyncio.to_thread(copies.get, session_id, cited) if copies and cited else None
        if kept is None:
            raise HTTPException(status_code=404, detail="file not found")
        entry, data = kept
        if entry.get("owner_only"):
            await require_owner()
        return Response(
            data,
            headers=media_headers(entry.get("content_type") or "application/octet-stream", "copy"),
        )

    return router
