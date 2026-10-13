"""The runner Pod's reporter: images/runner/startup/startup-report.py.

Runs it against a local HTTP server standing in for Omnigent's
``POST /v1/hosts/{host_id}/startup``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "images" / "runner" / "startup" / "startup-report.py"


class _Server(HTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.received: list[tuple[str | None, dict]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.received.append((self.headers.get("X-Omnigent-Host-Token"), body))  # type: ignore[attr-defined]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"accepted": true}')

    def log_message(self, *args: object) -> None:
        pass


class ReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # A repository's variables, as the kubelet mounts them.
        secret = self.tmp / "repo-env" / "omnigent-repo-env-roadpass"
        secret.mkdir(parents=True)
        (secret / "NPM_TOKEN").write_text("npm_supersecret123")
        (secret / "setup.sh").write_text("echo not a variable")
        (secret / "SHORT").write_text("abc")

    def run_reporter(self, *args: str, url: bool = True) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "OMNIGENT_HOST_TOKEN": "launch-token",
            "OMNIGENT_REPO_ENV_DIR": str(self.tmp / "repo-env"),
            "OMNIGENT_STARTUP_REPORT_POLL_S": "0.05",
        }
        if url:
            env["OMNIGENT_STARTUP_REPORT_URL"] = f"http://127.0.0.1:{self.server.server_port}/v1/hosts/h/startup"
        return subprocess.run(
            [sys.executable, str(REPORTER), *args], env=env, capture_output=True, text=True, timeout=30
        )

    def test_a_step_goes_with_the_launch_token(self) -> None:
        result = self.run_reporter("step", "setup", "roadpass", "running", "Running its setup script")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.received, [(
            "launch-token",
            {"steps": [{"step": "setup", "child": "roadpass", "state": "running", "detail": "Running its setup script"}]},
        )])

    def test_a_failure_brings_its_log_without_the_variables(self) -> None:
        log = self.tmp / "roadpass.log"
        log.write_text("+ npm install\nusing npm_supersecret123\nnpm ERR! 401\n")
        self.run_reporter("step", "setup", "roadpass", "failed", "Exit status 1", "--log", str(log))
        (_, body), = self.server.received
        entry = body["steps"][0]
        self.assertEqual(entry["state"], "failed")
        self.assertIn("using [hidden]\nnpm ERR! 401", entry["log_tail"])
        self.assertNotIn("supersecret", json.dumps(body))

    def test_follow_sends_new_lines_until_the_process_exits(self) -> None:
        log = self.tmp / "setup.log"
        log.write_text("")
        writer = subprocess.Popen(
            ["sh", "-c", f"echo one >> {log}; sleep 0.3; echo npm_supersecret123 >> {log}; printf partial >> {log}"]
        )
        result = self.run_reporter("follow", "setup", str(log), str(writer.pid))
        writer.wait()
        self.assertEqual(result.returncode, 0, result.stderr)
        texts = [line["text"] for _, body in self.server.received for line in body["log"]]
        self.assertEqual(texts, ["one", "[hidden]", "partial"])
        self.assertTrue(all(line["source"] == "setup" for _, b in self.server.received for line in b["log"]))

    def test_it_does_nothing_without_a_server(self) -> None:
        result = self.run_reporter("step", "setup", "roadpass", "done", url=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.server.received, [])

    def test_a_server_that_is_down_does_not_fail_the_caller(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        result = self.run_reporter("step", "host", "Docker", "done")
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
