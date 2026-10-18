"""Routes behind Settings → MCP servers, global and per repository.

Header and environment values never appear in responses.

Every route is admin-only. ``0019-mcp-servers.patch`` mounts the router under
``/v1``, and the server image installs this file next to ``omnigent_mcp_servers``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent_repo_env_api import _core
import omnigent_mcp_servers as mcp_servers


class McpServerRoute(APIRoute):
    """Keep submitted header and variable values out of request-validation error responses."""
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise OmnigentError("Check the server's name, transport, agents and values.",
                                    code=ErrorCode.INVALID_INPUT) from None
        return safe_handler


class SaveMcpServer(BaseModel):
    """
    Body for ``PUT /v1/mcp-servers``. Without ``id`` it adds a server.

    ``headers`` and ``env`` map each name to its new value, or to ``null`` to
    keep the current one. Names left out are removed.
    """
    id: str | None = None
    # owner/repo or a clone URL for a repository's server; None for every session's.
    repository: str | None = None
    name: str = Field(min_length=1, max_length=64)
    transport: Literal["http", "stdio"]
    agents: list[Literal["claude", "codex"]]
    url: str | None = None
    headers: dict[str, str | None] = {}
    command: str | None = None
    args: list[str] = []
    env: dict[str, str | None] = {}


def create_settings_mcp_servers_router(auth_provider, is_admin, multi_user, core=_core):
    router = APIRouter(route_class=McpServerRoute)

    async def admin(request) -> str | None:
        if not multi_user:
            return None
        user = get_user_id(request, auth_provider)
        if user is None:
            raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
        if not await asyncio.to_thread(is_admin, user):
            raise OmnigentError("Admin privileges required to manage MCP servers",
                                code=ErrorCode.FORBIDDEN)
        return user

    async def run(call):
        try:
            return await asyncio.to_thread(call)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from None
        except Exception as exc:
            if getattr(exc, "status", None) == 409:
                raise OmnigentError("The MCP servers changed while you were editing them. Reload and try again.",
                                    code=ErrorCode.CONFLICT) from None
            # Kubernetes exceptions may contain the Secret's body. Never log them.
            raise OmnigentError("Couldn't update the MCP servers. Check server permissions and try again.",
                                code=ErrorCode.INTERNAL_ERROR) from None

    @router.get("/mcp-servers")
    async def listing(request: Request) -> dict[str, Any]:
        await admin(request)
        return await run(lambda: mcp_servers.summary(core()))

    @router.put("/mcp-servers")
    async def save(request: Request, body: SaveMcpServer) -> dict[str, Any]:
        user = await admin(request)
        return await run(lambda: mcp_servers.save(core(), body.model_dump(), updated_by=user))

    @router.delete("/mcp-servers/{server_id}")
    async def delete(request: Request, server_id: str, repository: str | None = None) -> dict[str, Any]:
        user = await admin(request)
        await run(lambda: mcp_servers.delete(core(), server_id, repository, updated_by=user))
        return {"id": server_id, "deleted": True}

    return router
