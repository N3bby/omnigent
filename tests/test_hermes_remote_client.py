"""The hermes-remote harness's client for a Hermes Agent's runs API.

Tests packages/omnigent-hermes-remote's client.py on its own, against a fake
Hermes; it imports nothing from Omnigent. The executor is tested in
test_hermes_remote_executor.py.
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
from hermes_remote import client  # noqa: E402


def sse(*events: dict, keepalive: bool = True) -> bytes:
    frames = [": open\n\n"]
    for seq, event in enumerate(events):
        if keepalive:
            frames.append(": keepalive\n\n")
        frames.append(f"id: {seq}\ndata: {json.dumps(event)}\n\n")
    frames.append(": stream closed\n\n")
    return "".join(frames).encode()


class ParseSseTest(unittest.TestCase):
    def test_frames_become_events_and_comments_are_skipped(self) -> None:
        lines = sse({"event": "message.delta", "delta": "Hi"}, {"event": "run.completed"}).decode()

        events = list(client.parse_sse(lines.splitlines(keepends=True)))

        self.assertEqual(
            events, [{"event": "message.delta", "delta": "Hi"}, {"event": "run.completed"}]
        )

    def test_a_frame_that_isnt_a_json_object_is_skipped(self) -> None:
        lines = ["data: not json", "", "data: [1, 2]", "", 'data: {"event": "x"}', ""]

        self.assertEqual(list(client.parse_sse(lines)), [{"event": "x"}])

    def test_multi_line_data_is_joined(self) -> None:
        lines = ['data: {"event":', 'data: "x"}', ""]

        self.assertEqual(list(client.parse_sse(lines)), [{"event": "x"}])


class SessionIdTest(unittest.TestCase):
    def test_a_conversation_maps_to_a_stable_hermes_session(self) -> None:
        self.assertEqual(
            client.session_id_for("0eae2eb9fdf84fc497118dc055eb7afa"),
            "omnigent-0eae2eb9fdf84fc497118dc055eb7afa",
        )

    def test_characters_hermes_cant_use_in_file_names_are_dropped(self) -> None:
        self.assertEqual(client.session_id_for("conv/../x y"), "omnigent-convxy")

    def test_an_empty_id_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            client.session_id_for("../")


class ParseEnvTest(unittest.TestCase):
    def test_values_comments_quotes_and_exports(self) -> None:
        text = "# Hermes API\nAPI_SERVER_PORT=8642\nexport API_SERVER_KEY='k2'\nbroken line\n"

        self.assertEqual(
            client.parse_env(text), {"API_SERVER_PORT": "8642", "API_SERVER_KEY": "k2"}
        )


class FakeHermes:
    """Answers the runs API like Hermes does and records the requests."""

    def __init__(self, events: tuple[dict, ...] = ()) -> None:
        self.events = events
        self.requests: list[tuple[str, str, dict | None]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if request.headers.get("Authorization") != "Bearer key":
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        if request.method == "POST" and request.url.path == "/v1/runs":
            return httpx.Response(202, json={"run_id": "run_1", "status": "started"})
        if request.url.path == "/v1/runs/run_1/events":
            return httpx.Response(200, content=sse(*self.events))
        if request.url.path == "/v1/runs/run_1/stop":
            return httpx.Response(200, json={"status": "stopping"})
        if request.url.path == "/v1/runs/run_1/approval":
            return httpx.Response(200, json={"choice": body["choice"]})
        return httpx.Response(404, json={"error": {"message": "Run not found"}})

    def client(self, key: str = "key") -> client.HermesRunsClient:
        config = client.HermesConfig(url="http://hermes", api_key=key)
        return client.HermesRunsClient(config, transport=httpx.MockTransport(self.handle))


async def collect(runs: client.HermesRunsClient, run_id: str) -> list[dict]:
    return [event async for event in runs.events(run_id)]


class RunsClientTest(unittest.TestCase):
    def test_a_run_sends_only_the_new_message_into_the_session(self) -> None:
        fake = FakeHermes()

        run_id = asyncio.run(fake.client().start_run("omnigent-c1", "What's up?"))

        self.assertEqual(run_id, "run_1")
        self.assertEqual(
            fake.requests, [("POST", "/v1/runs", {"input": "What's up?", "session_id": "omnigent-c1"})]
        )

    def test_events_stop_at_the_terminal_one(self) -> None:
        fake = FakeHermes(
            (
                {"event": "message.delta", "delta": "Hel"},
                {"event": "message.delta", "delta": "lo"},
                {"event": "run.completed", "output": "Hello"},
                {"event": "message.delta", "delta": "never read"},
            )
        )

        events = asyncio.run(collect(fake.client(), "run_1"))

        self.assertEqual([e["event"] for e in events], ["message.delta"] * 2 + ["run.completed"])

    def test_stop_and_approval_go_to_the_run(self) -> None:
        fake = FakeHermes()
        runs = fake.client()

        async def go() -> None:
            await runs.stop("run_1")
            await runs.answer_approval(
                "run_1", choice="deny", request_id="req_1", execution_generation=3
            )

        asyncio.run(go())

        self.assertEqual(
            fake.requests,
            [
                ("POST", "/v1/runs/run_1/stop", {}),
                (
                    "POST",
                    "/v1/runs/run_1/approval",
                    {"choice": "deny", "request_id": "req_1", "execution_generation": 3},
                ),
            ],
        )

    def test_a_refusal_carries_hermess_message(self) -> None:
        fake = FakeHermes()

        with self.assertRaisesRegex(client.HermesError, "401: Invalid API key"):
            asyncio.run(fake.client(key="wrong").start_run("omnigent-c1", "hi"))

    def test_an_unreachable_hermes_names_the_url(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        runs = client.HermesRunsClient(
            client.HermesConfig(url="http://hermes", api_key="key"),
            transport=httpx.MockTransport(refuse),
        )

        with self.assertRaisesRegex(client.HermesError, "http://hermes"):
            asyncio.run(runs.start_run("omnigent-c1", "hi"))

    def test_errors_name_hermes_by_where_it_is(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        config = client.HermesConfig(url="http://hermes", api_key="key", where="n3bby@box (over SSH)")
        runs = client.HermesRunsClient(config, transport=httpx.MockTransport(refuse))

        with self.assertRaisesRegex(client.HermesError, r"n3bby@box \(over SSH\)"):
            asyncio.run(runs.start_run("omnigent-c1", "hi"))


if __name__ == "__main__":
    unittest.main()
