"""Signing in to an Omnigent server and staying signed in.

``omnigent-mcp login`` signs in once with a username and password, as
``omnigent login`` does, and asks the server for a refresh token. Only that
token is kept, in a file only its user can read. The server issues CLI logins
a refresh token that doesn't rotate and lasts 30 days (or the server's
``OMNIGENT_GRANT_MAX_LIFETIME_DAYS``), and trades it for an access token that
lasts an hour, as often as needed.

This module imports nothing from Omnigent, so it runs without Omnigent
installed.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# Settings from the environment win over the credentials file, so Hermes's
# config can carry them instead.
URL_ENV = "OMNIGENT_URL"
REFRESH_TOKEN_ENV = "OMNIGENT_REFRESH_TOKEN"
CREDENTIALS_ENV = "OMNIGENT_MCP_CREDENTIALS"

# Renew an access token this long before it expires, so a slow request
# doesn't outlive it.
_RENEW_EARLY_SECONDS = 120


class AuthError(RuntimeError):
    """Not signed in, or the server no longer accepts the sign-in."""


@dataclass(frozen=True)
class Credentials:
    server: str
    refresh_token: str
    username: str | None = None


def credentials_path() -> Path:
    explicit = os.environ.get(CREDENTIALS_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip() or str(Path.home() / ".config")
    return Path(config_home) / "omnigent-mcp" / "credentials.json"


def normalize_server(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def load_credentials(path: Path | None = None) -> Credentials:
    """Read the sign-in from the environment, or else the credentials file."""
    url = os.environ.get(URL_ENV, "").strip()
    token = os.environ.get(REFRESH_TOKEN_ENV, "").strip()
    if url and token:
        return Credentials(server=normalize_server(url), refresh_token=token)
    path = path or credentials_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise AuthError(
            "Not signed in to Omnigent: run `omnigent-mcp login --server <url>` "
            f"on this machine (no {path})."
        ) from None
    except (OSError, ValueError) as exc:
        raise AuthError(f"Can't read the Omnigent sign-in in {path}: {exc}") from exc
    server = data.get("server") if isinstance(data, dict) else None
    refresh = data.get("refresh_token") if isinstance(data, dict) else None
    if not isinstance(server, str) or not isinstance(refresh, str) or not refresh:
        raise AuthError(f"{path} has no Omnigent sign-in: run `omnigent-mcp login` again.")
    username = data.get("username")
    return Credentials(
        server=normalize_server(url or server),
        refresh_token=refresh,
        username=username if isinstance(username, str) else None,
    )


def save_credentials(creds: Credentials, path: Path | None = None) -> Path:
    """Write the sign-in where only this user can read it, replacing any."""
    path = path or credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "server": creds.server,
        "username": creds.username,
        "refresh_token": creds.refresh_token,
        "saved_at": int(time.time()),
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def error_text(response: httpx.Response) -> str:
    """The message in an Omnigent error response, or its status."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
    text = response.text.strip()
    return text[:300] if text else f"HTTP {response.status_code}"


async def login(
    client: httpx.AsyncClient, server: str, username: str, password: str
) -> Credentials:
    """Sign in with a password and return the sign-in to keep."""
    try:
        response = await client.post(
            f"{server}/auth/login",
            json={"username": username, "password": password, "issue_refresh": True},
        )
    except httpx.HTTPError as exc:
        raise AuthError(f"Can't reach {server}: {exc}") from exc
    if response.status_code != 200:
        raise AuthError(f"Signing in to {server} failed: {error_text(response)}")
    body = response.json()
    refresh = body.get("refresh_token") if isinstance(body, dict) else None
    if not isinstance(refresh, str) or not refresh:
        # Only servers with accounts or OIDC sign-in keep refresh grants.
        raise AuthError(f"{server} signed you in but gave no refresh token to stay signed in.")
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    user_id = user.get("id")
    return Credentials(
        server=server,
        refresh_token=refresh,
        username=user_id if isinstance(user_id, str) else username,
    )


async def revoke(client: httpx.AsyncClient, creds: Credentials) -> None:
    """Make the server forget the refresh token."""
    response = await client.post(
        f"{creds.server}/oauth/revoke", data={"refresh_token": creds.refresh_token}
    )
    if response.status_code >= 400:
        raise AuthError(f"Signing out of {creds.server} failed: {error_text(response)}")


class TokenSource:
    """Hands out an access token, renewing it from the refresh token."""

    def __init__(self, client: httpx.AsyncClient, creds: Credentials) -> None:
        self._client = client
        self._creds = creds
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def server(self) -> str:
        return self._creds.server

    async def token(self, *, renew: bool = False) -> str:
        async with self._lock:
            if renew or self._token is None or time.time() >= self._expires_at:
                await self._renew()
            assert self._token is not None
            return self._token

    async def _renew(self) -> None:
        try:
            response = await self._client.post(
                f"{self.server}/oauth/token",
                data={"grant_type": "refresh_token", "refresh_token": self._creds.refresh_token},
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"Can't reach {self.server}: {exc}") from exc
        if response.status_code != 200:
            reason = error_text(response)
            if reason in ("invalid_grant", "expired_token"):
                raise AuthError(
                    f"{self.server} no longer accepts this machine's sign-in ({reason}): "
                    "it ran out or was revoked. Run `omnigent-mcp login` on the machine again."
                )
            raise AuthError(f"Renewing the Omnigent sign-in failed: {reason}")
        body = response.json()
        self._token = body["access_token"]
        expires_in = float(body.get("expires_in") or 3600)
        self._expires_at = time.time() + max(expires_in - _RENEW_EARLY_SECONDS, 30)
