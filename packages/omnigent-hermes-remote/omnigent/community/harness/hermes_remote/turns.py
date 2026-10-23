"""One Omnigent turn as one Hermes run: what to send, and what came back.

``follow_run`` reads a run's events and yields plain updates (text, tool
calls, the outcome) that executor.py turns into Omnigent's events. Like
client.py, it imports nothing from Omnigent.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from .client import HermesError, HermesRunsClient

ApprovalChoice = Literal["once", "deny"]


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
    client: HermesRunsClient, run_id: str, *, approvals: ApprovalChoice = "deny"
) -> AsyncIterator[Update]:
    """Yield a run's updates, ending with exactly one Finished, Failed or Cancelled.

    Nobody watches an Omnigent chat for Hermes's approval prompts, so a
    pending approval is answered with *approvals* straight away and the
    chat says so, rather than leaving the run waiting until Hermes times out.
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
                yield Text(await _answer_approval(client, run_id, event, approvals))
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


async def _answer_approval(
    client: HermesRunsClient, run_id: str, event: dict[str, Any], choice: ApprovalChoice
) -> str:
    """Answer a pending approval and return the line the chat shows about it.

    With a session authority (Hermes's default) the answer must name the
    pending prompt and the run's execution generation, which the run's
    status lists; without one, the choice alone resolves it.
    """
    command = event.get("command") or event.get("description") or "a command"
    request_id = event.get("request_id") if isinstance(event.get("request_id"), str) else None
    generation = event.get("execution_generation")
    try:
        status = await client.status(run_id)
        pending = [p for p in status.get("pending_controls") or [] if p.get("kind") == "approval"]
        if pending and isinstance(pending[0].get("prompt_id"), str):
            request_id = pending[0]["prompt_id"]
        if isinstance(status.get("execution_generation"), int):
            generation = status["execution_generation"]
        await client.answer_approval(
            run_id,
            choice=choice,
            request_id=request_id,
            execution_generation=generation if isinstance(generation, int) else None,
        )
    except HermesError as exc:
        return f"\n\n> Hermes asked to run `{command}` and the approval couldn't be answered: {exc}\n\n"
    if choice == "once":
        return f"\n\n> Hermes asked to run `{command}`; approved automatically.\n\n"
    return (
        f"\n\n> Hermes asked to run `{command}`; denied automatically, because approvals "
        "can't be answered from Omnigent. Run it from the Hermes desktop app or terminal "
        "if it's needed.\n\n"
    )
