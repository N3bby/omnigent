"""Routes for how a sandbox session starts: its log, and the runner Pod's reports.

- ``GET /v1/sessions/{id}/startup-log?after=<seq>``: the startup log's lines
  after *seq*, for the web UI's log panel, which polls it while it's open.
  Anyone who can read the session can read it.
- ``POST /v1/hosts/{host_id}/startup``: the runner Pod reports its setup
  scripts, Tailscale and Docker (``omnigent-startup-report`` in the runner
  image). Authenticated by the host's launch token, like the Git credential
  route.

The server image installs this file as the top-level ``omnigent_startup_api``
module, and ``0016-startup-progress.patch`` mounts the router under ``/v1``.
The record itself is ``omnigent_startup``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

import omnigent_startup as startup
from omnigent.server.auth import LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.stores import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store import PermissionStore

MAX_REPORT_BYTES = 256 * 1024
"""A report bigger than this is refused; the reporter sends small batches."""


def create_startup_router(
    *,
    conversation_store: ConversationStore,
    host_store: HostStore,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> APIRouter:
    """
    Build the startup routes.

    :param conversation_store: Sessions, for the read check.
    :param host_store: Resolves a launch token to its host.
    :param auth_provider: The server's auth, or ``None`` without auth.
    :param permission_store: Session permissions, or ``None``.
    :returns: A router to mount under ``/v1``.
    """
    router = APIRouter()

    @router.get("/sessions/{session_id}/startup-log", include_in_schema=False)
    async def startup_log(request: Request, session_id: str, after: int = 0) -> dict[str, Any]:
        """The session's startup log after line *after*."""
        user_id = get_user_id(request, auth_provider)
        await require_access(user_id, session_id, LEVEL_READ, permission_store, conversation_store)
        page = startup.log_page(session_id, max(0, after))
        if page is None:
            return {"started_at": None, "lines": [], "next": 0, "live": False}
        return page

    @router.post("/hosts/{host_id}/startup", include_in_schema=False)
    async def host_startup(
        host_id: str,
        request: Request,
        x_omnigent_host_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        """Take a runner Pod's report on its start."""
        if not x_omnigent_host_token:
            raise HTTPException(status_code=401, detail="missing host token")
        host = await asyncio.to_thread(
            host_store.resolve_launch_token, host_id, x_omnigent_host_token
        )
        if host is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        raw = await request.body()
        if len(raw) > MAX_REPORT_BYTES:
            raise HTTPException(status_code=413, detail="report too large")
        try:
            body = json.loads(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="report isn't JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="report isn't an object")
        return {"accepted": startup.host_report(host_id, body)}

    return router
