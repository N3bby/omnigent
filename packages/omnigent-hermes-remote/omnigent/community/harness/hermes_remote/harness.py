"""The ``hermes-remote`` harness's entry point: Omnigent's runner calls ``create_app()``."""
from __future__ import annotations

from fastapi import FastAPI
from omnigent.runtime.harnesses._executor_adapter import ExecutorAdapter

from .executor import HermesRemoteExecutor


def create_app() -> FastAPI:
    # The runner sets app.state.conversation_id after create_app() returns,
    # and the adapter builds the executor on the first turn, so the factory
    # reads it then. It's the only reliable source: the adapter's own session
    # key is random, and a zygote-forked harness has no --conversation-id in
    # sys.argv.
    app: FastAPI | None = None

    def build_executor() -> HermesRemoteExecutor:
        assert app is not None
        return HermesRemoteExecutor(conversation_id=app.state.conversation_id)

    app = ExecutorAdapter(executor_factory=build_executor, harness_label="Hermes").build()
    return app
