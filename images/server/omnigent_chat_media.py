"""Copies of the files agents show in the chat, kept by the server.

An agent shows a file in its reply with markdown image syntax,
``![Sign-in page](/home/omnigent/workspace/app/shot.png)``, and the web UI
loads that path from the session's sandbox (``0033-inline-media.patch``). A
sandbox that is asleep, or gone because its session was archived, can't serve
it, so this module keeps a copy of each such file on the server, in the
artifact store, and the chat falls back to it.

- **At the end of every turn** (``0027-chat-media-copies.patch`` reports each
  session status through :func:`on_status`), the newest replies are scanned
  for workspace paths in image syntax, and each one without a copy at least as
  new as the reply that shows it is fetched from the sandbox and kept.
- **Whenever the chat loads one** through ``GET /v1/sessions/{id}/chat-media``
  (``omnigent_chat_media_api``), the sandbox's file is served and the copy
  refreshed when it changed; with the sandbox out of reach, or the file gone,
  the copy is served instead.
- **Deleting a session** deletes its copies (:func:`forget`).

A copy is kept per session and per path as the reply wrote it, so a reply
that shows a path again after the agent changed the file refreshes it. Files
over :data:`MAX_COPY_BYTES` aren't kept. Nothing here imports Omnigent, so the
tests can load it on its own; ``omnigent_chat_media_api`` wires it to the
server. See docs/features/inline-media.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

_logger = logging.getLogger(__name__)

MAX_COPY_BYTES = 64 * 1024 * 1024
"""Files bigger than this are served from the sandbox but not kept."""
SCAN_ITEMS = 40
"""Newest conversation items a turn's end looks through for shown files."""
MAX_FETCHES_PER_SCAN = 20
"""Files one scan fetches at most; the rest wait for the next turn's end."""
MAX_CACHED_INDEXES = 200
"""Sessions whose index of copies stays in memory."""
KEY_PREFIX = "chat-media"

# An image in markdown: `![alt](target "title")`, with the target optionally in
# angle brackets. Close enough to CommonMark for paths agents write; a file it
# misses is still kept when the chat first loads it.
_IMAGE = re.compile(
    r"!\[(?:[^\]\\\n]|\\.)*\]\(\s*"
    r"(<[^<>\n]*>|(?:[^\s()]|\([^\s()]*\))+)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?\s*\)"
)
_FENCE = re.compile(r"(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\1[`~]*[ \t]*$|\Z)")
_INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)", re.S)
# A target that can only be a URL or an anchor, never a file: the web UI's
# NON_FILE_HREF. `notes.md:12` is a file and a line, not a scheme.
_NON_FILE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?::\d+)?$)|//|#)")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def normalize(cited: str) -> str:
    """A cited path as the chat asks for it: without leading ``./``."""
    return re.sub(r"^(?:\./)+", "", cited)


def media_paths(text: str) -> list[str]:
    """
    The workspace files a reply shows with image syntax, as the chat names them.

    Mirrors the web UI's markWorkspaceMedia: a ``file:`` URI names its path,
    URLs and anchors are skipped, query and fragment are dropped, and the path
    is percent-decoded unless that fails. Code blocks and code spans are
    skipped, as markdown doesn't render images there.

    :param text: A reply's markdown, e.g. ``"Done:\\n\\n![Home](shots/home.png)"``.
    :returns: The paths, in order, without repeats, e.g. ``["shots/home.png"]``.
    """
    text = _INLINE_CODE.sub(" ", _FENCE.sub("\n", text))
    found: dict[str, None] = {}
    for match in _IMAGE.finditer(text):
        target = match.group(1)
        if target.startswith("<"):
            target = target[1:-1].strip()
        path = _file_uri_path(target) if re.match(r"(?i)file:", target) else None
        if path is None:
            if not target or _NON_FILE.match(target):
                continue
            path = re.split(r"[?#]", target, maxsplit=1)[0]
            try:
                path = urllib.parse.unquote(path, errors="strict")
            except UnicodeDecodeError:
                pass  # A lone `%` is a legal filename character; keep the raw text.
        path = normalize(path)
        if path:
            found[path] = None
    return list(found)


def _file_uri_path(uri: str) -> str | None:
    """The absolute path a local ``file:`` URI names, or ``None``."""
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme.lower() != "file" or parsed.netloc or parsed.query:
        return None
    try:
        path = urllib.parse.unquote(parsed.path, errors="strict")
    except UnicodeDecodeError:
        return None
    if not path.startswith("/") or path.startswith("//") or path == "/" or re.search(r"[?#]", path):
        return None
    return None if parsed.fragment else path


@dataclass(frozen=True)
class Resolved:
    """Where a cited path is in the sandbox."""

    path: str
    """Workspace-relative, or absolute with a leading slash."""
    absolute: bool
    """Outside the workspace, so only the session's owner may read it."""


