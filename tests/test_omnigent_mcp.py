"""The omnigent-mcp package: signing in, and its tools against a fake Omnigent.

Tests packages/omnigent-mcp without the MCP SDK, which only server.py
imports: the tools' logic lives in tools.py, on top of auth.py and api.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "omnigent-mcp"))
from omnigent_mcp import auth, tools  # noqa: E402
from omnigent_mcp.api import OmnigentApi, OmnigentError  # noqa: E402

SERVER = "https://omni.example.com"
CLAUDE = {"id": "ag-claude", "name": "claude-native-ui", "harness": "claude-native"}
CODEX = {"id": "ag-codex", "name": "codex-native-ui", "harness": "codex-native"}
HERMES = {"id": "ag-hermes", "name": "hermes", "harness": "hermes-remote"}
ROADPASS = {
    "id": "p-road",
    "name": "Roadpass",
    "config": {
        "host_id": "__sandbox__",
        "agent_id": "ag-codex",
        "model": "gpt-5.5",
        "sandbox_repos": [
            {"url": "https://github.com/org/roadpass", "branch": "dev"},
            {"url": "https://github.com/org/design-system", "branch": ""},
        ],
    },
}
LAPTOP = {
    "id": "p-laptop",
    "name": "Laptop work",
    "config": {"host_id": "host-laptop", "workspace": "/home/me/src/app"},
}


def item(item_id: str, kind: str = "message", **fields) -> dict:
    return {"id": item_id, "type": kind, "status": "completed", "created_at": 1792947796, **fields}


def said(item_id: str, role: str, text: str) -> dict:
    block = "input_text" if role == "user" else "output_text"
    return item(item_id, role=role, content=[{"type": block, "text": text}])


class FakeOmnigent:
    """Answers the API the tools use like Omnigent does, and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.projects = [ROADPASS, LAPTOP]
        self.label_projects = ["Old label project"]
        self.agents = [CLAUDE, CODEX, HERMES]
        self.items: list[dict] = []
        self.sessions = [
            {"id": "s1", "title": "Fix login", "status": "idle", "agent_name": "claude-native-ui",
             "created_at": 1792947000, "updated_at": 1792947796, "labels": {}, "archived": False},
        ]
        self.snapshots: list[dict] = []
        self.issued_tokens = 0
        self.reject_token: str | None = None
        self.message_gate: asyncio.Event | None = None
        self.message_error: str | None = None
        self.created: list[dict] = []

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)

    def find(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/oauth/token":
            self.issued_tokens += 1
            return httpx.Response(200, json={"access_token": f"at{self.issued_tokens}", "expires_in": 3600})
        auth_header = request.headers.get("authorization", "")
        if self.reject_token is not None and auth_header == f"Bearer {self.reject_token}":
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "Authentication required"}})
        if path == "/v1/agents":
            return httpx.Response(200, json={"object": "list", "data": self.agents})
        if path == "/v1/projects":
            return httpx.Response(200, json={"object": "list", "data": self.projects})
        if path == "/v1/sessions/projects":
            summaries = [{"id": p["id"], "name": p["name"]} for p in self.projects]
            summaries += [{"id": None, "name": n} for n in self.label_projects]
            return httpx.Response(200, json=summaries)
        if path == "/v1/sessions" and method == "GET":
            return httpx.Response(200, json={"object": "list", "data": self.sessions,
                                             "has_more": True, "last_id": "s1"})
        if path == "/v1/sessions" and method == "POST":
            self.created.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "new1", "agent_name": "codex-native-ui"})
        if path.endswith("/events"):
            if self.message_gate is not None:
                await self.message_gate.wait()
            if self.message_error is not None:
                return httpx.Response(503, json={"error": {"code": "runner_unavailable", "message": self.message_error}})
            return httpx.Response(202, json={"queued": True, "item_id": "m1"})
        if path.endswith("/items"):
            return httpx.Response(200, json=self.page(request.url.params))
        if path.startswith("/v1/sessions/"):
            if path == "/v1/sessions/missing":
                return httpx.Response(404, json={"error": {"code": "not_found", "message": "Not found."}})
            snapshot = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
            return httpx.Response(200, json=snapshot)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Not found."}})

    def page(self, params: httpx.QueryParams) -> dict:
        items = list(self.items)
        if params.get("order") == "desc":
            items.reverse()
        after = params.get("after")
        if after:
            ids = [i["id"] for i in items]
            items = items[ids.index(after) + 1:]
        limit = int(params.get("limit", 100))
        return {"object": "list", "data": items[:limit], "has_more": len(items) > limit}


