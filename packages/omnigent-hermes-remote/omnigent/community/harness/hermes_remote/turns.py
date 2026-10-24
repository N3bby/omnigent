"""One Omnigent turn as one Hermes run: what to send, and what came back.

``follow_run`` reads a run's events and yields plain updates (text, tool
calls, the outcome) that executor.py turns into Omnigent's events. Like
client.py, it imports nothing from Omnigent.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .client import HermesError, HermesRunsClient

# How often to check that Hermes still waits for an answer while it's asked.
APPROVAL_POLL_SECONDS = 3.0


@dataclass(frozen=True)
class ApprovalPrompt:
    """A command Hermes wants approved, and the answers it accepts."""

    command: str
    description: str
    choices: tuple[str, ...]


# Asks the user and returns one of the prompt's choices, or None when they
# dismissed it. It's cancelled once Hermes stops waiting for the answer.
Ask = Callable[[ApprovalPrompt], Awaitable[str | None]]


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    call_id: str
    name: str
    preview: str


@dataclass(frozen=True)
class ToolFinished:
    call_id: str
    name: str
    failed: bool
    preview: str
    duration_s: float | None


@dataclass(frozen=True)
class Finished:
    output: str


@dataclass(frozen=True)
class Failed:
    message: str


@dataclass(frozen=True)
class Cancelled:
    pass


Update = Text | ToolStarted | ToolFinished | Finished | Failed | Cancelled


def run_input(content: Any) -> str | list[dict[str, Any]] | None:
    """The ``input`` for a run from an Omnigent user message's content.

    Text goes as a string. A message with images goes as one chat message
    whose parts Hermes's agent understands; other attachments, which Hermes's
    API doesn't take, are named in the text instead. ``None`` means there is
    nothing to send.
    """
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts: list[dict[str, Any]] = []
    has_image = False
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if isinstance(block.get("text"), str):
            parts.append({"type": "text", "text": block["text"]})
        elif kind == "input_image" and isinstance(block.get("image_url"), str):
            parts.append({"type": "image_url", "image_url": {"url": block["image_url"]}})
            has_image = True
        elif kind in ("input_file", "input_audio"):
            name = block.get("filename") or kind.removeprefix("input_")
            parts.append({"type": "text", "text": f"[Attachment not sent to Hermes: {name}]"})
    if not parts:
        return None
    if has_image:
        return [{"role": "user", "content": parts}]
    return "\n".join(part["text"] for part in parts)


def last_user_content(messages: list[dict[str, Any]]) -> Any:
    """The newest user message's content: Hermes keeps the rest of the history."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content")
    return None


async def follow_run(
    client: HermesRunsClient,
    run_id: str,
    *,
    ask: Ask | None = None,
    poll_seconds: float = APPROVAL_POLL_SECONDS,
) -> AsyncIterator[Update]:
    """Yield a run's updates, ending with exactly one Finished, Failed or Cancelled.

    A command Hermes wants approved is put to the user with *ask*. Without
    one, it's denied straight away and the chat says so, rather than leaving
    the run waiting until Hermes times out.
    """
    streamed: list[str] = []
    try:
        async for event in client.events(run_id):
            name = event.get("event")
            if name == "message.delta":
                delta = event.get("delta")
                # Hermes starts its answer after a tool call with blank lines.
                if isinstance(delta, str) and (delta := delta if streamed else delta.lstrip()):
                    streamed.append(delta)
                    yield Text(delta)
            elif name == "message.interim":
                text = event.get("text")
                if isinstance(text, str) and text.strip() and not event.get("already_streamed"):
                    streamed.append(text)
                    yield Text(text.strip() + "\n\n")
            elif name == "tool.started":
                yield ToolStarted(
                    call_id=_call_id(event),
                    name=str(event.get("tool") or "tool"),
                    preview=str(event.get("preview") or ""),
                )
            elif name in ("tool.completed", "tool.failed"):
                duration = event.get("duration")
                yield ToolFinished(
                    call_id=_call_id(event),
                    name=str(event.get("tool") or "tool"),
                    failed=name == "tool.failed" or bool(event.get("error")),
                    preview=str(event.get("preview") or ""),
                    duration_s=float(duration) if isinstance(duration, (int, float)) else None,
                )
            elif name == "approval.request":
                if notice := await _approval(client, run_id, event, ask, poll_seconds):
                    yield Text(notice)
            elif name == "run.completed":
                output = event.get("output") if isinstance(event.get("output"), str) else ""
                if output and not streamed:
                    yield Text(output)
                yield Finished(output)
                return
            elif name == "run.cancelled":
                yield Cancelled()
                return
            elif name == "run.failed":
                yield Failed(f"Hermes couldn't finish: {event.get('error') or 'the run failed'}")
                return
            elif name == "run.interrupted":
                yield Failed("Hermes restarted while working on this. Send your message again.")
                return
        # The stream closed without a terminal event: ask how the run ended.
        status = await client.status(run_id)
    except HermesError as exc:
        yield Failed(str(exc))
        return
    state = status.get("status")
    output = status.get("output") if isinstance(status.get("output"), str) else ""
    if state == "completed":
        if output and not streamed:
            yield Text(output)
        yield Finished(output)
    elif state == "cancelled":
        yield Cancelled()
    else:
        yield Failed(f"Lost track of Hermes's run {run_id} (status: {state or 'unknown'}).")


