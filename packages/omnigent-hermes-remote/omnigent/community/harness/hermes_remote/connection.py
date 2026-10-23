"""How the harness reaches Hermes: an SSH tunnel to its API server.

The harness needs only an SSH host, user and key. It reads the API server's
port and key from the env file Hermes's gateway uses on that machine (by
default ``~/.config/hermes/api-server.env``), then forwards a local Unix socket
to the API server, which stays bound to 127.0.0.1 there.

On the Hermes machine the key can be limited to exactly that, in
``authorized_keys``::

    restrict,port-forwarding,permitopen="127.0.0.1:8642",command="cat ~/.config/hermes/api-server.env" ssh-ed25519 AAAA...

Like client.py, this imports nothing from Omnigent.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from .client import HermesConfig, HermesError, HermesRunsClient, parse_env

DEFAULT_REMOTE_ENV_FILE = "~/.config/hermes/api-server.env"
DEFAULT_API_PORT = 8642

# These go on ssh's command line, so they must not look like options.
_HOST = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.:-]*")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_TUNNEL_READY_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Settings:
    """Where Hermes is, from the harness's environment.

    ``HERMES_REMOTE_SSH_HOST`` is required; ``HERMES_REMOTE_SSH_USER``,
    ``HERMES_REMOTE_SSH_PORT`` and ``HERMES_REMOTE_SSH_KEY`` (a private key's
    text) fall back to SSH's own defaults. ``HERMES_REMOTE_ENV_FILE`` is the
    gateway's env file on the Hermes machine.
    """

    host: str
    user: str | None = None
    port: int | None = None
    private_key: str | None = None
    remote_env_file: str = DEFAULT_REMOTE_ENV_FILE

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        host = (env.get("HERMES_REMOTE_SSH_HOST") or "").strip()
        if not host:
            raise HermesError(
                "Hermes isn't configured for this Omnigent host: set HERMES_REMOTE_SSH_HOST "
                "(and HERMES_REMOTE_SSH_USER and HERMES_REMOTE_SSH_KEY as needed)."
            )
        if not _HOST.fullmatch(host):
            raise HermesError(f"HERMES_REMOTE_SSH_HOST isn't a host name: {host!r}")
        user = (env.get("HERMES_REMOTE_SSH_USER") or "").strip() or None
        if user is not None and not _USER.fullmatch(user):
            raise HermesError(f"HERMES_REMOTE_SSH_USER isn't a user name: {user!r}")
        raw_port = (env.get("HERMES_REMOTE_SSH_PORT") or "").strip()
        if raw_port and not (raw_port.isdigit() and 0 < int(raw_port) < 65536):
            raise HermesError(f"HERMES_REMOTE_SSH_PORT isn't a port: {raw_port!r}")
        key = (env.get("HERMES_REMOTE_SSH_KEY") or "").strip()
        return cls(
            host=host,
            user=user,
            port=int(raw_port) if raw_port else None,
            private_key=key + "\n" if key else None,
            remote_env_file=(env.get("HERMES_REMOTE_ENV_FILE") or "").strip()
            or DEFAULT_REMOTE_ENV_FILE,
        )

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


def ssh_command(
    settings: Settings, key_file: Path | None, *options: str, remote_command: str | None = None
) -> list[str]:
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        # The tunnel runs over the tailnet, which already authenticates the
        # machine; the first connection from a new sandbox records its key.
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
    ]
    if settings.port is not None:
        command += ["-p", str(settings.port)]
    if key_file is not None:
        command += ["-i", str(key_file), "-o", "IdentitiesOnly=yes"]
    command += [*options, settings.target]
    return [*command, remote_command] if remote_command is not None else command


def read_env_file_command(path: str) -> str:
    """The remote command that prints *path*; a leading ``~/`` stays unquoted so it expands."""
    if path.startswith("~/"):
        return "cat ~/" + shlex.quote(path[2:])
    return "cat " + shlex.quote(path)


class HermesConnection:
    """Hands out a client for Hermes, (re)opening the SSH tunnel when needed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: HermesRunsClient | None = None
        self._tunnel: asyncio.subprocess.Process | None = None
        # Holds the tunnel's socket and the private key, readable only by us.
        self._private_dir: tempfile.TemporaryDirectory[str] | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> HermesRunsClient:
        async with self._lock:
            if self._client is not None and self._tunnel is not None and self._tunnel.returncode is None:
                return self._client
            await self._close_unlocked()
            try:
                self._client = await self._open()
            except BaseException:
                await self._close_unlocked()
                raise
            return self._client

    async def aclose(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def _open(self) -> HermesRunsClient:
        settings = self._settings
        self._private_dir = tempfile.TemporaryDirectory(prefix="hermes-remote-")
        private = Path(self._private_dir.name)
        os.chmod(private, 0o700)
        key_file: Path | None = None
        if settings.private_key is not None:
            key_file = private / "id"
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(settings.private_key)

        remote = parse_env(
            await _ssh_output(
                ssh_command(
                    settings, key_file, remote_command=read_env_file_command(settings.remote_env_file)
                ),
                settings.target,
            )
        )
        api_key = remote.get("API_SERVER_KEY", "").strip()
        if not api_key:
            raise HermesError(
                f"{settings.target}:{settings.remote_env_file} has no API_SERVER_KEY. "
                "Turn on Hermes's API server there."
            )
        host = remote.get("API_SERVER_HOST", "").strip() or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = remote.get("API_SERVER_PORT", "").strip() or str(DEFAULT_API_PORT)
        if not port.isdigit():
            raise HermesError(f"API_SERVER_PORT on {settings.target} isn't a port: {port!r}")

        socket_path = private / "api.sock"
        self._tunnel = await asyncio.create_subprocess_exec(
            *ssh_command(
                settings,
                key_file,
                "-N",
                "-o", "ExitOnForwardFailure=yes",
                "-L", f"{socket_path}:{host}:{port}",
            ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._wait_for_socket(socket_path)
        return HermesRunsClient(
            HermesConfig(url="http://hermes", api_key=api_key, where=f"{settings.target} (over SSH)"),
            transport=httpx.AsyncHTTPTransport(uds=str(socket_path)),
        )

    async def _wait_for_socket(self, socket_path: Path) -> None:
        assert self._tunnel is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TUNNEL_READY_TIMEOUT_S
        while not socket_path.exists():
            if self._tunnel.returncode is not None:
                stderr = await self._tunnel.stderr.read() if self._tunnel.stderr else b""
                raise HermesError(f"SSH to {self._settings.target} failed: {_last_line(stderr)}")
            if loop.time() > deadline:
                raise HermesError(
                    f"SSH to {self._settings.target} didn't open a tunnel within "
                    f"{_TUNNEL_READY_TIMEOUT_S:.0f} s."
                )
            await asyncio.sleep(0.05)

    async def _close_unlocked(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()
            self._client = None
        if self._tunnel is not None:
            if self._tunnel.returncode is None:
                self._tunnel.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._tunnel.wait(), 5)
                if self._tunnel.returncode is None:
                    self._tunnel.kill()
                    await self._tunnel.wait()
            self._tunnel = None
        if self._private_dir is not None:
            self._private_dir.cleanup()
            self._private_dir = None


async def _ssh_output(command: list[str], target: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HermesError(f"SSH to {target} timed out.") from exc
    if process.returncode != 0:
        raise HermesError(f"SSH to {target} failed: {_last_line(stderr)}")
    return stdout.decode("utf-8", "replace")


def _last_line(stderr: bytes) -> str:
    lines = [line for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1] if lines else "no error message"