def run(coro):
    return asyncio.run(coro)


async def with_tools(fake: FakeOmnigent, body, poll_seconds: float = 0):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
        creds = auth.Credentials(server=SERVER, refresh_token="rt")
        api = OmnigentApi(client, auth.TokenSource(client, creds))
        return await body(tools.SessionTools(api, poll_seconds=poll_seconds))


class CredentialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "omnigent-mcp" / "credentials.json"
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (auth.URL_ENV, auth.REFRESH_TOKEN_ENV, auth.CREDENTIALS_ENV):
            os.environ.pop(name, None)

    def test_a_saved_sign_in_is_only_readable_by_its_user_and_reads_back(self) -> None:
        auth.save_credentials(auth.Credentials(SERVER, "rt", "levi"), self.path)

        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(auth.load_credentials(self.path), auth.Credentials(SERVER, "rt", "levi"))

    def test_the_environment_wins_over_the_file(self) -> None:
        auth.save_credentials(auth.Credentials(SERVER, "rt", "levi"), self.path)
        os.environ[auth.URL_ENV] = "omni.other.com/"
        os.environ[auth.REFRESH_TOKEN_ENV] = "rt-env"

        creds = auth.load_credentials(self.path)

        self.assertEqual((creds.server, creds.refresh_token), ("https://omni.other.com", "rt-env"))

    def test_without_a_sign_in_it_says_how_to_sign_in(self) -> None:
        with self.assertRaisesRegex(auth.AuthError, "omnigent-mcp login"):
            auth.load_credentials(self.path)

    def test_the_credentials_file_can_move(self) -> None:
        os.environ[auth.CREDENTIALS_ENV] = str(self.path)

        self.assertEqual(auth.credentials_path(), self.path)


class LoginTest(unittest.TestCase):
    def test_login_asks_for_a_refresh_token_and_keeps_the_user(self) -> None:
        seen: list[dict] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"token": "jwt", "refresh_token": "rt", "user": {"id": "levi"}})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                return await auth.login(client, SERVER, "Levi", "pw")

        creds = run(go())

        self.assertEqual(seen, [{"username": "Levi", "password": "pw", "issue_refresh": True}])
        self.assertEqual(creds, auth.Credentials(SERVER, "rt", "levi"))

    def test_a_wrong_password_says_what_the_server_said(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid username or password"})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                await auth.login(client, SERVER, "levi", "nope")

        with self.assertRaisesRegex(auth.AuthError, "invalid username or password"):
            run(go())

    def test_an_expired_sign_in_says_to_sign_in_again(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "expired_token"})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                await auth.TokenSource(client, auth.Credentials(SERVER, "rt")).token()

        with self.assertRaisesRegex(auth.AuthError, "omnigent-mcp login"):
            run(go())


class ApiTest(unittest.TestCase):
    def test_an_access_token_is_reused_and_renewed_once_when_refused(self) -> None:
        fake = FakeOmnigent()

        async def body(t: tools.SessionTools):
            await t.list_agents()
            await t.list_agents()
            fake.reject_token = "at1"
            await t.list_agents()

        run(with_tools(fake, body))

        self.assertEqual(fake.issued_tokens, 2)
        self.assertEqual(fake.requests[-1].headers["authorization"], "Bearer at2")

    def test_errors_carry_omnigents_message(self) -> None:
        with self.assertRaisesRegex(OmnigentError, "404: Not found"):
            run(with_tools(FakeOmnigent(), lambda t: t.get_session("missing")))


