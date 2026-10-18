"""Settings → MCP servers routes against fake Kubernetes; no live credentials."""
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images/server"))
sys.path.insert(0, str(ROOT / "tests"))
from test_repo_env import FakeCore
from test_mcp_servers import BEARER, FIGMA, FIGMA_KEY, LINEAR

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse
    from omnigent.errors import OmnigentError
    from omnigent_mcp_servers_api import create_settings_mcp_servers_router
except ImportError:
    create_settings_mcp_servers_router = None

ADMIN = {"x-user": "admin"}


class HeaderAuth:
    def get_user_id(self, request):
        return request.headers.get("x-user")


@unittest.skipIf(create_settings_mcp_servers_router is None, "needs FastAPI and Omnigent")
class Routes(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        app = FastAPI()
        @app.exception_handler(OmnigentError)
        async def handler(request, exc):
            return JSONResponse(status_code=exc.http_status, content={"error": {"message": exc.message}})
        app.include_router(create_settings_mcp_servers_router(HeaderAuth(), lambda user: user == "admin", True,
                                                     core=lambda: self.core), prefix="/v1")
        self.client = TestClient(app)

    def test_every_route_is_admin_only(self):
        for user, status in [(None, 401), ("member", 403)]:
            for method, path, body in [("GET", "", None), ("PUT", "", FIGMA), ("DELETE", "/abc", None)]:
                with self.subTest(user=user, method=method):
                    response = self.client.request(method, "/v1/mcp-servers" + path,
                                                   headers={"x-user": user} if user else {}, json=body)
                    self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.core.secrets, {})

    def test_saving_and_listing_never_return_values(self):
        saved = self.client.put("/v1/mcp-servers", headers=ADMIN, json=FIGMA)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["updated_by"], "admin")
        self.client.put("/v1/mcp-servers", headers=ADMIN, json=LINEAR)
        listing = self.client.get("/v1/mcp-servers", headers=ADMIN)
        self.assertEqual([s["name"] for s in listing.json()["data"]], ["figma", "linear"])
        for text in (saved.text, listing.text):
            self.assertNotIn(FIGMA_KEY, text)
            self.assertNotIn(BEARER, text)
        server_id = saved.json()["id"]
        kept = self.client.put("/v1/mcp-servers", headers=ADMIN,
                               json={**FIGMA, "id": server_id, "env": {"FIGMA_API_KEY": None}})
        self.assertEqual(kept.json()["env_names"], ["FIGMA_API_KEY"])
        deleted = self.client.delete(f"/v1/mcp-servers/{server_id}", headers=ADMIN)
        self.assertEqual(deleted.json(), {"id": server_id, "deleted": True})

    def test_errors_never_echo_the_values_sent(self):
        malformed = self.client.put("/v1/mcp-servers", headers=ADMIN,
                                    json={**LINEAR, "headers": {"Authorization": {"nested": BEARER}}})
        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertNotIn(BEARER, malformed.text)
        invalid = self.client.put("/v1/mcp-servers", headers=ADMIN,
                                  json={**LINEAR, "headers": {"Authorization": BEARER + "\n"}})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertNotIn(BEARER, invalid.text)
        missing = self.client.delete("/v1/mcp-servers/gone", headers=ADMIN)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("no longer exists", json.loads(missing.text)["error"]["message"])

    def test_a_kubernetes_failure_hides_the_secret(self):
        class Broken(FakeCore):
            def read_namespaced_secret(self, name, namespace):
                raise RuntimeError("body: " + FIGMA_KEY)
        self.core = Broken()
        response = self.client.get("/v1/mcp-servers", headers=ADMIN)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(FIGMA_KEY, response.text)


if __name__ == "__main__":
    unittest.main()