def needs_environment(cited: str) -> bool:
    """Whether resolving *cited* needs the workspace root and home directory."""
    return cited.startswith(("/", "~"))


def resolve(cited: str, root: str | None, home: str | None) -> Resolved | None:
    """
    Resolve a cited path the way the web UI's resolveChatFilePath does.

    :param cited: The path as the chat names it, e.g. ``"shots/home.png"``,
        ``"~/shot.png"`` or ``"/home/omnigent/workspace/app/shot.png"``.
    :param root: The workspace root, e.g. ``"/home/omnigent/workspace/app"``.
    :param home: The home directory, e.g. ``"/home/omnigent"``.
    :returns: Where it is, or ``None`` for a path that can't name a file
        there, such as one with ``..`` in it.
    """
    if not cited or "://" in cited or "?" in cited or "#" in cited:
        return None
    path = cited
    if home and (path == "~" or path.startswith("~/")):
        path = home.rstrip("/") + path[1:]
    if not path.startswith("/"):
        if path.startswith("~"):
            return None
        return None if _unsafe(path) else Resolved(path, absolute=False)
    if not root:
        return None
    norm_root = root.rstrip("/")
    if path in (norm_root, "/"):
        return None
    if path.startswith(f"{norm_root}/"):
        relative = path[len(norm_root) + 1 :]
        return None if _unsafe(relative) else Resolved(relative, absolute=False)
    return None if _unsafe(path[1:]) else Resolved(path, absolute=True)


def _unsafe(relative: str) -> bool:
    return any(segment in ("", ".", "..") for segment in relative.split("/"))


class BlobStore(Protocol):
    """The part of Omnigent's ArtifactStore this module uses."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class MediaCopies:
    """
    The copies, in an artifact store: per session, a blob per cited path and
    an ``index.json`` naming them, under ``chat-media/<session id>/``.

    Thread-safe; its methods block, so async callers run them in a thread.
    """

    def __init__(self, blobs: BlobStore) -> None:
        self._blobs = blobs
        self._lock = threading.Lock()
        self._indexes: OrderedDict[str, dict[str, dict[str, Any]]] = OrderedDict()

    @staticmethod
    def _index_key(session_id: str) -> str:
        return f"{KEY_PREFIX}/{session_id}/index.json"

    @staticmethod
    def _blob_key(session_id: str, cited: str) -> str:
        digest = hashlib.sha256(cited.encode()).hexdigest()[:32]
        return f"{KEY_PREFIX}/{session_id}/{digest}"

    def _index(self, session_id: str) -> dict[str, dict[str, Any]]:
        """The session's index, loaded once; the caller holds the lock."""
        index = self._indexes.get(session_id)
        if index is None:
            try:
                raw = json.loads(self._blobs.get(self._index_key(session_id)))
                index = raw.get("files", {}) if isinstance(raw, dict) else {}
            except KeyError:
                index = {}
            except (ValueError, OSError):
                _logger.warning("chat media index for %s unreadable", session_id, exc_info=True)
                index = {}
            self._indexes[session_id] = index
            while len(self._indexes) > MAX_CACHED_INDEXES:
                self._indexes.popitem(last=False)
        self._indexes.move_to_end(session_id)
        return index

    def entry(self, session_id: str, cited: str) -> dict[str, Any] | None:
        """
        What's kept of *cited*: its ``content_type``, ``bytes``, ``sha256``,
        ``captured_at`` (epoch seconds) and ``owner_only``; ``None`` without a copy.
        """
        if not _SESSION_ID.match(session_id):
            return None
        with self._lock:
            entry = self._index(session_id).get(cited)
            return dict(entry) if entry else None

    def get(self, session_id: str, cited: str) -> tuple[dict[str, Any], bytes] | None:
        """The copy of *cited* with its :meth:`entry`, or ``None``."""
        entry = self.entry(session_id, cited)
        if entry is None:
            return None
        try:
            return entry, self._blobs.get(entry["key"])
        except KeyError:
            return None

    def put(
        self,
        session_id: str,
        cited: str,
        data: bytes,
        *,
        content_type: str,
        owner_only: bool,
        now: float | None = None,
    ) -> bool:
        """
        Keep *data* as the copy of *cited*, replacing an older one.

        :returns: Whether it was kept: not for a bad session id or a file over
            :data:`MAX_COPY_BYTES`. Unchanged bytes aren't written again; only
            the time of the copy moves on.
        """
        if not _SESSION_ID.match(session_id) or len(data) > MAX_COPY_BYTES:
            return False
        digest = hashlib.sha256(data).hexdigest()
        key = self._blob_key(session_id, cited)
        with self._lock:
            index = self._index(session_id)
            old = index.get(cited)
            if old is None or old.get("sha256") != digest or old.get("key") != key:
                self._blobs.put(key, data)
            index[cited] = {
                "key": key,
                "content_type": content_type,
                "bytes": len(data),
                "sha256": digest,
                "captured_at": time.time() if now is None else now,
                "owner_only": owner_only,
            }
            self._blobs.put(
                self._index_key(session_id),
                json.dumps({"version": 1, "files": index}, separators=(",", ":")).encode(),
            )
        return True

    def forget(self, session_id: str) -> int:
        """Delete every copy kept for a session. :returns: How many there were."""
        if not _SESSION_ID.match(session_id):
            return 0
        with self._lock:
            index = self._index(session_id)
            for entry in index.values():
                try:
                    self._blobs.delete(entry["key"])
                except Exception:  # noqa: BLE001 - delete the rest regardless
                    _logger.warning("could not delete %s", entry.get("key"), exc_info=True)
            count = len(index)
            self._blobs.delete(self._index_key(session_id))
            self._indexes.pop(session_id, None)
        return count