class ProjectsTest(unittest.TestCase):
    def test_projects_show_what_their_sessions_start_with(self) -> None:
        result = run(with_tools(FakeOmnigent(), lambda t: t.list_projects()))

        road, laptop, label = result["projects"]
        self.assertEqual(road["default_host"], "sandbox")
        self.assertEqual(road["default_agent"], "codex-native-ui")
        self.assertEqual(road["default_model"], "gpt-5.5")
        self.assertEqual(
            road["repositories"],
            ["https://github.com/org/roadpass#dev", "https://github.com/org/design-system"],
        )
        self.assertEqual(laptop["default_host"], "connected host host-laptop")
        self.assertEqual(laptop["workspace"], "/home/me/src/app")
        self.assertEqual((label["id"], label["name"]), (None, "Old label project"))


class ListSessionsTest(unittest.TestCase):
    def params(self, **kwargs) -> httpx.QueryParams:
        fake = FakeOmnigent()
        run(with_tools(fake, lambda t: t.list_sessions(**kwargs)))
        return fake.find("GET", "/v1/sessions")[0].url.params

    def test_by_default_archived_sessions_are_left_out(self) -> None:
        params = self.params()

        self.assertNotIn("visibility", params)
        self.assertNotIn("include_archived", params)
        self.assertEqual(params["sort_by"], "updated_at")

    def test_only_archived_sessions(self) -> None:
        self.assertEqual(self.params(archived="only")["visibility"], "archived")

    def test_archived_sessions_too(self) -> None:
        self.assertEqual(self.params(archived="include")["include_archived"], "true")

    def test_a_project_given_by_id_filters_by_its_name(self) -> None:
        self.assertEqual(self.params(project="p-road")["project"], "Roadpass")

    def test_sessions_in_no_project(self) -> None:
        self.assertEqual(self.params(without_project=True)["project"], "")

    def test_sessions_have_a_url_and_the_next_page_a_cursor(self) -> None:
        result = run(with_tools(FakeOmnigent(), lambda t: t.list_sessions()))

        self.assertEqual(result["sessions"][0]["url"], f"{SERVER}/c/s1")
        self.assertEqual(result["next_cursor"], "s1")


class ReadSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeOmnigent()
        self.fake.items = [
            said("i1", "user", "Fix the login page"),
            item("i2", "function_call", name="Bash", arguments='{"command": "ls"}', call_id="c1"),
            item("i3", "function_call_output", output="src\n", call_id="c1"),
            item("i4", "reasoning", summary=[]),
            said("i5", "assistant", "Fixed it."),
            said("i6", "user", "Thanks"),
            item("i7", "error", message="Runner went away"),
        ]

    def test_the_latest_messages_come_oldest_first_without_tool_calls(self) -> None:
        result = run(with_tools(self.fake, lambda t: t.read_session("s1", limit=3)))

        self.assertEqual([e["id"] for e in result["entries"]], ["i5", "i6", "i7"])
        self.assertEqual(result["entries"][2]["role"], "error")
        self.assertEqual(result["older_than"], "i5")

    def test_reading_further_back(self) -> None:
        result = run(with_tools(self.fake, lambda t: t.read_session("s1", limit=3, older_than="i5")))

        self.assertEqual([e["id"] for e in result["entries"]], ["i1"])
        self.assertNotIn("older_than", result)

    def test_tool_calls_when_asked_and_long_text_cut(self) -> None:
        result = run(with_tools(
            self.fake, lambda t: t.read_session("s1", include_tools=True, max_chars=5)
        ))

        roles = [(e["id"], e["role"]) for e in result["entries"]]
        self.assertEqual(roles[1:3], [("i2", "tool_call"), ("i3", "tool_result")])
        self.assertEqual(result["entries"][1]["tool"], "Bash")
        self.assertEqual(result["entries"][0]["text"], "Fix t… [13 more characters]")


