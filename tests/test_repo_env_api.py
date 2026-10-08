"""The Settings page's routes, against an in-memory Kubernetes.

Needs FastAPI and Omnigent, which the server image has and CI's Python
doesn't, so CI skips it. Run it in a runner Pod with:

    PYTHONPATH=images/server /opt/venv/bin/python -m unittest tests.test_repo_env_api
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
sys.path.insert(0, str(ROOT / "tests"))

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from omnigent.errors import OmnigentError
    from omnigent_repo_env_api import create_repo_env_router
except ImportError:  # CI's Python
    create_repo_env_router = None

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


if __name__ == "__main__":
    unittest.main()
