"""The ``hermes-remote`` harness's entry point: Omnigent's runner calls ``create_app()``."""
from __future__ import annotations

from fastapi import FastAPI
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter
from omnigent.runtime.harnesses._scaffold import TurnContext
from omnigent.server.schemas import CreateResponseRequest

from .executor import HermesRemoteExecutor


class HermesAdapter(ExecutorAdapter):
    """Omnigent's executor adapter, which also lends the executor each turn's context.

    The executor needs it to show Hermes's approval prompts and take them
    down again when Hermes stops waiting, which the adapter's own approval
    bridge can't do.
    """

    def __init__(self) -> None:
        super().__init__(executor_factory=self._build_executor, harness_label="Hermes")
        self.app: FastAPI | None = None
        self._hermes_turn: TurnContext | None = None

    def _build_executor(self) -> HermesRemoteExecutor:
        # The runner sets app.state.conversation_id after create_app()
        # returns, and the adapter builds the executor on the first turn, so
        # it's read here. It's the only reliable source: the adapter's own
        # session key is random, and a zygote-forked harness has no
        # --conversation-id in sys.argv.
        assert self.app is not None
        return HermesRemoteExecutor(
            conversation_id=self.app.state.conversation_id,
            turn_context=lambda: self._hermes_turn,
        )

    async def run_turn(self, request: CreateResponseRequest, ctx: TurnContext) -> None:
        self._hermes_turn = ctx
        try:
            await super().run_turn(request, ctx)
        finally:
            if self._hermes_turn is ctx:
                self._hermes_turn = None


def create_app() -> FastAPI:
    adapter = HermesAdapter()
    adapter.app = adapter.build()
    return adapter.app