class CreateSessionTest(unittest.TestCase):
    def create(self, **kwargs) -> tuple[FakeOmnigent, dict, dict]:
        fake = FakeOmnigent()
        result = run(with_tools(fake, lambda t: t.create_session(**kwargs)))
        return fake, fake.created[0], result

    def test_in_a_sandbox_project_it_starts_with_the_projects_settings(self) -> None:
        _, body, result = self.create(project="roadpass")

        self.assertEqual(body, {
            "project_id": "p-road",
            "host_type": "managed",
            "workspaces": [
                "https://github.com/org/roadpass#dev", "https://github.com/org/design-system"
            ],
            # Explicit, so the server doesn't fill in a working directory.
            "workspace": None,
            "git": None,
            "model_override": "gpt-5.5",
        })
        self.assertEqual(result["agent"], "codex-native-ui")
        self.assertEqual(result["url"], f"{SERVER}/c/new1")
        self.assertEqual(result["runs_on"], "sandbox")

    def test_what_you_pass_replaces_the_projects_settings(self) -> None:
        _, body, _ = self.create(
            project="Roadpass", agent="Claude Code", repositories=["https://x/y#main"], model="opus"
        )

        self.assertEqual(body["agent_id"], "ag-claude")
        self.assertEqual(body["workspaces"], ["https://x/y#main"])
        self.assertEqual(body["model_override"], "opus")

    def test_an_explicit_agent_doesnt_take_the_projects_model(self) -> None:
        _, body, _ = self.create(project="roadpass", agent="hermes")

        self.assertEqual(body["agent_id"], "ag-hermes")
        self.assertNotIn("model_override", body)

    def test_without_a_project_it_runs_claude_code_in_an_empty_sandbox(self) -> None:
        _, body, _ = self.create(title="Scratch", reasoning_effort="high")

        self.assertEqual(body, {
            "agent_id": "ag-claude",
            "host_type": "managed",
            "workspaces": [],
            "title": "Scratch",
            "reasoning_effort": "high",
        })

    def test_a_project_on_a_connected_host_starts_there(self) -> None:
        _, body, result = self.create(project="laptop work")

        self.assertEqual(body, {"project_id": "p-laptop", "agent_id": "ag-claude", "host_id": "host-laptop"})
        self.assertEqual(result["runs_on"], "connected host host-laptop")

    def test_a_connected_host_cant_clone_repositories(self) -> None:
        with self.assertRaisesRegex(OmnigentError, "connected host"):
            self.create(project="laptop work", repositories=["https://x/y"])

    def test_a_label_only_project_is_a_label(self) -> None:
        _, body, _ = self.create(project="Old label project")

        self.assertEqual(body["labels"], {"omni_project": "Old label project"})
        self.assertNotIn("project_id", body)

    def test_an_unknown_project_or_agent_lists_the_known_ones(self) -> None:
        with self.assertRaisesRegex(OmnigentError, "Roadpass"):
            self.create(project="nope")
        with self.assertRaisesRegex(OmnigentError, "codex-native-ui"):
            self.create(agent="nope")

    def test_the_first_message_is_sent_after_the_session_exists(self) -> None:
        fake, _, result = self.create(message="Fix the login page")

        event = json.loads(fake.find("POST", "/v1/sessions/new1/events")[0].content)
        self.assertEqual(event, {
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": "Fix the login page"}]},
        })
        self.assertEqual((result["message"], result["since"]), ("delivered", ""))


