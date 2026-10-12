"""Admin-only subscriptions routes. Credentials never appear in responses."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent_repo_env_api import _core
import omnigent_subscriptions as subscriptions
import omnigent_subscription_login as login


class SubscriptionRoute(APIRoute):
    """Keep submitted credentials out of request-validation error responses."""
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise OmnigentError("Check the provider, subscription name and credential format.",
                                    code=ErrorCode.INVALID_INPUT) from None
        return safe_handler


class SaveSubscription(BaseModel):
    provider: Literal["claude", "codex"]
    name: str = Field(min_length=1, max_length=80)
    credential: str | None = None
    id: str | None = None


class LoginCodeRequest(BaseModel):
    code: str


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    id: str | None = None


def create_subscriptions_router(auth_provider, is_admin, multi_user, core=_core):
    router = APIRouter(route_class=SubscriptionRoute)

    async def admin(request):
        if not multi_user:
            return
        user = get_user_id(request, auth_provider)
        if user is None:
            raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
        if not await asyncio.to_thread(is_admin, user):
            raise OmnigentError("Admin privileges required", code=ErrorCode.FORBIDDEN)

    async def run(call):
        try:
            return await asyncio.to_thread(call)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from None
        except Exception as exc:
            if getattr(exc, "status", None) == 409:
                raise OmnigentError("Subscriptions changed. Reload and try again.", code=ErrorCode.CONFLICT) from None
            # Kubernetes exceptions may contain secret bodies. Never log them.
            raise OmnigentError("Couldn't update subscriptions. Check server permissions and try again.",
                                code=ErrorCode.INTERNAL_ERROR) from None

    @router.get("/subscriptions")
    async def listing(request: Request):
        await admin(request)
        return await run(lambda: subscriptions.summary(subscriptions.read(core())[0]))

    @router.put("/subscriptions")
    async def save(request: Request, body: SaveSubscription):
        await admin(request)
        return await run(lambda: subscriptions.save(core(), body.provider, body.name, body.credential, body.id))

    @router.post("/subscriptions/{account_id}/activate")
    async def activate(request: Request, account_id: str):
        await admin(request)
        return await run(lambda: subscriptions.activate(core(), account_id))

    @router.delete("/subscriptions/{account_id}")
    async def delete(request: Request, account_id: str):
        await admin(request)
        return await run(lambda: subscriptions.delete(core(), account_id))

    @router.post("/subscriptions/login/{provider}")
    async def start_login(request: Request, provider: str, body: LoginRequest):
        await admin(request)
        return await run(lambda: login.start(core(), body.name, body.id, provider))

    @router.post("/subscriptions/login/{login_id}/code")
    async def submit_login_code(request: Request, login_id: str, body: LoginCodeRequest):
        await admin(request)
        return await run(lambda: login.submit_code(core(), login_id, body.code))

    @router.get("/subscriptions/login/{login_id}")
    async def poll_login(request: Request, login_id: str):
        await admin(request)
        return await run(lambda: login.poll(core(), login_id))

    @router.delete("/subscriptions/login/{login_id}")
    async def cancel_login(request: Request, login_id: str):
        await admin(request)
        return await run(lambda: login.cancel(core(), login_id))

    return router
