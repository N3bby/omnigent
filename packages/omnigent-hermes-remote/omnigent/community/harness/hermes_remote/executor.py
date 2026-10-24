"""Runs each Omnigent turn as a run on an existing Hermes Agent.

Every Omnigent conversation maps to one Hermes session
(``omnigent-<conversation id>``), so the conversation is part of that Hermes's
history, beside its desktop app, terminal and chat platforms, and shares its
memory, skills and model login. Only the newest user message is sent each
turn: Hermes loads the rest of the conversation from its own history.
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from omnigent.inner.executor import (
    Executor,
    ExecutorConfig,
    ExecutorError,
    ExecutorEvent,
    Message,
    TextChunk,
    ToolCallComplete,
    ToolCallRequest,
    ToolCallStatus,
    ToolSpec,
    TurnCancelled,
    TurnComplete,
)
from omnigent.runtime.harnesses._scaffold import TurnContext
from omnigent.server.schemas import ElicitationRequestParams, ElicitationResolvedEvent

from . import turns
from .client import HermesError, HermesRunsClient, session_id_for
from .connection import HermesConnection, Settings

_logger = logging.getLogger(__name__)

# The buttons for Hermes's approval answers, in the order the card shows them.
CHOICE_LABELS = {
    "once": "Allow once",
    "session": "Allow for this session",
    "always": "Always allow",
    "deny": "Deny",
}


class HermesRemoteExecutor(Executor):
    def __init__(
        self,
        *,
        conversation_id: str,
        environ: Mapping[str, str] | None = None,
        turn_context: Callable[[], TurnContext | None] | None = None,
    ) -> None:
        """
        :param turn_context: Returns the running turn's context, which shows
            Hermes's approval prompts in the chat. Without it they're denied.
        """
        self._session_id = session_id_for(conversation_id)
        self._turn_context = turn_context
        self._connection: HermesConnection | None = None
        # A missing setting fails each turn with how to fix it, rather than
        # the harness process.
        self._settings_error: str | None = None
        try:
            self._connection = HermesConnection(Settings.from_env(environ))
        except HermesError as exc:
            self._settings_error = str(exc)
        self._run: tuple[HermesRunsClient, str] | None = None

    def supports_streaming(self) -> bool:
        return True

    def handles_tools_internally(self) -> bool:
        return True

    async def run_turn(
        self,
        messages: list[Message],
        tools: list[ToolSpec],  # noqa: ARG002 — Hermes uses its own tools
        system_prompt: str,  # noqa: ARG002 — Hermes keeps its own instructions
        config: ExecutorConfig | None = None,  # noqa: ARG002 — Hermes picks its own model
    ) -> AsyncIterator[ExecutorEvent]:
        run_input = turns.run_input(turns.last_user_content(messages))
        if run_input is None:
            yield ExecutorError(message="There's no message to send to Hermes.", preserve_session=True)
            return
        if self._connection is None:
            yield ExecutorError(
                message=self._settings_error or "Hermes isn't configured.",
                title="Hermes isn't set up",
                preserve_session=True,
            )
            return
        try:
            client = await self._connection.client()
            run_id = await client.start_run(self._session_id, run_input)
        except HermesError as exc:
            yield ExecutorError(
                message=str(exc),
                title="Couldn't reach Hermes",
                retryable=True,
                preserve_session=True,
            )
            return
        _logger.info("Hermes run %s started in session %s", run_id, self._session_id)
        self._run = (client, run_id)
        try:
            ask = self._ask if self._turn_context is not None else None
            async for update in turns.follow_run(client, run_id, ask=ask):
                event = _to_executor_event(update)
                if event is not None:
                    yield event
        finally:
            self._run = None

    async def _ask(self, prompt: turns.ApprovalPrompt) -> str | None:
        """Show Hermes's approval prompt as an approval card and return the choice.

        Cancelled when Hermes stops waiting, which takes the card down too.
        """
        ctx = self._turn_context() if self._turn_context is not None else None
        if ctx is None:
            return None
        order = list(CHOICE_LABELS)
        choices = sorted(prompt.choices, key=lambda c: order.index(c) if c in order else len(order))
        labels = {CHOICE_LABELS.get(choice, choice): choice for choice in choices}
        elicitation_id = f"elicit_{secrets.token_hex(16)}"
        params = ElicitationRequestParams(
            mode="form",
            message=_approval_message(prompt),
            # An enum named "answer" is shown as one button per choice.
            requestedSchema={
                "type": "object",
                "properties": {"answer": {"type": "string", "enum": list(labels)}},
                "required": ["answer"],
            },
            phase="tool_call",
            policy_name="hermes_approval",
            content_preview=prompt.command[:300],
        )
        answered = False
        try:
            result = await ctx.elicit(elicitation_id, params)
            answered = True
        finally:
            if not answered:
                # Omnigent leaves an unanswered card up unless it's told.
                ctx.emit(
                    ElicitationResolvedEvent(
                        type="response.elicitation_resolved",
                        elicitation_id=elicitation_id,
                        reason="unanswered",
                    )
                )
        if result.action != "accept":
            return None
        answer = (result.content or {}).get("answer")
        return labels.get(answer) if isinstance(answer, str) else None

    async def interrupt_session(self, session_key: str) -> bool:  # noqa: ARG002
        """Stop the running Hermes run; its stream then ends with ``run.cancelled``."""
        if self._run is None:
            return False
        client, run_id = self._run
        try:
            await client.stop(run_id)
        except HermesError:
            _logger.warning("Couldn't stop Hermes run %s", run_id, exc_info=True)
            return False
        return True

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.aclose()


def _to_executor_event(update: turns.Update) -> ExecutorEvent | None:
    if isinstance(update, turns.Text):
        return TextChunk(text=update.text)
    if isinstance(update, turns.ToolStarted):
        # Observed, not dispatched: Hermes already runs the tool itself.
        return ToolCallRequest(
            name=update.name,
            args=_tool_args(update.preview),
            metadata={"call_id": update.call_id, "internally_executed": True},
        )
    if isinstance(update, turns.ToolFinished):
        return ToolCallComplete(
            name=update.name,
            status=ToolCallStatus.ERROR if update.failed else ToolCallStatus.SUCCESS,
            result=update.preview,
            error="Hermes reported an error" if update.failed else None,
            duration_ms=(update.duration_s or 0.0) * 1000,
            metadata={"call_id": update.call_id},
        )
    if isinstance(update, turns.Finished):
        return TurnComplete(response=update.output or None)
    if isinstance(update, turns.Cancelled):
        return TurnCancelled()
    if isinstance(update, turns.Failed):
        return ExecutorError(message=update.message, title="Hermes", preserve_session=True)
    return None


def _approval_message(prompt: turns.ApprovalPrompt) -> str:
    command = " ".join(prompt.command.split())
    if len(command) > 200:
        command = command[:199] + "…"
    reason = f" ({prompt.description})" if prompt.description else ""
    # The card shows the message as plain text.
    return f"Hermes wants to run: {command}{reason}"


def _tool_args(preview: str) -> dict[str, Any]:
    return {"preview": preview} if preview else {}
