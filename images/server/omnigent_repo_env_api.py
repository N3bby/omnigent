"""Routes behind Settings → Repository variables.

Admins list, save and remove repositories' variables. Values only go from the
browser to the Secret, never back: listing returns names, and saving a name
without a value keeps the value it has. Anyone signed in can see the names of
a repository's variables, which the new-session form shows next to the
repository.

The server image installs this file as the top-level
``omnigent_repo_env_api`` module, and ``0006-repo-env-routes.patch`` mounts
the router under ``/v1``. The Secrets are the ones ``scripts/setup-repo-env``
writes, through the functions in ``omnigent_repo_env``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

import omnigent_repo_env as repo_env
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id

logger = logging.getLogger(__name__)


class SaveRepositoryRequest(BaseModel):
    """Body for ``PUT /v1/repo-env``.

    ``variables`` maps each name to its new value, or to ``null`` to keep the
    current one. Names left out are removed.
    """

    repository: str
    variables: dict[str, str | None]


@functools.cache
def _core() -> Any:
    """A ``CoreV1Api`` with the server's ServiceAccount, or a kubeconfig."""
    from kubernetes import client, config

    cfg = client.Configuration()
    try:
        config.load_incluster_config(client_configuration=cfg)
    except config.ConfigException:
        config.load_kube_config(client_configuration=cfg)
    return client.CoreV1Api(client.ApiClient(cfg))


def _key(repository: str) -> str:
    try:
        return repo_env.repo_key(repository)
    except ValueError as exc:
        raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc


async def _kubernetes(call: Callable[[], Any]) -> Any:
    """Run a Kubernetes call off the event loop, as an Omnigent error."""
    try:
        return await asyncio.to_thread(call)
    except ValueError as exc:
        raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
    except Exception as exc:
        status = getattr(exc, "status", None)
        if status == 409:
            raise OmnigentError(
                "The variables changed while you were editing them. Reload and try again.",
                code=ErrorCode.CONFLICT,
            ) from exc
        if status == 403:
            logger.error("Kubernetes refused a repository variables call: %s", exc)
            raise OmnigentError(
                f"The server may not manage Secrets in {repo_env.NAMESPACE}. "
                "Run mise run bootstrap to update its permissions.",
                code=ErrorCode.INTERNAL_ERROR,
            ) from exc
        if status == 422:
            raise OmnigentError(
                "Kubernetes rejected the variables. They may be too large.",
                code=ErrorCode.INVALID_INPUT,
            ) from exc
        logger.exception("Repository variables call failed")
        raise OmnigentError(
            "Couldn't reach the repository variables. Try again.",
            code=ErrorCode.INTERNAL_ERROR,
        ) from exc


def create_repo_env_router(
    auth_provider: AuthProvider | None,
    is_admin: Callable[[str], bool],
    multi_user: bool,
    core: Callable[[], Any] = _core,
) -> APIRouter:
    """
    Build the repository variables router (mounted under ``/v1``).

    :param auth_provider: Identifies the caller.
    :param is_admin: Whether a user may manage variables; the same check as
        ``/v1/me``'s ``is_admin``, so the Settings nav matches the routes.
    :param multi_user: False in single-user mode, where there's no one to
        check and every route is open, as with upstream's admin routes.
    :param core: Returns the ``CoreV1Api``; tests pass a fake.
    """
    router = APIRouter()

    async def signed_in(request: Request) -> str | None:
        if not multi_user:
            return None
        user_id = get_user_id(request, auth_provider)
        if user_id is None:
            raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
        return user_id

    async def admin(request: Request) -> str | None:
        user_id = await signed_in(request)
        if user_id is not None and not await asyncio.to_thread(is_admin, user_id):
            raise OmnigentError(
                "Admin privileges required to manage repository variables",
                code=ErrorCode.FORBIDDEN,
            )
        return user_id

    @router.get("/repo-env")
    async def list_repositories(request: Request) -> dict[str, Any]:
        """Every repository with variables, and their names (admin only)."""
        await admin(request)
        data = await _kubernetes(lambda: repo_env.list_repositories(core()))
        return {"object": "list", "data": data}

    @router.get("/repo-env/names")
    async def variable_names(request: Request, repository: str) -> dict[str, Any]:
        """The names of one repository's variables, for anyone signed in."""
        await signed_in(request)
        try:
            key = repo_env.repo_key(repository)
        except ValueError:
            return {"repository": None, "names": []}
        names = await _kubernetes(lambda: repo_env.variable_names(core(), key))
        return {"repository": key, "names": names}

    @router.put("/repo-env")
    async def save_repository(request: Request, body: SaveRepositoryRequest) -> dict[str, Any]:
        """Store a repository's variables, replacing what it had (admin only)."""
        user_id = await admin(request)
        key = _key(body.repository)
        return await _kubernetes(
            lambda: repo_env.save_repository(core(), key, body.variables, updated_by=user_id)
        )

    @router.delete("/repo-env")
    async def delete_repository(request: Request, repository: str) -> dict[str, Any]:
        """Remove a repository's variables (admin only)."""
        await admin(request)
        key = _key(repository)
        await _kubernetes(lambda: repo_env.delete_repository(core(), key))
        return {"repository": key, "deleted": True}

    return router
