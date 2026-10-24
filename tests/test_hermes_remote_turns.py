"""The hermes-remote harness's turns: what it sends Hermes and what it makes of a run.

Tests packages/omnigent-hermes-remote's turns.py against a fake Hermes; it
imports nothing from Omnigent.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(
    0, str(ROOT / "packages" / "omnigent-hermes-remote" / "omnigent" / "community" / "harness")
)
from hermes_remote import client, turns  # noqa: E402


def sse(*events: dict) -> bytes:
    frames = [": open\n\n"]
    frames += [f"id: {seq}\ndata: {json.dumps(event)}\n\n" for seq, event in enumerate(events)]
    return "".join(frames + [": stream closed\n\n"]).encode()


class FakeRun:
    """One Hermes run: its events, its status, and the answers it got."""

    def __init__(self, *events: dict, status: dict | list[dict] | None = None) -> None:
        self.events = events
        # A list is the status for each check in turn, the last one repeating.
        self.statuses = status if isinstance(status, list) else [status or {"status": "running"}]
        self.answers: list[dict] = []
        self.prompts: list[turns.ApprovalPrompt] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/runs/run_1/events":
            return httpx.Response(200, content=sse(*self.events))
        if path == "/v1/runs/run_1" and request.method == "GET":
            status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            return httpx.Response(200, json=status)
        if path == "/v1/runs/run_1/approval":
            self.answers.append(json.loads(request.content))
            return httpx.Response(200, json={"resolved": 1})
        return httpx.Response(404, json={"error": {"message": "Run not found"}})

    def follow(self, answer: object = ...) -> list[turns.Update]:
        """Follow the run; *answer* is the user's answer to approvals, ``...`` for never."""
        runs = client.HermesRunsClient(
            client.HermesConfig(url="http://hermes", api_key="key"),
            transport=httpx.MockTransport(self.handle),
        )

        async def ask(prompt: turns.ApprovalPrompt) -> str | None:
            self.prompts.append(prompt)
            if answer is ...:
                await asyncio.Event().wait()
            return answer  # type: ignore[return-value]

        async def go() -> list[turns.Update]:
            return [
                u async for u in turns.follow_run(runs, "run_1", ask=ask, poll_seconds=0.01)
            ]

        return asyncio.run(go())


