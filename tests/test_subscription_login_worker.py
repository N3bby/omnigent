"""Drive the actual PTY bridge with a fake CLI; no provider credentials needed."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "images/runner/subscription-login.py"
spec = importlib.util.spec_from_file_location("subscription_login_worker", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_hyperlink_url_is_complete_and_restricted_to_claude(self):
        url = "https://claude.com/cai/oauth/authorize?code_challenge=example&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
        self.assertEqual(worker.authorization_url(f"\x1b]8;id=link;{url}\x1b\\wrapped text\x1b]8;;\x1b\\"), url)
        self.assertIsNone(worker.authorization_url("\x1b]8;;https://evil.example/login\x1b\\"))
        terminal = worker.LoginTerminal()
        terminal.feed(b"sk-ant-oat01-partial")
        self.assertIsNone(terminal.token())

    def test_cursor_addressing_and_split_reads_capture_the_whole_token(self):
        terminal = worker.LoginTerminal()
        terminal.feed(b"Your OAuth token:\r\n")
        token = "sk-ant-oat01-" + "whole_token_" * 8
        # A cursor-forward sequence represents the 'o' which raw text stripping loses.
        terminal.feed(b"sk-ant-oat01-" + b"whole_token_" * 8 + b"\r\n")
        self.assertIsNone(terminal.token())
        terminal.feed(b"\x1b[1;1HYour OAuth token:\x1b[2;1H")
        terminal.feed(b"s\x1b[2;2Hk-ant-oat01-" + b"whole_token_" * 8)
        terminal.feed(b"\x1b[3;1HStore this token securely.")
        self.assertEqual(terminal.token(), token)

    def test_pty_code_exchange_keeps_credentials_out_of_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "fake-claude"
            token = "sk-ant-oat01-" + "a" * 100
            cli.write_text(f'''#!{sys.executable}
import sys
print("\\x1b]8;;https://claude.com/cai/oauth/authorize?code_challenge=test\\x1b\\\\Open browser\\x1b]8;;\\x1b\\\\", flush=True)
code = input()
assert "one-time-code#state" in code
print("Your OAuth token:", flush=True)
print("{token}", flush=True)
print("Store this token securely.", flush=True)
''')
            cli.chmod(0o700)
            state = root / "state"
            env = {**os.environ, "OMNIGENT_LOGIN_DIR": str(state), "OMNIGENT_CLAUDE_LOGIN_BINARY": str(cli)}
            proc = subprocess.Popen([sys.executable, str(WORKER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if (state / "status.json").exists() and json.loads((state / "status.json").read_text())["status"] == "waiting":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Worker did not publish the browser challenge")
                (state / "code.txt").write_text("one-time-code#state")
                stdout, stderr = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertEqual(json.loads((state / "status.json").read_text()), {"status": "complete"})
                self.assertEqual((state / "credential.txt").read_text(), token)
                self.assertNotIn(token, stdout + stderr)
                self.assertNotIn("one-time-code", stdout + stderr)
                self.assertEqual((state / "credential.txt").stat().st_mode & 0o777, 0o600)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
