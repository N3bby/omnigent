from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images/server"))
sys.path.insert(0, str(ROOT / "tests"))
import omnigent_subscriptions as subscriptions
from test_repo_env import FakeCore, FakeApiException

TOKEN = "sk-ant-oat01-test_personal"
WORK = "sk-ant-oat01-test_work"
AUTH = json.dumps({"auth_mode": "chatgpt", "tokens": {
    "id_token": "test-id", "access_token": "test-access", "refresh_token": "test-refresh"}})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()

    def test_multiple_accounts_switch_and_rotation_are_atomic_and_redacted(self):
        personal = subscriptions.save(self.core, "claude", "Personal", TOKEN)["data"][0]
        result = subscriptions.save(self.core, "claude", "Work", WORK)
        work = result["data"][1]
        self.assertTrue(result["data"][0]["active"])
        self.assertFalse(work["active"])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(WORK, json.dumps(result))
        subscriptions.activate(self.core, work["id"])
        secret = self.core.read_namespaced_secret(subscriptions.SECRET, subscriptions.NAMESPACE)
        import base64
        self.assertEqual(base64.b64decode(secret.data["claude-token"]).decode(), WORK)
        previous, version = subscriptions.read(self.core)
        revision = previous["accounts"][1]["revision"]
        subscriptions.save(self.core, "claude", "Work renamed", None, work["id"])
        renamed, _ = subscriptions.read(self.core)
        self.assertEqual(renamed["accounts"][1]["credential"], WORK)
        self.assertEqual(renamed["accounts"][1]["revision"], revision)
        with self.assertRaises(FakeApiException):
            subscriptions.write(self.core, previous, version)
        subscriptions.save(self.core, "claude", "Work renamed", TOKEN, work["id"])
        rotated, _ = subscriptions.read(self.core)
        self.assertNotEqual(rotated["accounts"][1]["revision"], revision)
        with self.assertRaisesRegex(ValueError, "Switch"):
            subscriptions.delete(self.core, work["id"])
        subscriptions.delete(self.core, personal["id"])
        self.assertEqual(len(subscriptions.read(self.core)[0]["accounts"]), 1)

    def test_providers_switch_independently_and_validate_credentials(self):
        subscriptions.save(self.core, "claude", "Personal", TOKEN)
        subscriptions.save(self.core, "codex", "Personal", AUTH)
        result = subscriptions.summary(subscriptions.read(self.core)[0])
        self.assertEqual(set(result["active"]), {"claude", "codex"})
        for provider, credential in [("claude", "api-key"), ("codex", "{}"),
                                    ("codex", '[]'), ("codex", '{"tokens":null}')]:
            with self.subTest(provider=provider, credential=credential), self.assertRaises(ValueError):
                subscriptions.save(self.core, provider, "Bad", credential)
        with self.assertRaisesRegex(ValueError, "already"):
            subscriptions.save(self.core, "claude", "personal", TOKEN)

    def test_only_active_credentials_are_projected(self):
        volumes, mounts = subscriptions.pod_volumes()
        keys = {item["key"] for item in volumes[0]["secret"]["items"]}
        self.assertNotIn("store.json", keys)
        self.assertEqual(keys, {"selection.json", "claude-token", "codex-auth.json"})
        self.assertTrue(mounts[0]["readOnly"])


class RunnerTests(unittest.TestCase):
    def test_codex_switches_preserve_refreshes_and_rotation_replaces_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = root / "projection"
            projection.mkdir()
            (projection / "codex-auth.json").write_text(AUTH)
            env = {**os.environ, "OMNIGENT_SUBSCRIPTIONS_DIR": str(projection),
                   "OMNIGENT_CODEX_AUTH_ROOT": str(root / "auth")}
            def run(account, revision):
                (projection / "selection.json").write_text(json.dumps({"codex": {"id": account, "revision": revision}}))
                return Path(subprocess.check_output([sys.executable, str(ROOT / "images/runner/subscriptions.py"), "codex"], env=env, text=True))
            personal = run("personal", "v1")
            self.assertEqual(personal.read_text(), AUTH)
            personal.write_text("refreshed-token")
            self.assertEqual(run("personal", "v1").read_text(), "refreshed-token")
            work = run("work", "v1")
            self.assertNotEqual(work, personal)
            self.assertEqual(run("personal", "v1").read_text(), "refreshed-token")
            self.assertEqual(run("personal", "v2").read_text(), AUTH)
            self.assertEqual(personal.stat().st_mode & 0o777, 0o600)

    def test_claude_reads_current_projection_and_legacy_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "OMNIGENT_SUBSCRIPTIONS_DIR": directory}
            command = [sys.executable, str(ROOT / "images/runner/subscriptions.py"), "claude"]
            self.assertEqual(subprocess.check_output(command, env=env, text=True), "")
            (root / "selection.json").write_text(json.dumps({"claude": {"id": "personal", "revision": "v1"}}))
            (root / "claude-token").write_text(TOKEN)
            self.assertEqual(subprocess.check_output(command, env=env, text=True), TOKEN)
            (root / "claude-token").write_text(WORK)
            self.assertEqual(subprocess.check_output(command, env=env, text=True), WORK)