@dataclass(frozen=True)
class Fetched:
    """A file read from a session's sandbox."""

    data: bytes
    content_type: str
    owner_only: bool


Fetcher = Callable[[str, str], Awaitable[Fetched | None]]
"""Reads ``(session id, cited path)`` from the sandbox; ``None`` when it can't."""
RecentReplies = Callable[[str, int], Iterable[tuple[float, str]]]
"""The newest ``n`` assistant replies of a session, as ``(created_at, markdown)``."""

_copies: MediaCopies | None = None
_recent_replies: RecentReplies | None = None
_fetch: Fetcher | None = None
_scanning: set[str] = set()
_rescan: set[str] = set()
_tasks: set[asyncio.Task[None]] = set()


def configure(
    copies: MediaCopies | None,
    recent_replies: RecentReplies | None = None,
    fetch: Fetcher | None = None,
) -> None:
    """Wire the module to the server's stores and sandboxes; ``None`` turns it off."""
    global _copies, _recent_replies, _fetch
    _copies, _recent_replies, _fetch = copies, recent_replies, fetch


def copies() -> MediaCopies | None:
    """The configured copies, or ``None``."""
    return _copies


def forget(session_id: str) -> None:
    """Delete a deleted session's copies, best effort."""
    if _copies is None:
        return
    try:
        _copies.forget(session_id)
    except Exception:  # noqa: BLE001 - a leftover copy must never block a delete
        _logger.warning("could not delete the chat media of %s", session_id, exc_info=True)


def on_status(session_id: str, status: str) -> None:
    """
    Follow a session's status: when a turn ends, keep what its replies showed.

    Called on the event loop for every status; does nothing off it, or while
    the module isn't configured. A scan already running for the session runs
    once more when it finishes.

    :param status: ``"running"``, ``"waiting"``, ``"idle"`` or ``"failed"``.
    """
    if status not in ("idle", "failed") or None in (_copies, _recent_replies, _fetch):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if session_id in _scanning:
        _rescan.add(session_id)
        return
    _scanning.add(session_id)
    task = loop.create_task(_scan_until_settled(session_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _scan_until_settled(session_id: str) -> None:
    try:
        while True:
            _rescan.discard(session_id)
            await scan(session_id)
            if session_id not in _rescan:
                return
    except Exception:  # noqa: BLE001 - a missed copy is fetched on the chat's next load
        _logger.warning("keeping the chat media of %s failed", session_id, exc_info=True)
    finally:
        _scanning.discard(session_id)
        _rescan.discard(session_id)


async def scan(session_id: str) -> int:
    """
    Keep the files the session's newest replies show that have no copy as new
    as the reply.

    :returns: How many files were fetched and kept.
    """
    copies, recent_replies, fetch = _copies, _recent_replies, _fetch
    if copies is None or recent_replies is None or fetch is None:
        return 0
    replies = await asyncio.to_thread(lambda: list(recent_replies(session_id, SCAN_ITEMS)))
    shown: dict[str, float] = {}
    for created_at, text in replies:
        for cited in media_paths(text):
            shown[cited] = max(shown.get(cited, 0.0), created_at)
    kept = 0
    fetched = 0
    for cited, shown_at in shown.items():
        entry = await asyncio.to_thread(copies.entry, session_id, cited)
        if entry is not None and entry.get("captured_at", 0) >= shown_at:
            continue
        if fetched >= MAX_FETCHES_PER_SCAN:
            break
        fetched += 1
        try:
            result = await fetch(session_id, cited)
        except Exception:  # noqa: BLE001 - one unreadable file mustn't stop the rest
            _logger.debug("fetching %s of %s failed", cited, session_id, exc_info=True)
            continue
        if result is None:
            continue
        if await asyncio.to_thread(
            lambda r=result, c=cited: copies.put(
                session_id, c, r.data, content_type=r.content_type, owner_only=r.owner_only
            )
        ):
            kept += 1
    return kept
