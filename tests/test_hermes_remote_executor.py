"""The hermes-remote harness's executor and its registration with Omnigent.

Needs Omnigent installed, so CI, which runs without it, skips these; run them
with `uv run --with omnigent==0.18.0 --with httpx python -m unittest
tests.test_hermes_remote_executor`. turns.py, client.py and connection.py have
their own tests that need no Omnigent.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "omnigent-hermes-remote"
HAS_OMNIGENT = importlib.util.find_spec("omnigent") is not None


def load_package():
    """Import the harness as omnigent.community.harness.hermes_remote, as installed."""
    import omnigent.community.harness as community_harness

    path = str(PACKAGE / "omnigent" / "community" / "harness")
    if path not in community_harness.__path__:
        community_harness.__path__.append(path)
    from omnigent.community.harness.hermes_remote import executor, plugin, turns

    return executor, plugin, turns


@unittest.skipUnless(HAS_OMNIGENT, "needs Omnigent installed")
class PluginTest(unittest.TestCase):
    def test_omnigent_accepts_the_contribution(self) -> None:
        from omnigent import harness_plugins

        _, plugin, _ = load_package()
        contribution = plugin.get_contribution()

        error = harness_plugins._validate_community_contribution(
            contribution,
            entry_point_name="hermes-remote",
            existing=(harness_plugins._BUILTIN_CONTRIBUTION,),
        )

        self.assertIsNone(error)
        self.assertEqual(contribution.harness_labels, {"hermes-remote": "Hermes (remote)"})
        # No install spec, so a host with the package reports it configured.
        self.assertEqual(contribution.install_specs, {})


@unittest.skipUnless(HAS_OMNIGENT, "needs Omnigent installed")
class ExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.executor_module, _, self.turns = load_package()

    def run_turn(self, updates: list, environ: dict | None = None, messages: list | None = None):
        environ = {"HERMES_REMOTE_SSH_HOST": "home-hermes"} if environ is None else environ
        executor = self.executor_module.HermesRemoteExecutor(
            conversation_id="conv1", environ=environ
        )
        client = mock.AsyncMock()
        client.start_run.return_value = "run_1"
        if executor._connection is not None:
            executor._connection.client = mock.AsyncMock(return_value=client)

        async def follow(_client, _run_id, **_kwargs):
            for update in updates:
                yield update

        async def go() -> list:
            with mock.patch.object(self.turns, "follow_run", follow):
                return [
                    event
                    async for event in executor.run_turn(
                        messages or [{"role": "user", "content": "hi"}], [], ""
                    )
                ]

        return asyncio.run(go()), client

    def test_a_turn_maps_hermes_updates_to_omnigent_events(self) -> None:
        from omnigent.inner.executor import (
            TextChunk,
            ToolCallComplete,
            ToolCallRequest,
            ToolCallStatus,
            TurnComplete,
        )

        t = self.turns
        events, client = self.run_turn(
            [
                t.ToolStarted("c1", "terminal", "hostname"),
                t.ToolFinished("c1", "terminal", True, "", 0.25),
                t.Text("Done."),
                t.Finished("Done."),
            ]
        )

        client.start_run.assert_awaited_once_with("omnigent-conv1", "hi")
        request, complete, text, done = events
        self.assertIsInstance(request, ToolCallRequest)
        self.assertEqual(request.args, {"preview": "hostname"})
        # Observed, so Omnigent shows the card but never runs the tool itself.
        self.assertEqual(request.metadata, {"call_id": "c1", "internally_executed": True})
        self.assertIsInstance(complete, ToolCallComplete)
        self.assertEqual(complete.status, ToolCallStatus.ERROR)
        self.assertEqual(complete.duration_ms, 250.0)
        self.assertEqual(text, TextChunk(text="Done."))
        self.assertEqual(done, TurnComplete(response="Done."))

    def test_cancelled_and_failed_runs(self) -> None:
        from omnigent.inner.executor import ExecutorError, TurnCancelled

        events, _ = self.run_turn([self.turns.Cancelled()])
        self.assertIsInstance(events[0], TurnCancelled)

        events, _ = self.run_turn([self.turns.Failed("Hermes couldn't finish: x")])
        self.assertIsInstance(events[0], ExecutorError)
        self.assertTrue(events[0].preserve_session)

    def test_missing_settings_fail_the_turn_with_what_to_set(self) -> None:
        from omnigent.inner.executor import ExecutorError

        events, client = self.run_turn([], environ={})

        [error] = events
        self.assertIsInstance(error, ExecutorError)
        self.assertIn("HERMES_REMOTE_SSH_HOST", error.message)
        client.start_run.assert_not_awaited()

    def test_interrupt_stops_the_running_run(self) -> None:
        executor = self.executor_module.HermesRemoteExecutor(
            conversation_id="conv1", environ={"HERMES_REMOTE_SSH_HOST": "home-hermes"}
        )
        client = mock.AsyncMock()
        executor._run = (client, "run_7")

        self.assertTrue(asyncio.run(executor.interrupt_session("any")))
        client.stop.assert_awaited_once_with("run_7")

        executor._run = None
        self.assertFalse(asyncio.run(executor.interrupt_session("any")))


@unittest.skipUnless(HAS_OMNIGENT, "needs Omnigent installed")
class ApprovalCardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.executor_module, _, self.turns = load_package()
        self.ctx = mock.Mock()
        self.executor = self.executor_module.HermesRemoteExecutor(
            conversation_id="conv1",
            environ={"HERMES_REMOTE_SSH_HOST": "home-hermes"},
            turn_context=lambda: self.ctx,
        )
        self.prompt = self.turns.ApprovalPrompt(
            # As Hermes sometimes lists them.
            "rm -rf build", "recursive delete", ("once", "deny", "session", "always")
        )

    def test_the_card_offers_hermes_choices_and_returns_the_one_picked(self) -> None:
        from omnigent.server.schemas import ElicitationResult

        self.ctx.elicit = mock.AsyncMock(
            return_value=ElicitationResult(
                action="accept", content={"answer": "Allow for this session"}
            )
        )

        self.assertEqual(asyncio.run(self.executor._ask(self.prompt)), "session")

        _, params = self.ctx.elicit.await_args.args
        self.assertEqual(params.message, "Hermes wants to run: rm -rf build (recursive delete)")
        self.assertEqual(params.content_preview, "rm -rf build")
        self.assertEqual(
            params.requestedSchema["properties"]["answer"]["enum"],
            ["Allow once", "Allow for this session", "Always allow", "Deny"],
        )
        self.ctx.emit.assert_not_called()

    def test_a_dismissed_card_is_no_answer(self) -> None:
        from omnigent.server.schemas import ElicitationResult

        self.ctx.elicit = mock.AsyncMock(return_value=ElicitationResult(action="decline"))

        self.assertIsNone(asyncio.run(self.executor._ask(self.prompt)))

    def test_a_withdrawn_question_takes_the_card_down(self) -> None:
        from omnigent.server.schemas import ElicitationResolvedEvent

        async def never_answered(*_args):
            await asyncio.Event().wait()

        self.ctx.elicit = never_answered

        async def go() -> None:
            task = asyncio.ensure_future(self.executor._ask(self.prompt))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(go())

        [event] = [call.args[0] for call in self.ctx.emit.call_args_list]
        self.assertIsInstance(event, ElicitationResolvedEvent)
        self.assertEqual(event.reason, "unanswered")

    def test_the_harness_lends_the_executor_the_running_turn(self) -> None:
        from omnigent.community.harness.hermes_remote import harness

        adapter = harness.HermesAdapter()
        adapter.app = adapter.build()
        adapter.app.state.conversation_id = "conv1"
        executor = adapter._build_executor()
        ctx = object()

        async def go() -> object:
            seen = []

            async def run_turn(_self, _request, _ctx):
                seen.append(executor._turn_context())

            with mock.patch.object(harness.ExecutorAdapter, "run_turn", run_turn):
                await adapter.run_turn(mock.Mock(), ctx)
            return seen[0]

        self.assertIs(asyncio.run(go()), ctx)
        self.assertIsNone(executor._turn_context())


if __name__ == "__main__":
    unittest.main()
