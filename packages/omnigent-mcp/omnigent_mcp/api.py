"""A small client for the parts of Omnigent's REST API the tools use."""
from __future__ import annotations

from typing import Any

import httpx

from .auth import AuthError, TokenSource, error_text

# Most calls answer at once. Posting a message to a session whose sandbox is
# starting waits for the sandbox, which the server allows four minutes.
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MESSAGE_TIMEOUT = httpx.Timeout(330.0, connect=10.0)


class OmnigentError(RuntimeError):
    """Omnigent refused a request or couldn't be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OmnigentApi:
    def __init__(self, client: httpx.AsyncClient, tokens: TokenSource) -> None:
        self._client = client
        self._tokens = tokens

    @property
    def server(self) -> str:
        return self._tokens.server

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> Any:
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        try:
            response = await self._send(method, path, params, json, timeout, renew=False)
            if response.status_code == 401:
                # The access token may have been cut short, such as by a
                # server restart with a new key: renew it once.
                response = await self._send(method, path, params, json, timeout, renew=True)
        except AuthError as exc:
            raise OmnigentError(str(exc), status=401) from exc
        except httpx.TimeoutException as exc:
            raise OmnigentError(f"Omnigent didn't answer {method} {path} in time") from exc
        except httpx.HTTPError as exc:
            raise OmnigentError(f"Can't reach {self.server}: {exc}") from exc
        if response.status_code >= 400:
            raise OmnigentError(
                f"Omnigent answered {response.status_code}: {error_text(response)}",
                status=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: Any,
        timeout: httpx.Timeout,
        *,
        renew: bool,
    ) -> httpx.Response:
        token = await self._tokens.token(renew=renew)
        return await self._client.request(
            method,
            f"{self.server}{path}",
            params=params,
            json=json,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )
