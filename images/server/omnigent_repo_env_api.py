"""Routes behind Settings → Repository variables, and the repository picker's tokens.

Admins list, save and remove repositories' variables. Values only go from the
browser to the Secret, never back: listing returns names, and saving a name
without a value keeps the value it has. Anyone signed in can see the names of
a repository's variables, which the new-session form shows next to the
repository.

A repository with a ``GIT_TOKEN`` variable is also in the repository picker,
for everyone who has connected GitHub, and its branches are listed with that
token. ``0010-repo-git-token.patch`` calls :func:`add_token_repositories` and
:func:`repository_token` from the GitHub picker routes.

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

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

import omnigent_repo_env as repo_env
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_TIMEOUT_S = 15.0


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


async def _picker_entry(client: httpx.AsyncClient, key: str, token: str) -> dict[str, object]:
    """
    A repository with a ``GIT_TOKEN``, as the picker lists it.

    GitHub fills in the details when the token can read the repository.
    When it can't, the repository is still listed, so a session started on
    it fails on the clone, where the error says why.
    """
    full_name = key.split("/", 1)[1]
    entry: dict[str, object] = {
        "full_name": full_name,
        "clone_url": f"https://github.com/{full_name}.git",
        "default_branch": None,
        "private": True,
        "pushed_at": None,
    }
    try:
        resp = await client.get(
            f"{_GITHUB_API}/repos/{full_name}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if resp.status_code != 200:
            logger.warning("GitHub answered %s for %s with its GIT_TOKEN", resp.status_code, key)
            return entry
        data = resp.json()
        entry.update(
            full_name=str(data["full_name"]),
            clone_url=data.get("clone_url") or entry["clone_url"],
            default_branch=data.get("default_branch"),
            private=bool(data.get("private")),
            pushed_at=data.get("pushed_at"),
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Couldn't look up %s with its GIT_TOKEN: %s", key, exc)
    return entry


async def add_token_repositories(
    repos: list[dict[str, object]],
    core: Callable[[], Any] = _core,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, object]]:
    """
    Add the repositories with a ``GIT_TOKEN`` to the GitHub App's list.

    A repository the App already lists keeps the App's entry. The result
    stays newest push first. If the Secrets can't be read, the App's list
    comes back as it was.

    :param repos: ``/v1/connections/github/repos`` entries, newest push first.
    :param core: Returns the ``CoreV1Api``; tests pass a fake.
    :param transport: For tests, an ``httpx.MockTransport``.
    :returns: The combined list.
    """
    try:
        tokens = await asyncio.to_thread(lambda: repo_env.token_repositories(core()))
    except Exception:
        logger.exception("Couldn't read the repositories with a GIT_TOKEN")
        return repos
    listed = {str(repo.get("full_name", "")).lower() for repo in repos}
    missing = {key: token for key, token in tokens.items() if key.split("/", 1)[1] not in listed}
    if not missing:
        return repos
    async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT_S, transport=transport) as client:
        extra = await asyncio.gather(
            *(_picker_entry(client, key, token) for key, token in sorted(missing.items()))
        )
    # ISO times sort as text; a repository without one goes last.
    return sorted([*repos, *extra], key=lambda repo: str(repo.get("pushed_at") or ""), reverse=True)


async def repository_token(owner: str, repo: str, core: Callable[[], Any] = _core) -> str | None:
    """
    The ``GIT_TOKEN`` of ``github.com/owner/repo``, for listing its branches.

    :returns: The token, or ``None`` when it has none or the Secret can't be
        read, so the caller falls back to the GitHub App token.
    """
    try:
        key = repo_env.repo_key(f"{owner}/{repo}")
        return await asyncio.to_thread(lambda: repo_env.repository_token(core(), key))
    except Exception:
        logger.exception("Couldn't read the GIT_TOKEN of %s/%s", owner, repo)
        return None
