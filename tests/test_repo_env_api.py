"""The Settings page's routes, against an in-memory Kubernetes.

Needs FastAPI and Omnigent, which the server image has and CI's Python
doesn't, so CI skips it. Run it in a runner Pod with:

    PYTHONPATH=images/server /opt/venv/bin/python -m unittest tests.test_repo_env_api
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from omnigent.errors import OmnigentError
    from omnigent_repo_env_api import add_token_repositories, create_repo_env_router, repository_token
except ImportError:  # CI's Python
    create_repo_env_router = None

import omnigent_repo_env as repo_env  # noqa: E402
from test_repo_env import FakeApiException, FakeCore, decoded  # noqa: E402

ROADPASS = "github.com/kunlabora/roadpass"


class HeaderAuth:
    def get_user_id(self, request):
        return request.headers.get("x-user")


@unittest.skipIf(create_repo_env_router is None, "needs FastAPI and Omnigent")
class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore()
        app = FastAPI()

        @app.exception_handler(OmnigentError)
        async def omnigent_error(request, exc):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=exc.http_status, content={"error": {"message": exc.message}})

        app.include_router(
            create_repo_env_router(
                auth_provider=HeaderAuth(),
                is_admin=lambda user: user == "admin",
                multi_user=True,
                core=lambda: self.core,
            ),
            prefix="/v1",
        )
        self.client = TestClient(app)

    def call(self, method: str, path: str, user: str | None = "admin", **kwargs):
        headers = {"x-user": user} if user else {}
        return self.client.request(method, path, headers=headers, **kwargs)

    def test_admin_saves_lists_and_deletes(self) -> None:
        saved = self.call("PUT", "/v1/repo-env", json={
            "repository": "git@github.com:Kunlabora/RoadPass.git",
            "variables": {"TOKEN": "abc", "URL": "https://x"},
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["repository"], ROADPASS)
        self.assertEqual(saved.json()["updated_by"], "admin")
        listed = self.call("GET", "/v1/repo-env").json()
        self.assertEqual([(e["repository"], e["names"]) for e in listed["data"]], [(ROADPASS, ["TOKEN", "URL"])])
        self.assertNotIn("abc", self.call("GET", "/v1/repo-env").text)
        kept = self.call("PUT", "/v1/repo-env", json={"repository": ROADPASS, "variables": {"TOKEN": None}})
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(decoded(self.core), {"TOKEN": "abc"})
        deleted = self.call("DELETE", "/v1/repo-env", params={"repository": "kunlabora/roadpass"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.core.secrets, {})

    def test_only_admins_manage_and_anyone_signed_in_sees_names(self) -> None:
        self.call("PUT", "/v1/repo-env", json={"repository": ROADPASS, "variables": {"TOKEN": "abc"}})
        self.assertEqual(self.call("GET", "/v1/repo-env", user="member").status_code, 403)
        self.assertEqual(self.call("GET", "/v1/repo-env", user=None).status_code, 401)
        put = self.call("PUT", "/v1/repo-env", user="member", json={"repository": ROADPASS, "variables": {"A": "1"}})
        self.assertEqual(put.status_code, 403)
        delete = self.call("DELETE", "/v1/repo-env", user="member", params={"repository": ROADPASS})
        self.assertEqual(delete.status_code, 403)
        names = self.call("GET", "/v1/repo-env/names", user="member",
                          params={"repository": "https://github.com/kunlabora/roadpass.git"})
        self.assertEqual(names.json(), {"repository": ROADPASS, "names": ["TOKEN"]})
        self.assertEqual(self.call("GET", "/v1/repo-env/names", user=None, params={"repository": ROADPASS}).status_code, 401)
        bad = self.call("GET", "/v1/repo-env/names", user="member", params={"repository": "nope"})
        self.assertEqual(bad.json(), {"repository": None, "names": []})

    def test_errors_are_messages_the_page_can_show(self) -> None:
        reserved = self.call("PUT", "/v1/repo-env", json={"repository": ROADPASS, "variables": {"PATH": "/x"}})
        self.assertEqual((reserved.status_code, reserved.json()["error"]["message"]), (400, "PATH is reserved for the runner"))
        bad = self.call("PUT", "/v1/repo-env", json={"repository": "not a repo", "variables": {"A": "1"}})
        self.assertEqual(bad.status_code, 400)
        forbidden = FakeCore()
        forbidden.list_namespaced_secret = lambda *a, **k: (_ for _ in ()).throw(FakeApiException(403))
        self.core = forbidden
        refused = self.call("GET", "/v1/repo-env")
        self.assertEqual(refused.status_code, 500)
        self.assertIn("mise run bootstrap", refused.json()["error"]["message"])


def app_repo(full_name: str, pushed_at: str | None) -> dict:
    return {
        "full_name": full_name,
        "clone_url": f"https://github.com/{full_name}.git",
        "default_branch": "main",
        "private": False,
        "pushed_at": pushed_at,
    }


@unittest.skipIf(create_repo_env_router is None, "needs FastAPI and Omnigent")
class PickerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore()
        repo_env.save_repository(self.core, ROADPASS, {"GIT_TOKEN": "roadpass-token"})
        repo_env.save_repository(self.core, "github.com/kunlabora/gone", {"GIT_TOKEN": "gone-token"})
        repo_env.save_repository(self.core, "github.com/me/listed", {"GIT_TOKEN": "listed-token"})
        repo_env.save_repository(self.core, "github.com/me/npm", {"NPM": "x"})
        self.requests: list[tuple[str, str]] = []

    def github(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.url.path, request.headers["Authorization"]))
        if request.url.path == "/repos/kunlabora/roadpass":
            return httpx.Response(200, json={
                "full_name": "Kunlabora/RoadPass",
                "clone_url": "https://github.com/Kunlabora/RoadPass.git",
                "default_branch": "develop",
                "private": True,
                "pushed_at": "2026-10-07T10:00:00Z",
            })
        return httpx.Response(404, json={"message": "Not Found"})

    def add(self, repos: list[dict], core=None) -> list[dict]:
        return asyncio.run(add_token_repositories(
            repos, core=core or (lambda: self.core), transport=httpx.MockTransport(self.github),
        ))

    def test_adds_repositories_with_a_token_newest_push_first(self) -> None:
        listed = self.add([
            app_repo("me/newest", "2026-10-08T00:00:00Z"),
            app_repo("Me/Listed", "2026-10-06T00:00:00Z"),
            app_repo("me/older", "2026-10-01T00:00:00Z"),
        ])
        self.assertEqual(
            [(r["full_name"], r["default_branch"]) for r in listed],
            [("me/newest", "main"), ("Kunlabora/RoadPass", "develop"), ("Me/Listed", "main"),
             ("me/older", "main"), ("kunlabora/gone", None)],
        )
        self.assertEqual(listed[1]["clone_url"], "https://github.com/Kunlabora/RoadPass.git")
        # A repository GitHub doesn't show the token is still listed, so its clone says why.
        self.assertEqual(listed[-1]["clone_url"], "https://github.com/kunlabora/gone.git")
        self.assertEqual(sorted(self.requests), [
            ("/repos/kunlabora/gone", "Bearer gone-token"),
            ("/repos/kunlabora/roadpass", "Bearer roadpass-token"),
        ])

    def test_unreadable_secrets_leave_the_app_list_alone(self) -> None:
        def broken():
            raise FakeApiException(403)

        repos = [app_repo("me/newest", "2026-10-08T00:00:00Z")]
        self.assertEqual(self.add(repos, core=broken), repos)
        self.assertEqual(self.requests, [])

    def test_branches_use_the_repository_token(self) -> None:
        self.assertEqual(asyncio.run(repository_token("Kunlabora", "RoadPass", core=lambda: self.core)), "roadpass-token")
        self.assertIsNone(asyncio.run(repository_token("me", "npm", core=lambda: self.core)))
        self.assertIsNone(asyncio.run(repository_token("me", "other", core=lambda: self.core)))


if __name__ == "__main__":
    unittest.main()
