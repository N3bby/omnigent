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

        async def follow(_client, _run_id):
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


if __name__ == "__main__":
    unittest.main()