def _call_id(event: dict[str, Any]) -> str:
    call_id = event.get("tool_call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return f"hermes_{event.get('run_id', 'run')}_{event.get('seq', 0)}"


async def _approval(
    client: HermesRunsClient,
    run_id: str,
    event: dict[str, Any],
    ask: Ask | None,
    poll_seconds: float,
) -> str:
    """Get a pending approval answered; return what the chat should say about it, if anything.

    With a session authority (Hermes's default) the answer must name the
    pending prompt and the run's execution generation, which the run's
    status lists; without one, the choice alone resolves it.
    """
    command = str(event.get("command") or event.get("description") or "a command")
    choices = tuple(c for c in event.get("choices") or () if isinstance(c, str)) or ("once", "deny")
    request_id = event.get("request_id") if isinstance(event.get("request_id"), str) else None
    generation = event.get("execution_generation")
    try:
        status = await client.status(run_id)
        pending = [p for p in status.get("pending_controls") or [] if p.get("kind") == "approval"]
        if pending and isinstance(pending[0].get("prompt_id"), str):
            request_id = pending[0]["prompt_id"]
        if isinstance(status.get("execution_generation"), int):
            generation = status["execution_generation"]
    except HermesError as exc:
        return f"\n\n> Hermes asked to run `{command}` and the approval couldn't be answered: {exc}\n\n"
    if ask is None:
        choice: str | None = "deny"
    else:
        prompt = ApprovalPrompt(command, str(event.get("description") or ""), choices)
        choice = await _ask_while_pending(client, run_id, request_id, ask(prompt), poll_seconds)
        if choice is None:
            return f"\n\n> Hermes stopped waiting for an answer about `{command}`.\n\n"
    try:
        await client.answer_approval(
            run_id,
            choice=choice,
            request_id=request_id,
            execution_generation=generation if isinstance(generation, int) else None,
        )
    except HermesError as exc:
        return f"\n\n> Hermes asked to run `{command}` and the approval couldn't be answered: {exc}\n\n"
    if ask is None:
        return (
            f"\n\n> Hermes asked to run `{command}`; denied automatically. Run it from the "
            "Hermes desktop app or terminal if it's needed.\n\n"
        )
    # The card shows the answer.
    return ""


async def _ask_while_pending(
    client: HermesRunsClient,
    run_id: str,
    request_id: str | None,
    asking: Awaitable[str | None],
    poll_seconds: float,
) -> str | None:
    """The user's choice, ``deny`` if they dismissed it, or None once Hermes stopped waiting.

    Hermes gives up on a prompt after its own timeout, and its desktop app
    or terminal can answer it too, so the question is withdrawn as soon as
    the run no longer lists it.
    """
    task = asyncio.ensure_future(asking)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                # Cancelled means the turn is stopping; Stop also stops the run.
                if task.cancelled() or task.exception() is not None:
                    return "deny"
                return task.result() or "deny"
            if not await _still_waiting(client, run_id, request_id):
                return None
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _still_waiting(client: HermesRunsClient, run_id: str, request_id: str | None) -> bool:
    try:
        status = await client.status(run_id)
    except HermesError:
        # Keep asking: a lost connection also ends the run's event stream.
        return True
    if request_id is None:
        return status.get("status") == "waiting_for_approval"
    return any(
        p.get("prompt_id") == request_id
        for p in status.get("pending_controls") or []
        if isinstance(p, dict)
    )