class FollowRunTest(unittest.TestCase):
    def test_text_and_tool_calls_stream_and_the_run_finishes(self) -> None:
        run = FakeRun(
            {"event": "tool.started", "tool": "terminal", "preview": "hostname", "tool_call_id": "t1"},
            {"event": "tool.completed", "tool": "terminal", "duration": 0.5, "error": False,
             "tool_call_id": "t1", "preview": "home-hermes"},
            {"event": "message.delta", "delta": "\n\n"},
            {"event": "message.delta", "delta": "It's "},
            {"event": "message.delta", "delta": "home-hermes."},
            {"event": "run.completed", "output": "It's home-hermes."},
        )

        self.assertEqual(
            run.follow(),
            [
                turns.ToolStarted("t1", "terminal", "hostname"),
                turns.ToolFinished("t1", "terminal", False, "home-hermes", 0.5),
                turns.Text("It's "),
                turns.Text("home-hermes."),
                turns.Finished("It's home-hermes."),
            ],
        )

    def test_blank_lines_before_the_answer_are_dropped_but_not_inside_it(self) -> None:
        run = FakeRun(
            {"event": "message.delta", "delta": "\n\nFirst"},
            {"event": "message.delta", "delta": "\n\nSecond"},
            {"event": "run.completed", "output": "First\n\nSecond"},
        )

        self.assertEqual(
            run.follow()[:2], [turns.Text("First"), turns.Text("\n\nSecond")]
        )

    def test_a_failed_tool_is_marked_failed(self) -> None:
        run = FakeRun(
            {"event": "tool.failed", "tool": "terminal", "tool_call_id": "t1"},
            {"event": "run.completed", "output": "Blocked."},
        )

        self.assertTrue(run.follow()[0].failed)

    def test_an_answer_that_wasnt_streamed_is_sent_whole(self) -> None:
        run = FakeRun({"event": "run.completed", "output": "Done."})

        self.assertEqual(run.follow(), [turns.Text("Done."), turns.Finished("Done.")])

    def test_commentary_already_streamed_isnt_repeated(self) -> None:
        run = FakeRun(
            {"event": "message.interim", "text": "Looking.", "already_streamed": True},
            {"event": "message.interim", "text": "Checking the disk.", "already_streamed": False},
            {"event": "run.completed", "output": ""},
        )

        self.assertEqual(
            run.follow(), [turns.Text("Checking the disk.\n\n"), turns.Finished("")]
        )

    def test_terminal_events_end_the_turn(self) -> None:
        for event, expected in (
            ({"event": "run.cancelled"}, turns.Cancelled()),
            ({"event": "run.failed", "error": "max turns"},
             turns.Failed("Hermes couldn't finish: max turns")),
            ({"event": "run.interrupted"},
             turns.Failed("Hermes restarted while working on this. Send your message again.")),
        ):
            with self.subTest(event=event["event"]):
                self.assertEqual(FakeRun(event).follow(), [expected])

    def test_a_stream_that_closes_early_asks_how_the_run_ended(self) -> None:
        run = FakeRun(status={"status": "completed", "output": "All done."})

        self.assertEqual(run.follow(), [turns.Text("All done."), turns.Finished("All done.")])

    def test_a_run_that_cant_be_found_fails_the_turn(self) -> None:
        run = FakeRun(status={"status": "unknown"})

        [update] = run.follow()

        self.assertIsInstance(update, turns.Failed)
        self.assertIn("run_1", update.message)

    def test_an_approval_is_put_to_the_user_and_their_answer_sent(self) -> None:
        run = FakeRun(
            {"event": "approval.request", "command": "rm -rf build",
             "description": "recursive delete", "choices": ["once", "session", "deny"]},
            {"event": "run.completed", "output": "Deleted."},
            status={
                "status": "waiting_for_approval",
                "execution_generation": 4,
                "pending_controls": [{"kind": "approval", "prompt_id": "prompt_9"}],
            },
        )

        updates = run.follow("session")

        self.assertEqual(
            run.prompts,
            [turns.ApprovalPrompt("rm -rf build", "recursive delete", ("once", "session", "deny"))],
        )
        self.assertEqual(
            run.answers,
            [{"choice": "session", "request_id": "prompt_9", "execution_generation": 4}],
        )
        # The card shows the answer, so the chat doesn't repeat it.
        self.assertEqual(updates, [turns.Text("Deleted."), turns.Finished("Deleted.")])

    def test_a_dismissed_approval_is_denied(self) -> None:
        run = FakeRun(
            {"event": "approval.request", "command": "make deploy"},
            {"event": "run.completed", "output": "Not deployed."},
        )

        run.follow(None)

        self.assertEqual(run.answers, [{"choice": "deny"}])
        self.assertEqual(run.prompts[0].choices, ("once", "deny"))

    def test_the_question_is_withdrawn_when_hermes_stops_waiting(self) -> None:
        waiting = {
            "status": "waiting_for_approval",
            "pending_controls": [{"kind": "approval", "prompt_id": "prompt_9"}],
        }
        run = FakeRun(
            {"event": "approval.request", "command": "rm -rf build"},
            {"event": "run.completed", "output": "It timed out."},
            # Hermes timed out, or its desktop app answered.
            status=[waiting, waiting, {"status": "running", "pending_controls": []}],
        )

        updates = run.follow()

        self.assertEqual(run.answers, [])
        self.assertIn("stopped waiting for an answer about `rm -rf build`", updates[0].text)
        self.assertEqual(updates[-1], turns.Finished("It timed out."))

    def test_without_anyone_to_ask_an_approval_is_denied_and_said_in_the_chat(self) -> None:
        run = FakeRun(
            {"event": "approval.request", "command": "rm -rf build", "choices": ["once", "deny"]},
            {"event": "run.completed", "output": "It was denied."},
            status={
                "status": "waiting_for_approval",
                "execution_generation": 4,
                "pending_controls": [{"kind": "approval", "prompt_id": "prompt_9"}],
            },
        )
        runs = client.HermesRunsClient(
            client.HermesConfig(url="http://hermes", api_key="key"),
            transport=httpx.MockTransport(run.handle),
        )

        async def go() -> list[turns.Update]:
            return [u async for u in turns.follow_run(runs, "run_1")]

        updates = asyncio.run(go())

        self.assertEqual(
            run.answers, [{"choice": "deny", "request_id": "prompt_9", "execution_generation": 4}]
        )
        self.assertIn("`rm -rf build`; denied automatically", updates[0].text)
        self.assertEqual(updates[-1], turns.Finished("It was denied."))


class RunInputTest(unittest.TestCase):
    def test_text_is_sent_as_a_string(self) -> None:
        self.assertEqual(turns.run_input("Hi"), "Hi")
        self.assertEqual(
            turns.run_input([{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}]),
            "a\nb",
        )

    def test_images_are_sent_as_chat_parts(self) -> None:
        content = [
            {"type": "input_text", "text": "What's this?"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        ]

        self.assertEqual(
            turns.run_input(content),
            [{"role": "user", "content": [
                {"type": "text", "text": "What's this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]}],
        )

    def test_other_attachments_are_named_instead(self) -> None:
        content = [{"type": "input_text", "text": "Read this"},
                   {"type": "input_file", "filename": "notes.pdf"}]

        self.assertEqual(
            turns.run_input(content), "Read this\n[Attachment not sent to Hermes: notes.pdf]"
        )

    def test_nothing_to_send(self) -> None:
        self.assertIsNone(turns.run_input(""))
        self.assertIsNone(turns.run_input(None))

    def test_only_the_newest_user_message_is_sent(self) -> None:
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": ""},
        ]

        self.assertEqual(turns.last_user_content(messages), "second")


if __name__ == "__main__":
    unittest.main()
