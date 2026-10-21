"""The routes agents' progress bars and their logs come in and go out by.

- ``POST /v1/sessions/{id}/progress``: set the session's bar,
  ``{"label": "Running tests", "percent": 40, "log_file": "/home/omnigent/tests.log"}``,
  or end it, ``{"clear": true}``. The runner posts it when the agent calls
  ``sys_session_progress``. Anyone who can edit the session can set it, like
  its title.
- ``POST /v1/sessions/{id}/progress/{bar_id}/log``: lines the bar's log file
  gained, ``{"lines": ["..."]}``, from the runner, which follows the file.
  Answers ``{"kept": false}`` once the bar is gone, so the runner stops.
- ``GET /v1/sessions/{id}/progress/{bar_id}/log?after=<line>``: the bar's log
  after a line, for the web UI, which polls it while the log is open. Anyone
  who can read the session can read it.

The server image installs this file as the top-level ``omnigent_progress_api``
module, and ``0022-agent-progress.patch`` mounts the router under ``/v1``.
The bars themselves are ``omnigent_progress``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import omnigent_progress as progress
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore

MAX_BODY_BYTES = 4096
"""An update bigger than this is refused; a real one is a few dozen bytes."""
MAX_LOG_BODY_BYTES = 1024 * 1024
"""A log post bigger than this is refused; the runner sends small batches."""


def create_progress_router(
    *,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None,
    permission_store: PermissionStore | None,
) -> APIRouter:
    """
    Build the progress routes.

    :param conversation_store: Sessions, for the access checks.
    :param auth_provider: The server's auth, or ``None`` without auth.
    :param permission_store: Session permissions, or ``None``.
    :returns: A router to mount under ``/v1``.
    """
    router = APIRouter()

    async def body_of(request: Request, limit: int) -> Any:
        raw = await request.body()
        if len(raw) > limit:
            raise HTTPException(status_code=413, detail="too large")
        try:
            return json.loads(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="the body isn't JSON") from exc

    @router.post("/sessions/{session_id}/progress", include_in_schema=False)
    async def set_progress(request: Request, session_id: str) -> dict[str, Any]:
        """Set or end the session's progress bar."""
        user_id = get_user_id(request, auth_provider)
        await require_access(user_id, session_id, LEVEL_EDIT, permission_store, conversation_store)
        body = await body_of(request, MAX_BODY_BYTES)
        try:
            current = progress.update(session_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"progress": current}

    @router.post("/sessions/{session_id}/progress/{bar_id}/log", include_in_schema=False)
    async def add_log(request: Request, session_id: str, bar_id: str) -> dict[str, bool]:
        """Take lines a bar's log file gained."""
        user_id = get_user_id(request, auth_provider)
        await require_access(user_id, session_id, LEVEL_EDIT, permission_store, conversation_store)
        body = await body_of(request, MAX_LOG_BODY_BYTES)
        lines = body.get("lines") if isinstance(body, dict) else None
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise HTTPException(status_code=422, detail="lines must be a list of strings")
        return {"kept": progress.append_log(session_id, bar_id, lines)}

    @router.get("/sessions/{session_id}/progress/{bar_id}/log", include_in_schema=False)
    async def read_log(
        request: Request, session_id: str, bar_id: str, after: int = 0
    ) -> dict[str, Any]:
        """A bar's log after line *after*."""
        user_id = get_user_id(request, auth_provider)
        await require_access(user_id, session_id, LEVEL_READ, permission_store, conversation_store)
        page = progress.log_page(session_id, bar_id, max(0, after))
        if page is None:
            raise HTTPException(status_code=404, detail="no such progress bar")
        return page

    return router