class DeliveryTest(unittest.TestCase):
    def test_a_message_waiting_for_a_sandbox_is_queued_and_waited_for(self) -> None:
        fake = FakeOmnigent()
        fake.message_gate = asyncio.Event()
        fake.snapshots = [{"id": "new1", "status": "idle"}]

        async def body(t: tools.SessionTools):
            with mock.patch.object(tools, "DELIVERY_WAIT_SECONDS", 0.01):
                created = await t.create_session(message="hi")
            self.assertTrue(created["message"].startswith("queued"))
            self.assertEqual(
                (await t.get_session("new1"))["message_delivery"], "waiting for the sandbox to start"
            )

            async def sandbox_starts():
                await asyncio.sleep(0.05)
                fake.items = [said("u1", "user", "hi"), said("a1", "assistant", "Hello!")]
                fake.message_gate.set()

            starting = asyncio.create_task(sandbox_starts())
            reply = await t.wait_for_reply("new1", since=created["since"], timeout_seconds=5)
            await starting
            return reply

        reply = run(with_tools(fake, body))

        self.assertEqual(reply["status"], "idle")
        self.assertEqual([r["text"] for r in reply["replies"]], ["Hello!"])
        self.assertEqual(reply["since"], "a1")

    def test_a_message_the_sandbox_never_got_reports_why(self) -> None:
        fake = FakeOmnigent()
        fake.message_error = "The session's managed sandbox failed to launch: no capacity"

        with self.assertRaisesRegex(OmnigentError, "no capacity"):
            run(with_tools(fake, lambda t: t.send_message("s1", "hi")))


class WaitForReplyTest(unittest.TestCase):
    def test_the_previous_turns_status_doesnt_end_the_wait(self) -> None:
        fake = FakeOmnigent()
        fake.items = [said("a0", "assistant", "Earlier answer")]
        # Still the last turn's failure, then busy, then done.
        fake.snapshots = [
            {"status": "failed"}, {"status": "running"}, {"status": "idle"},
        ]
        polls = 0
        real_page = fake.page

        def page(params):
            nonlocal polls
            polls += 1
            if polls == 2:
                fake.items += [said("u1", "user", "Again"), said("a1", "assistant", "New answer")]
            return real_page(params)

        fake.page = page
        reply = run(with_tools(fake, lambda t: t.wait_for_reply("s1", since="a0", timeout_seconds=5)))

        self.assertEqual(reply["status"], "idle")
        self.assertEqual([r["text"] for r in reply["replies"]], ["New answer"])

    def test_a_turn_that_fails_without_a_word_reports_the_error(self) -> None:
        fake = FakeOmnigent()
        fake.items = [said("u1", "user", "Go")]
        error = {"code": "RuntimeError", "message": "Claude Code didn't start"}
        fake.snapshots = [{"status": "failed", "last_task_error": error}]

        with mock.patch.object(tools, "SETTLE_SECONDS", 0):
            reply = run(with_tools(fake, lambda t: t.wait_for_reply("s1", timeout_seconds=5)))

        self.assertEqual(reply["status"], "failed")
        self.assertEqual(reply["error"], error)

    def test_a_question_for_the_user_ends_the_wait(self) -> None:
        fake = FakeOmnigent()
        fake.snapshots = [{"status": "running", "pending_elicitations": [{"message": "Allow rm?"}]}]

        reply = run(with_tools(fake, lambda t: t.wait_for_reply("s1", timeout_seconds=5)))

        self.assertEqual(reply["status"], "needs_response")
        self.assertEqual(reply["needs_response"], [{"message": "Allow rm?"}])

    def test_timing_out_says_to_wait_again(self) -> None:
        fake = FakeOmnigent()
        fake.snapshots = [{"status": "running"}]

        reply = run(with_tools(
            fake, lambda t: t.wait_for_reply("s1", timeout_seconds=1), poll_seconds=0.2
        ))

        self.assertEqual(reply["status"], "still_working")


if __name__ == "__main__":
    unittest.main()
