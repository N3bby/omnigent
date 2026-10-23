"""A small client for a Hermes Agent's runs API (``/v1/runs``).

Hermes's API server runs one agent turn per run: ``POST /v1/runs`` starts it
in a Hermes session, ``GET /v1/runs/{id}/events`` streams what it does as
server-sent events, ``POST /v1/runs/{id}/stop`` interrupts it and
``POST /v1/runs/{id}/approval`` answers a command it wants approved.

This module imports nothing from Omnigent, so the repository's tests run it
without Omnigent installed.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

# Hermes's own terminal events. ``run.interrupted`` is a run cut short by a
# gateway restart.
TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.interrupted"})

# Omnigent conversation ids are hex; Hermes session ids end up in file names,
# so keep to characters Hermes accepts everywhere.
_SESSION_PREFIX = "omnigent-"


class HermesError(RuntimeError):
    """Hermes refused a request or couldn't be reached."""


@dataclass(frozen=True)
class HermesConfig:
    """Where a Hermes API server answers and the key it wants."""

    url: str
    api_key: str
    # How errors name Hermes, such as "n3bby@home-hermes (over SSH)".
    where: str = ""

    @property
    def label(self) -> str:
        return self.where or self.url


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, as in an env file, skipping comments."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def session_id_for(conversation_id: str) -> str:
    """The Hermes session that holds an Omnigent conversation's history."""
    safe = "".join(ch for ch in conversation_id if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError(f"Unusable conversation id: {conversation_id!r}")
    return _SESSION_PREFIX + safe


def parse_sse(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Turn server-sent event lines into the JSON objects they carry.

    Comment lines (``: keepalive``), ``id:`` lines and frames whose data isn't
    a JSON object are skipped. A frame ends at a blank line.
    """
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            event = _decode_frame(data)
            data = []
            if event is not None:
                yield event
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    event = _decode_frame(data)
    if event is not None:
        yield event


def _decode_frame(data: list[str]) -> dict[str, Any] | None:
    if not data:
        return None
    try:
        event = json.loads("\n".join(data))
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


class HermesRunsClient:
    """Starts, follows, stops and answers runs on one Hermes API server."""

    def __init__(self, config: HermesConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        # Runs can go quiet for minutes while a tool works; Hermes sends a
        # keepalive every 10 s, so only the connect step needs a short limit.
        self._http = httpx.AsyncClient(
            base_url=config.url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=httpx.Timeout(30.0, read=None),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def start_run(self, session_id: str, text: str) -> str:
        """Start a turn in *session_id* and return its run id.

        Hermes loads the session's history itself, so only the new message
        is sent.
        """
        body = await self._post("/v1/runs", {"input": text, "session_id": session_id})
        run_id = body.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise HermesError(f"Hermes didn't return a run id: {body!r}")
        return run_id

    async def events(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield the run's events until its terminal one."""
        try:
            async with self._http.stream("GET", f"/v1/runs/{run_id}/events") as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise HermesError(_describe_failure(response))
                buffer: list[str] = []
                async for line in response.aiter_lines():
                    buffer.append(line)
                    if line:
                        continue
                    for event in parse_sse(buffer):
                        yield event
                        if event.get("event") in TERMINAL_EVENTS:
                            return
                    buffer = []
                for event in parse_sse(buffer):
                    yield event
        except httpx.HTTPError as exc:
            raise HermesError(f"Lost the connection to Hermes at {self._config.label}: {exc}") from exc

    async def status(self, run_id: str) -> dict[str, Any]:
        try:
            response = await self._http.get(f"/v1/runs/{run_id}")
        except httpx.HTTPError as exc:
            raise HermesError(f"Couldn't reach Hermes at {self._config.label}: {exc}") from exc
        if response.status_code >= 400:
            raise HermesError(_describe_failure(response))
        return response.json()

    async def stop(self, run_id: str) -> None:
        await self._post(f"/v1/runs/{run_id}/stop", {})

    async def answer_approval(
        self, run_id: str, *, choice: str, request_id: str | None, execution_generation: int | None
    ) -> None:
        """Answer the run's pending approval with ``once``, ``session``, ``always`` or ``deny``."""
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        if execution_generation is not None:
            body["execution_generation"] = execution_generation
        await self._post(f"/v1/runs/{run_id}/approval", body)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise HermesError(f"Couldn't reach Hermes at {self._config.label}: {exc}") from exc
        if response.status_code >= 400:
            raise HermesError(_describe_failure(response))
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}


def _describe_failure(response: httpx.Response) -> str:
    """Hermes's own error message when it sent one, else the status line."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict) and error.get("message"):
        return f"Hermes answered {response.status_code}: {error['message']}"
    if response.status_code == 401:
        return "Hermes rejected the API key (401). Check API_SERVER_KEY."
    return f"Hermes answered {response.status_code} {response.reason_phrase}"
