"""Keep the always-on Hermes host to Hermes sessions.

A session with the hermes agent only forwards its turns to your own Hermes
over SSH (packages/omnigent-hermes-remote), so a sandbox gives it nothing but
a start of several seconds. The deployment runs one Omnigent host for these
sessions in an always-on Pod (the omnigent-hermes-host Deployment), which you
pick like any connected host, such as a project's default host. See
docs/features/hermes-host.md.

That host is signed in as you, and an agent with a shell there could read the
sign-in. So it runs only the hermes-remote harness, which runs no tools of its
own:

- ``OMNIGENT_HERMES_HOST_ID`` names the host. Unset, nothing changes.
- A create that names the host for another agent is refused
  (:func:`check_create`).
- An automation without a pinned host never picks it (:func:`is_hermes_host`).

``images/server/patches/0024-hermes-host.patch`` calls :func:`check_create`
from ``POST /v1/sessions`` and :func:`is_hermes_host` from the scheduled tasks'
host pick. Only :func:`check_create` imports Omnigent, when it's called, so the
tests can load the rest on its own.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any

_logger = logging.getLogger(__name__)

HOST_ID_ENV_VAR = "OMNIGENT_HERMES_HOST_ID"

HARNESS = "hermes-remote"
"""The harness of the sessions the host runs."""

_HEX_ID = re.compile(r"[0-9a-f]{32}")


def host_id(environ: Mapping[str, str] | None = None) -> str | None:
    """The Hermes host's id, as host rows store it (32 hex digits), or ``None``."""
    env = os.environ if environ is None else environ
    raw = (env.get(HOST_ID_ENV_VAR) or "").strip()
    if not raw:
        return None
    normalized = raw.replace("-", "").lower()
    if not _HEX_ID.fullmatch(normalized):
        _logger.warning("%s isn't a host id: %r; ignoring it", HOST_ID_ENV_VAR, raw)
        return None
    return normalized


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
        "The Hermes host only runs Hermes sessions. Start this session in the "
        "sandbox or on another host."
    )


async def check_create(
    body: Any,
    *,
    user_id: str | None,
    agent_store: Any,
    agent_cache: Any,
    project_store: Any,
) -> None:
    """Refuse a create that names the Hermes host for an agent that isn't Hermes.

    :raises OmnigentError: When it does. When the agent can't be resolved, its
        harness is unknown, so the create is refused too.
    """
    if not is_hermes_host(getattr(body, "host_id", None)):
        return
    import asyncio

    from omnigent.errors import ErrorCode, OmnigentError
    from omnigent.server.routes._session_create_validation import (
        resolve_project_session_create,
    )
    from omnigent.server.routes._sessions.orchestration import _create_resolved_harness

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
    except (ValueError, KeyError):
        harness = None
    reason = refusal(requested_host_id=body.host_id, harness=harness)
    if reason is not None:
        raise OmnigentError(reason, code=ErrorCode.INVALID_INPUT)
