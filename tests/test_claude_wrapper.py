from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REAL_CLAUDE = shutil.which("claude-real")


class OnboardingSeedTests(unittest.TestCase):
    """The wrapper creates Claude Code's global config with onboarding done."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def wrapper(self, claude: str) -> Path:
        library = self.root / "library.sh"
        library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
        source = (ROOT / "images/runner/claude-wrapper.sh").read_text()
        source = source.replace('/usr/local/lib/omnigent/repo-env.sh', str(library)).replace('/usr/local/lib/omnigent/agent-instructions.sh', str(library))
        source = source.replace('managed=/etc/claude-code/managed-settings.json', 'managed=' + shlex.quote(str(self.root / "managed-settings.json")))
        source = source.replace('/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py', shlex.quote(sys.executable) + ' ' + shlex.quote(str(ROOT / 'images/runner/subscriptions.py')))
        source = source.replace('/usr/local/bin/claude-real', shlex.quote(claude))
        wrapper = self.root / "claude-wrapper.sh"
        wrapper.write_text(source)
        return wrapper

    def fake_claude(self) -> str:
        # Prints the global config it would load, as Claude Code reads it.
        cli = self.root / "fake-claude"
        cli.write_text("#!/bin/sh\ncat \"${CLAUDE_CONFIG_DIR:-$HOME}/.claude.json\" 2>/dev/null || echo missing\n")
        cli.chmod(0o755)
        return str(cli)

    def run_wrapper(self, *args: str, claude: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "OMNIGENT_SUBSCRIPTIONS_DIR": str(self.root / "subscriptions"),
            **env,
        }
        return subprocess.run(["sh", str(self.wrapper(claude or self.fake_claude())), *args],
                              env=environment, text=True, capture_output=True, check=True, timeout=60)

    def test_a_new_home_gets_the_config_before_claude_starts(self) -> None:
        result = self.run_wrapper()
        self.assertEqual(json.loads(result.stdout), {"hasCompletedOnboarding": True})
        config = self.home / ".claude.json"
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".claude.json"])
        self.assertEqual(result.stderr, "")

    def test_an_existing_config_is_left_alone(self) -> None:
        config = self.home / ".claude.json"
        config.write_text('{"theme": "dark"}\n')
        result = self.run_wrapper()
        self.assertEqual(result.stdout, '{"theme": "dark"}\n')
        self.assertEqual(config.read_text(), '{"theme": "dark"}\n')

    def test_claudes_starting_together_all_see_the_key(self) -> None:
        wrapper = self.wrapper(self.fake_claude())
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.home),
                       "OMNIGENT_SUBSCRIPTIONS_DIR": str(self.root / "subscriptions")}
        runs = [subprocess.Popen(["sh", str(wrapper)], env=environment, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
        for run in runs:
            out, err = run.communicate(timeout=60)
            self.assertEqual((run.returncode, json.loads(out), err), (0, {"hasCompletedOnboarding": True}, ""))
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".claude.json"])

    def test_it_follows_claude_config_dir(self) -> None:
        config_dir = self.root / "config"
        config_dir.mkdir()
        self.run_wrapper(CLAUDE_CONFIG_DIR=str(config_dir))
        self.assertEqual(json.loads((config_dir / ".claude.json").read_text()), {"hasCompletedOnboarding": True})
        self.assertFalse((self.home / ".claude.json").exists())

    def test_claude_still_starts_when_the_config_cant_be_written(self) -> None:
        result = self.run_wrapper(CLAUDE_CONFIG_DIR=str(self.root / "missing"))
        self.assertEqual(result.stdout, "missing\n")
        self.assertIn("could not create", result.stderr)

    @unittest.skipIf(REAL_CLAUDE is None, "needs the runner image's claude-real")
    def test_claude_keeps_the_key_it_starts_with(self) -> None:
        # Without the wrapper, `claude auth status` in a new home writes a
        # config without the key; through it, the key survives Claude's write.
        bare = self.root / "bare"
        bare.mkdir()
        subprocess.run([REAL_CLAUDE, "auth", "status"], cwd=bare, capture_output=True, timeout=60,
                       env={"PATH": os.environ["PATH"], "HOME": str(bare), "DISABLE_AUTOUPDATER": "1"})
        self.assertNotIn("hasCompletedOnboarding", json.loads((bare / ".claude.json").read_text()))
        subprocess.run(["sh", str(self.wrapper(REAL_CLAUDE)), "auth", "status"], cwd=self.home,
                       capture_output=True, timeout=60,
                       env={"PATH": os.environ["PATH"], "HOME": str(self.home),
                            "OMNIGENT_SUBSCRIPTIONS_DIR": str(self.root / "subscriptions")})
        config = json.loads((self.home / ".claude.json").read_text())
        self.assertIn("firstStartTime", config)
        self.assertIs(config.get("hasCompletedOnboarding"), True)


if __name__ == "__main__":
    unittest.main()