if __name__ == "__main__":
    unittest.main()

class WrapperTests(unittest.TestCase):
    def test_codex_wrapper_switches_both_probe_and_session_homes(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = root / "projection"
            projection.mkdir()
            (projection / "codex-auth.json").write_text(AUTH)
            library = root / "library.sh"
            library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
            cli = root / "fake-codex.py"
            cli.write_text("import os,sys,json; from pathlib import Path; print(json.dumps({'auth':json.loads((Path(os.environ['CODEX_HOME'])/'auth.json').read_text()),'args':sys.argv[1:]}))")
            source = (ROOT / "images/runner/codex-wrapper.sh").read_text()
            source = source.replace('/usr/local/lib/omnigent/repo-env.sh', str(library)).replace('/usr/local/lib/omnigent/agent-instructions.sh', str(library))
            source = source.replace('node=/usr/local/bin/node', 'node=' + shlex.quote(sys.executable))
            source = source.replace('real_codex=/usr/local/bin/codex-real', 'real_codex=' + shlex.quote(str(cli)))
            source = source.replace('/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py', shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'images/runner/subscriptions.py')))
            wrapper = root / "codex-wrapper.sh"
            wrapper.write_text(source)
            for home in (root / "probe", root / "omnigent-codex-home-test"):
                home.mkdir()
                (home / "auth.json").write_text('{}')
                env = {**os.environ, "CODEX_HOME": str(home), "OMNIGENT_SUBSCRIPTIONS_DIR": str(projection), "OMNIGENT_CODEX_AUTH_ROOT": str(root / "saved"), "OMNIGENT_CODEX_BYPASS_APPROVALS": "1"}
                for account in ("personal", "work", "personal"):
                    (projection / "selection.json").write_text(json.dumps({"codex": {"id":account,"revision":"v1"}}))
                    result = json.loads(subprocess.check_output(["sh", str(wrapper), "app-server"], env=env, text=True))
                    self.assertEqual(result["auth"], json.loads(AUTH))
                    self.assertEqual((home / "auth.json").resolve(), root / "saved" / account / "auth.json")
                    self.assertEqual("--dangerously-bypass-approvals-and-sandbox" in result["args"], home.name.startswith("omnigent-codex-home-"))
                # A terminal reconnect after switching must keep the server's old account.
                (projection / "selection.json").write_text(json.dumps({"codex": {"id":"work","revision":"v1"}}))
                subprocess.check_output(["sh", str(wrapper), "--remote", "ws://127.0.0.1:1234"], env=env, text=True)
                self.assertEqual((home / "auth.json").resolve(), root / "saved" / "personal" / "auth.json")

    def test_codex_wrapper_keeps_the_link_a_running_codex_still_uses(self):
        import fcntl
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = root / "projection"
            projection.mkdir()
            (projection / "codex-auth.json").write_text(AUTH)
            library = root / "library.sh"
            library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
            cli = root / "fake-codex.py"
            cli.write_text("print('ok')")
            source = (ROOT / "images/runner/codex-wrapper.sh").read_text()
            source = source.replace('/usr/local/lib/omnigent/repo-env.sh', str(library)).replace('/usr/local/lib/omnigent/agent-instructions.sh', str(library))
            source = source.replace('node=/usr/local/bin/node', 'node=' + shlex.quote(sys.executable))
            source = source.replace('real_codex=/usr/local/bin/codex-real', 'real_codex=' + shlex.quote(str(cli)))
            source = source.replace('/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py', shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'images/runner/subscriptions.py')))
            wrapper = root / "codex-wrapper.sh"
            wrapper.write_text(source)
            home = root / "home"
            env = {**os.environ, "CODEX_HOME": str(home), "OMNIGENT_SUBSCRIPTIONS_DIR": str(projection), "OMNIGENT_CODEX_AUTH_ROOT": str(root / "saved")}
            def launch(account):
                (projection / "selection.json").write_text(json.dumps({"codex": {"id": account, "revision": "v1"}}))
                return subprocess.run(["sh", str(wrapper)], env=env, text=True, capture_output=True, check=True)
            launch("personal")
            self.assertEqual((home / "auth.json").resolve(), root / "saved" / "personal" / "auth.json")
            # A Codex started before the switch still holds its shared lock.
            with (home / ".omnigent-auth.lock").open("a") as running:
                fcntl.flock(running, fcntl.LOCK_SH)
                self.assertIn("previous subscription", launch("work").stderr)
                self.assertEqual((home / "auth.json").resolve(), root / "saved" / "personal" / "auth.json")
            launch("work")
            self.assertEqual((home / "auth.json").resolve(), root / "saved" / "work" / "auth.json")
            # An unwritable lock, like credential-status's read-only home, still starts Codex.
            (home / ".omnigent-auth.lock").unlink()
            (home / ".omnigent-auth.lock").mkdir()
            self.assertEqual(launch("personal").stdout, "ok\n")

    def test_codex_wrapper_records_the_account_its_codex_uses(self):
        import fcntl
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = root / "projection"
            projection.mkdir()
            (projection / "codex-auth.json").write_text(AUTH)
            library = root / "library.sh"
            library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
            cli = root / "fake-codex.py"
            cli.write_text("print('ok')")
            source = (ROOT / "images/runner/codex-wrapper.sh").read_text()
            source = source.replace('/usr/local/lib/omnigent/repo-env.sh', str(library)).replace('/usr/local/lib/omnigent/agent-instructions.sh', str(library))
            source = source.replace('node=/usr/local/bin/node', 'node=' + shlex.quote(sys.executable))
            source = source.replace('real_codex=/usr/local/bin/codex-real', 'real_codex=' + shlex.quote(str(cli)))
            source = source.replace('/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py', shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'images/runner/subscriptions.py')))
            source = source.replace('shared_auth=/mnt/codex-home/auth.json', 'shared_auth=' + shlex.quote(str(root / "shared-auth.json")))
            wrapper = root / "codex-wrapper.sh"
            wrapper.write_text(source)
            home = root / "omnigent-codex-home-test"
            record = home / "omnigent-subscription"
            env = {**os.environ, "CODEX_HOME": str(home), "OMNIGENT_SUBSCRIPTIONS_DIR": str(projection), "OMNIGENT_CODEX_AUTH_ROOT": str(root / "saved")}
            def launch(account):
                (projection / "selection.json").write_text(json.dumps({"codex": {"id": account, "revision": "v1"}}))
                subprocess.run(["sh", str(wrapper)], env=env, text=True, capture_output=True, check=True)
            launch("a" * 32)
            self.assertEqual(record.read_text(), "a" * 32)
            # A Codex that still holds the old account keeps it, and so does the record.
            with (home / ".omnigent-auth.lock").open("a") as running:
                fcntl.flock(running, fcntl.LOCK_SH)
                launch("b" * 32)
                self.assertEqual(record.read_text(), "a" * 32)
            launch("b" * 32)
            self.assertEqual(record.read_text(), "b" * 32)
            # Without an account in Settings, Codex uses the deployment's own login.
            (projection / "selection.json").unlink()
            (home / "auth.json").unlink()
            (root / "shared-auth.json").write_text(AUTH)
            subprocess.run(["sh", str(wrapper)], env=env, text=True, capture_output=True, check=True)
            self.assertEqual((home / "auth.json").resolve(), root / "shared-auth.json")
            self.assertEqual(record.read_text(), "")

    def test_claude_wrapper_records_the_account_in_its_bridge_directory(self):
        import shlex
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = root / "projection"
            projection.mkdir()
            library = root / "library.sh"
            library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
            cli = root / "claude-real"
            cli.write_text('#!/bin/sh\nprintf "%s" "${CLAUDE_CODE_OAUTH_TOKEN:-none}"\n')
            cli.chmod(0o755)
            source = (ROOT / "images/runner/claude-wrapper.sh").read_text()
            source = source.replace('/usr/local/lib/omnigent/repo-env.sh', str(library)).replace('/usr/local/lib/omnigent/agent-instructions.sh', str(library))
            source = source.replace('managed=/etc/claude-code/managed-settings.json', 'managed=' + shlex.quote(str(root / "managed.json")))
            source = source.replace('/usr/local/bin/claude-real', shlex.quote(str(cli)))
            source = source.replace('/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py', shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'images/runner/subscriptions.py')))
            wrapper = root / "claude-wrapper.sh"
            wrapper.write_text(source)
            bridge = root / "bridge"
            bridge.mkdir()
            record = bridge / "omnigent-subscription"
            env = {**os.environ, "OMNIGENT_SUBSCRIPTIONS_DIR": str(projection)}
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            def run(*args):
                return subprocess.run(["sh", str(wrapper), *args], env=env, text=True, capture_output=True, check=True).stdout
            settings = str(bridge / "claude-settings.json")
            self.assertEqual(run("--model", "opus", "--settings", settings), "none")
            self.assertEqual(record.read_text(), "")
            (projection / "selection.json").write_text(json.dumps({"claude": {"id": "a" * 32, "revision": "v1"}}))
            (projection / "claude-token").write_text(TOKEN)
            self.assertEqual(run("--settings=" + settings, "--model", "opus"), TOKEN)
            self.assertEqual(record.read_text(), "a" * 32)
            # Inline settings, as Omnigent's title helper passes, aren't a bridge directory.
            record.unlink()
            self.assertEqual(run("--settings", '{"apiKeyHelper": "x"}'), TOKEN)
            self.assertFalse(record.exists())
            # An unwritable bridge directory still starts Claude.
            self.assertEqual(run("--settings", str(root / "missing" / "claude-settings.json")), TOKEN)
