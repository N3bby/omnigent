"""Routes and device-login lifecycle against fake Kubernetes; no live credentials."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images/server"))
sys.path.insert(0, str(ROOT / "tests"))
from test_repo_env import FakeCore
from test_subscriptions import TOKEN, AUTH
import omnigent_subscription_login as login

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    from omnigent.errors import OmnigentError
    from omnigent_subscriptions_api import create_subscriptions_router
except ImportError:
    create_subscriptions_router = None


class HeaderAuth:
    def get_user_id(self, request):
        return request.headers.get("x-user")


@unittest.skipIf(create_subscriptions_router is None, "needs FastAPI and Omnigent")
class Routes(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        app = FastAPI()
        @app.exception_handler(OmnigentError)
        async def handler(request, exc):
            return JSONResponse(status_code=exc.http_status, content={"error": {"message": exc.message}})
        app.include_router(create_subscriptions_router(HeaderAuth(), lambda user: user == "admin", True,
                                                       core=lambda: self.core), prefix="/v1")
        self.client = TestClient(app)

    def test_auth_applies_to_every_read_and_mutation_including_login(self):
        for user, status in [(None, 401), ("member", 403)]:
            for method, path, body in [
                ("GET", "", None), ("PUT", "", {"provider": "claude", "name": "Personal", "credential": TOKEN}),
                ("POST", "/account/activate", None), ("DELETE", "/account", None),
                ("POST", "/login/codex", {"name": "Personal"}), ("GET", "/login/" + "a" * 32, None),
                ("DELETE", "/login/" + "a" * 32, None),
                ("POST", "/login/claude", {"name": "Personal"}),
                ("POST", "/login/" + "a" * 32 + "/code", {"code": "authorization-code"}),
            ]:
                with self.subTest(user=user, path=path):
                    response = self.client.request(method, "/v1/subscriptions" + path,
                        headers={"x-user": user} if user else {}, json=body)
                    self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.core.secrets, {})

    def test_save_and_list_never_return_credentials(self):
        response = self.client.put("/v1/subscriptions", headers={"x-user": "admin"},
                                  json={"provider": "claude", "name": "Personal", "credential": TOKEN})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(TOKEN, response.text)
        self.assertNotIn(TOKEN, self.client.get("/v1/subscriptions", headers={"x-user": "admin"}).text)
        invalid = self.client.put("/v1/subscriptions", headers={"x-user": "admin"},
                                 json={"provider": "claude", "name": "Bad", "credential": "x" * 70000})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertLess(len(invalid.text), 500)
        malformed = self.client.put("/v1/subscriptions", headers={"x-user": "admin"},
            json={"provider": "claude", "name": "Bad", "credential": {"secret": TOKEN}})
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertNotIn(TOKEN, malformed.text)


class LoginTests(unittest.TestCase):
    def test_login_only_returns_device_challenge_then_stores_auth_and_deletes_pod(self):
        class Core(FakeCore):
            def read_namespaced_pod(self, *args, **kwargs):
                return SimpleNamespace(status=SimpleNamespace(phase="Running"),
                    metadata=SimpleNamespace(labels={login.LABEL: "codex"}, annotations={"omnigent.dev/subscription-name": "Personal"}))
            def read_namespaced_pod_log(self, *args, **kwargs):
                return "Go to https://auth.openai.com/codex/device and enter ABCD-EFGHI\nprivate-log-text"
            def delete_namespaced_pod(self, *args, **kwargs):
                self.deleted = True
        core = Core()
        result = login.poll(core, "a" * 32, execute=lambda command: "")
        self.assertEqual(result, {"status": "waiting", "code": "ABCD-EFGHI", "url": "https://auth.openai.com/codex/device"})
        result = login.poll(core, "a" * 32, execute=lambda command: AUTH)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn("test-access", json.dumps(result))
        self.assertTrue(core.deleted)
        self.assertEqual(result["subscriptions"]["data"][0]["name"], "Personal")

    def test_helper_is_bounded_and_never_mounts_existing_credentials(self):
        class Core(FakeCore):
            def list_namespaced_pod(self, *args, **kwargs):
                return SimpleNamespace(items=[])
            def create_namespaced_pod(self, namespace, body):
                self.body = body
        core = Core()
        with patch.dict("os.environ", {"OMNIGENT_SUBSCRIPTION_LOGIN_IMAGE": "example/runner@sha256:abc"}):
            login.start(core, "Personal")
        spec = core.body["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["activeDeadlineSeconds"], 900)
        self.assertNotIn("volumes", spec)
        for bad in ["../pod", "a" * 31, "pod"]:
            with self.assertRaises(ValueError):
                login.pod_name(bad)


class ClaudeLoginTests(unittest.TestCase):
    def setUp(self):
        class Core(FakeCore):
            def read_namespaced_pod(self, *args, **kwargs):
                return SimpleNamespace(status=SimpleNamespace(phase="Running"),
                    metadata=SimpleNamespace(labels={login.LABEL: "claude"}, annotations={"omnigent.dev/subscription-name": "Personal"}))
            def delete_namespaced_pod(self, *args, **kwargs):
                self.deleted = True
        self.core = Core()

    def test_browser_challenge_then_completion_does_not_return_token(self):
        url = "https://claude.com/cai/oauth/authorize?code_challenge=example"
        waiting = login.poll(self.core, "a" * 32, execute=lambda command: json.dumps({"status": "waiting", "url": url}))
        self.assertEqual(waiting, {"status": "waiting", "url": url})
        done = login.poll(self.core, "a" * 32, execute=lambda command: json.dumps({"status": "complete", "credential": TOKEN}))
        self.assertEqual(done["status"], "complete")
        self.assertEqual(done["subscriptions"]["data"][0]["provider"], "claude")
        self.assertNotIn(TOKEN, json.dumps(done))
        self.assertTrue(self.core.deleted)

    def test_code_uses_stdin_and_rejects_control_characters(self):
        calls = []
        result = login.submit_code(self.core, "a" * 32, "auth-code#state", execute=lambda command, code: calls.append((command, code)))
        self.assertEqual(result["status"], "exchanging")
        self.assertNotIn("auth-code#state", json.dumps(calls[0][0]))
        self.assertEqual(calls[0][1], "auth-code#state")
        for bad in ("bad\ncode", "\x1bcommand", "x" * 2049):
            with self.assertRaises(ValueError):
                login.submit_code(self.core, "a" * 32, bad, execute=lambda *args: None)

    def test_untrusted_authorization_url_is_rejected(self):
        with self.assertRaises(ValueError):
            login.poll(self.core, "a" * 32, execute=lambda command: json.dumps({"status": "waiting", "url": "https://attacker.example/login"}))

class CompletionTests(unittest.TestCase):
    def test_completion_is_idempotent_and_survives_cleanup_failure(self):
        from test_repo_env import FakeApiException
        import omnigent_subscriptions as subscriptions
        class Core(FakeCore):
            def delete_namespaced_pod(self, *args, **kwargs):
                raise FakeApiException(500)
        core = Core()
        pod = SimpleNamespace(metadata=SimpleNamespace(annotations={"omnigent.dev/subscription-name": "Personal"}))
        first = login._complete(core, pod, "a" * 32, "claude", TOKEN)
        second = login._complete(core, pod, "a" * 32, "claude", TOKEN)
        self.assertEqual(first, second)
        self.assertEqual(len(subscriptions.read(core)[0]["accounts"]), 1)

    def test_missing_helper_recovers_an_already_saved_login(self):
        from test_repo_env import FakeApiException
        import omnigent_subscriptions as subscriptions
        class Core(FakeCore):
            def read_namespaced_pod(self, *args, **kwargs):
                raise FakeApiException(404)
        core = Core()
        subscriptions.save(core, "claude", "Personal", TOKEN, login_id="a" * 32)
        result = login.poll(core, "a" * 32)
        self.assertEqual(result["status"], "complete")
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(login.poll(core, "b" * 32), {"status": "expired"})

    def test_new_helpers_have_small_storage_limits(self):
        class Core(FakeCore):
            def list_namespaced_pod(self, *args, **kwargs):
                return SimpleNamespace(items=[])
            def create_namespaced_pod(self, namespace, body):
                self.body = body
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider), patch.dict("os.environ", {"OMNIGENT_SUBSCRIPTION_LOGIN_IMAGE": "example/runner@sha256:abc"}):
                core = Core()
                login.start(core, "Personal", provider=provider)
                resources = core.body["spec"]["containers"][0]["resources"]
                self.assertEqual(resources["requests"]["ephemeral-storage"], "64Mi")
                self.assertEqual(resources["limits"]["ephemeral-storage"], "128Mi")

    def test_three_helpers_block_another_start(self):
        class Core(FakeCore):
            def list_namespaced_pod(self, *args, **kwargs):
                return SimpleNamespace(items=[SimpleNamespace(status=SimpleNamespace(phase="Running"))]*3)
            def create_namespaced_pod(self, *args, **kwargs):
                raise AssertionError("Must not create a fourth helper")
        with patch.dict("os.environ", {"OMNIGENT_SUBSCRIPTION_LOGIN_IMAGE": "example/runner@sha256:abc"}), self.assertRaisesRegex(ValueError, "Three"):
            login.start(Core(), "Personal")
