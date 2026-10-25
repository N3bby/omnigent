"""Hermes sessions on an always-on host, instead of a sandbox each.

A session with the hermes agent only forwards its turns to your own Hermes
over SSH (packages/omnigent-hermes-remote), so a sandbox gives it nothing but
a start of several seconds. The deployment runs one Omnigent host for them,
in an always-on Pod (the omnigent-hermes-host Deployment), and the server
starts every Hermes session that would get a sandbox on that host instead,
whichever client creates it: the web UI, the glasses app or the Omnigent MCP
server. See docs/features/hermes-host.md.

- ``OMNIGENT_HERMES_HOST_ID`` names that host. Unset, nothing changes.
- When the host is offline, or isn't the creator's, the session gets a
  sandbox as before, so Hermes keeps working while the Pod restarts.
- The host runs nothing else: a create that names it for another agent is
  refused, an automation without a pinned host never picks it, and the host
  pickers leave it out (``hermes_only`` in the host list,
  images/server/web-patches/0040-hide-hermes-host.patch).

``images/server/patches/0024-hermes-host.patch`` calls :func:`place_session`
from ``POST /v1/sessions``, and :func:`is_hermes_host` from ``GET /v1/hosts``
and the scheduled tasks' host pick.
Only :func:`place_session` imports Omnigent, when it's called, so the tests
can load the rest on its own.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any

_logger = logging.getLogger(__name__)

HOST_ID_ENV_VAR = "OMNIGENT_HERMES_HOST_ID"
WORKSPACE_ENV_VAR = "OMNIGENT_HERMES_HOST_WORKSPACE"
DEFAULT_WORKSPACE = "/home/omnigent/hermes"
"""The runner's working directory on the host. Hermes works on its own machine,
so every session shares this empty one."""

HARNESS = "hermes-remote"
"""The harness of the sessions that run on the host."""

_HEX_ID = re.compile(r"[0-9a-f]{32}")


def host_id(environ: Mapping[str, str] | None = None) -> str | None:
    """The Hermes host's id, as host rows store it (32 hex digits), or ``None``."""
    env = os.environ if environ is None else environ
    raw = (env.get(HOST_ID_ENV_VAR) or "").strip()
    if not raw:
        return None
    normalized = raw.replace("-", "").lower()
    if not _HEX_ID.fullmatch(normalized):
        _logger.warning("%s isn't a host id: %r; Hermes sessions get sandboxes", HOST_ID_ENV_VAR, raw)
        return None
    return normalized


def workspace(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return (env.get(WORKSPACE_ENV_VAR) or "").strip() or DEFAULT_WORKSPACE


def is_hermes_host(candidate: str | None, environ: Mapping[str, str] | None = None) -> bool:
    configured = host_id(environ)
    if configured is None or not candidate:
        return False
    return candidate.replace("-", "").lower() == configured


def refusal(*, requested_host_id: str | None, harness: str | None,
            environ: Mapping[str, str] | None = None) -> str | None:
    """Why a create can't name this host, or ``None`` when it can."""
    if not is_hermes_host(requested_host_id, environ) or harness == HARNESS:
        return None
    return (
        "This host only runs Hermes sessions. Start this session in the sandbox "
        "or on another host."
    )


def should_place(*, harness: str | None, host_type: str, parent_session_id: str | None,
                 environ: Mapping[str, str] | None = None) -> bool:
    """Whether a create is one to start on the Hermes host, if the host can take it.

    Only a top-level Hermes session that would get a sandbox: one that names a
    host keeps it, and a sub-agent session shares its parent's runner.
    """
    return (
        harness == HARNESS
        and host_type == "managed"
        and parent_session_id is None
        and host_id(environ) is not None
    )


def rerouted(body: Any, *, target_host_id: str, target_workspace: str) -> Any:
    """The create request *body*, starting on the host instead of a sandbox.

    Repositories and a Git worktree are dropped: Hermes works on its own
    machine and never sees them. The fields are set explicitly, so a
    project's defaults for them don't apply afterwards.
    """
    data = body.model_dump(exclude_unset=True)
    for name in ("workspaces", "sandbox_provider"):
        data.pop(name, None)
    data.update(
        host_type="external",
        host_id=target_host_id,
        workspace=target_workspace,
        git=None,
    )
    return body.__class__.model_validate(data)


async def place_session(
    request: Any,
    body: Any,
    *,
    user_id: str | None,
    agent_store: Any,
    agent_cache: Any,
    project_store: Any,
) -> Any:
    """Start a Hermes session on the Hermes host when it's online and the caller's.

    Returns *body* unchanged for every other create, and whenever anything
    needed to decide can't be read: the create's own validation then reports
    it as usual.

    :raises OmnigentError: When the create names the Hermes host for an agent
        that isn't Hermes.
    """
    configured = host_id()
    if configured is None:
        return body
    import asyncio

    from omnigent.errors import ErrorCode, OmnigentError
    from omnigent.server.auth import RESERVED_USER_LOCAL
    from omnigent.server.routes._session_create_validation import (
        resolve_project_session_create,
    )
    from omnigent.server.routes._sessions.orchestration import _create_resolved_harness
    from omnigent.stores.host_store import host_is_live

    names_host = is_hermes_host(getattr(body, "host_id", None))
    if not names_host and getattr(body, "host_type", None) != "managed":
        return body
    try:
        resolved = (
            await resolve_project_session_create(
                body=body, user_id=user_id, project_store=project_store
            )
        ).body
        agent = await asyncio.to_thread(agent_store.get, resolved.agent_id)
        harness = (
            await asyncio.to_thread(
                _create_resolved_harness, agent, resolved.harness_override, agent_cache
            )
            if agent is not None
            else None
        )
    except (OmnigentError, ValueError, KeyError):
        return body

    reason = refusal(requested_host_id=getattr(body, "host_id", None), harness=harness)
    if reason is not None:
        raise OmnigentError(reason, code=ErrorCode.INVALID_INPUT)
    if not should_place(
        harness=harness, host_type=resolved.host_type, parent_session_id=resolved.parent_session_id
    ):
        return body

    host_store = getattr(request.app.state, "host_store", None)
    host = await asyncio.to_thread(host_store.get_host, configured) if host_store else None
    owner = user_id if user_id is not None else RESERVED_USER_LOCAL
    if host is None or host.user_id != owner or not host_is_live(host):
        _logger.info(
            "Hermes host %s is %s; this Hermes session gets a sandbox",
            configured,
            "unknown" if host is None else "someone else's" if host.user_id != owner else "offline",
        )
        return body
    return rerouted(body, target_host_id=configured, target_workspace=workspace())
