"""A repository's setup script and agent instructions, on the runner.

Runs images/runner/repo-env/repo-setup.sh and agent-instructions.sh against
Secrets laid out the way the kubelet mounts them, in a temporary directory.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ENV = ROOT / "images" / "runner" / "repo-env"
SERVICE = REPO_ENV / "repo-setup.sh"
INSTRUCTIONS = REPO_ENV / "agent-instructions.sh"

ROADPASS = ("omnigent-repo-env-kunlabora-roadpass-1a2b3c4d", "github.com/kunlabora/roadpass", "roadpass")
OTHER = ("omnigent-repo-env-kunlabora-other-5e6f7a8b", "github.com/kunlabora/other", "other")


class RunnerFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env_dir = self.tmp / "repo-env"
        self.run_dir = self.tmp / "run"
        self.state = self.tmp / "state"
        self.workspace = self.tmp / "workspace"
        self.env_dir.mkdir()
        self.run_dir.mkdir()
        self.base = self.tmp / "agent-instructions.md"
        self.base.write_text("# Omnigent runner\n\nThe runner's own instructions.\n")

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": os.environ["PATH"],
            "OMNIGENT_REPO_ENV_DIR": str(self.env_dir),
            "OMNIGENT_REPO_SETUP_RUN": str(self.run_dir),
            "OMNIGENT_REPO_SETUP_STATE": str(self.state),
            "OMNIGENT_AGENT_INSTRUCTIONS": str(self.base),
            **extra,
        }

    def mount(self, repo: tuple[str, str, str], files: dict[str, str]) -> None:
        """Lay out a repository's Secret the way the kubelet mounts it."""
        secret = self.env_dir / repo[0]
        data = secret / "..2026_10_11_12_00_00.000000000"
        data.mkdir(parents=True)
        for name, value in files.items():
            (data / name).write_text(value)
            (data / name).chmod(0o440)
            (secret / name).symlink_to(f"..data/{name}")
        (secret / "..data").symlink_to(data.name)

    def checkouts(self, *repos: tuple[str, str, str]) -> None:
        """The list repo-setup-profile.sh writes from OMNIGENT_REPO_CHECKOUTS."""
        lines = []
        for secret, key, name in repos:
            (self.workspace / name).mkdir(parents=True, exist_ok=True)
            lines.append(f"{secret}\t{key}\t{self.workspace / name}\n")
        (self.run_dir / "checkouts").write_text("".join(lines))


class SetupServiceTests(RunnerFixture):
    def run_service(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(SERVICE)], env=self.env(**extra), check=True, capture_output=True, text=True,
            timeout=60,
        )

    def test_runs_each_script_once_in_its_checkout(self) -> None:
        self.mount(ROADPASS, {
            "TOKEN": "abc",
            "setup.sh": 'pwd\necho "args:$#"\nread -r line || echo "no input"\necho done > made-by-setup\n',
        })
        self.mount(OTHER, {"TOKEN": "x"})  # variables only: nothing to run
        self.checkouts(ROADPASS, OTHER)
        out = self.run_service(OMNIGENT_HOST_TOKEN="launch-token").stdout
        self.assertIn("running the setup script of github.com/kunlabora/roadpass", out)
        self.assertNotIn("kunlabora/other", out)
        checkout = self.workspace / "roadpass"
        self.assertEqual((checkout / "made-by-setup").read_text(), "done\n")
        log = (self.state / "roadpass.log").read_text()
        self.assertIn(f"\n{checkout}\nargs:0\nno input\n", log)
        self.assertRegex(log, r"# omnigent-repo-setup: exit status 0, \S+\n$")
        self.assertEqual((self.state / "roadpass.exit").read_text(), "0\n")
        self.assertFalse((self.state / "other.exit").exists())
        self.assertTrue((self.run_dir / "done").exists())

        # The next Pod start (a wake) doesn't run it again.
        (checkout / "made-by-setup").unlink()
        (self.run_dir / "done").unlink()
        out = self.run_service().stdout
        self.assertIn("already ran its setup script", out)
        self.assertFalse((checkout / "made-by-setup").exists())

    def test_records_a_failure_and_keeps_the_pod_environment_out(self) -> None:
        self.mount(ROADPASS, {"setup.sh": 'echo "token:${OMNIGENT_HOST_TOKEN-unset}"\necho oops >&2\nexit 3\n'})
        self.checkouts(ROADPASS)
        self.run_service(OMNIGENT_HOST_TOKEN="launch-token")
        log = (self.state / "roadpass.log").read_text()
        self.assertIn("token:unset\noops\n", log)
        self.assertNotIn("launch-token", log)
        self.assertEqual((self.state / "roadpass.exit").read_text(), "3\n")

    def test_a_script_with_a_shebang_runs_with_its_interpreter(self) -> None:
        self.mount(ROADPASS, {"setup.sh": "#!/usr/bin/env python3\nimport os\nprint('python in', os.getcwd())\n"})
        self.checkouts(ROADPASS)
        self.run_service()
        self.assertIn(f"python in {self.workspace / 'roadpass'}\n", (self.state / "roadpass.log").read_text())

    @unittest.skipUnless(Path("/etc/profile.d/omnigent-repo-env.sh").exists(), "needs the runner image's profile")
    def test_a_script_gets_the_repository_variables(self) -> None:
        self.mount(ROADPASS, {"GOVFLANDERS_NPM_TOKEN": "abc123", "setup.sh": 'echo "npm:$GOVFLANDERS_NPM_TOKEN"\n'})
        self.checkouts(ROADPASS)
        self.run_service()
        self.assertIn("npm:abc123\n", (self.state / "roadpass.log").read_text())

    def test_without_checkouts_it_only_marks_itself_done(self) -> None:
        self.run_service()
        self.assertTrue((self.run_dir / "done").exists())
        self.assertFalse(self.state.exists())

    def test_one_copy_at_a_time(self) -> None:
        self.mount(ROADPASS, {"setup.sh": "sleep 2\n"})
        self.checkouts(ROADPASS)
        first = subprocess.Popen(["sh", str(SERVICE)], env=self.env(), stdout=subprocess.DEVNULL)
        self.addCleanup(first.wait)
        while not (self.state / "roadpass.log").exists():
            time.sleep(0.05)
        second = self.run_service()
        self.assertEqual(second.stdout, "")
        self.assertFalse((self.run_dir / "done").exists())
        first.wait(timeout=30)
        self.assertTrue((self.run_dir / "done").exists())


class AgentInstructionsTests(RunnerFixture):
    def shell(self, script: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", "-c", f"set -eu; . {INSTRUCTIONS}; {script}"],
            env=self.env(**extra), check=True, capture_output=True, text=True, timeout=60,
        )

    def write(self) -> str:
        target = self.tmp / "CLAUDE.md"
        self.shell(f"omnigent_agent_instructions {target}")
        return target.read_text()

    def test_without_repository_settings_the_agent_gets_the_runner_instructions(self) -> None:
        self.assertEqual(self.write(), self.base.read_text())
        self.mount(ROADPASS, {"TOKEN": "abc"})
        self.checkouts(ROADPASS)
        self.assertEqual(self.write(), self.base.read_text())

    def test_appends_the_instructions_and_says_where_they_come_from(self) -> None:
        self.mount(ROADPASS, {"TOKEN": "abc", "agent-instructions.md": "Use pnpm, never npm.\nRun `mise run dev`."})
        self.checkouts(ROADPASS)
        text = self.write()
        self.assertTrue(text.startswith(self.base.read_text()))
        appended = text[len(self.base.read_text()):]
        self.assertIn("\n# Repository settings from Omnigent\n", appended)
        self.assertIn("Omnigent appended this to the runner's instructions", appended)
        self.assertIn("don't copy it into a repository or commit it", appended)
        self.assertIn("\n## github.com/kunlabora/roadpass\n", appended)
        self.assertIn(f"Checked out at `{self.workspace / 'roadpass'}`.", appended)
        self.assertIn("These are the user's own instructions for this repository.", appended)
        self.assertTrue(appended.endswith("\nUse pnpm, never npm.\nRun `mise run dev`.\n"))
        self.assertNotIn("Setup script", appended)
        self.assertNotIn("abc", appended)

    def test_tells_the_agent_how_the_setup_script_went(self) -> None:
        self.mount(ROADPASS, {"setup.sh": "mise install\n"})
        self.mount(OTHER, {"setup.sh": "false\n", "agent-instructions.md": "Other rules.\n"})
        self.checkouts(ROADPASS, OTHER)
        log = self.state / "roadpass.log"
        text = self.write()
        self.assertIn("It is still running, so what it sets up may not be ready yet.", text)
        self.assertIn(f"`{self.state / 'roadpass.exit'}` gets its exit status", text)

        self.state.mkdir()
        (self.state / "roadpass.exit").write_text("0\n")
        (self.state / "other.exit").write_text("1\n")
        text = self.write()
        self.assertIn(f"Omnigent runs the setup script `{self.env_dir / ROADPASS[0] / 'setup.sh'}` in the checkout once", text)
        self.assertIn(f"It finished successfully. Its output is in `{log}`.", text)
        self.assertIn("It failed with exit status 1, so what it sets up may be missing.", text)
        # Each repository's section has its own instructions and script.
        roadpass, other = text.split("\n## github.com/kunlabora/roadpass\n")[1].split("\n## github.com/kunlabora/other\n")
        self.assertNotIn("Other rules.", roadpass)
        self.assertIn("\n### Instructions\n", other)
        self.assertLess(other.index("Other rules."), other.index("### Setup script"))

    def test_rewrites_the_file_only_when_it_changes(self) -> None:
        self.mount(ROADPASS, {"agent-instructions.md": "Rules.\n"})
        self.checkouts(ROADPASS)
        target = self.tmp / "CLAUDE.md"
        self.write()
        before = target.stat().st_ino
        self.write()
        self.assertEqual(target.stat().st_ino, before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if "omnigent." in p.name), [])

    def test_a_missing_runner_file_leaves_the_target_alone(self) -> None:
        target = self.tmp / "CLAUDE.md"
        target.write_text("as built\n")
        self.shell(f"omnigent_agent_instructions {target}", OMNIGENT_AGENT_INSTRUCTIONS=str(self.tmp / "missing"))
        self.assertEqual(target.read_text(), "as built\n")

    def test_an_unwritable_target_fails_quietly(self) -> None:
        result = self.shell(f"omnigent_agent_instructions {self.tmp / 'missing-dir' / 'CLAUDE.md'} || echo failed")
        self.assertEqual(result.stdout, "failed\n")
        self.assertEqual(result.stderr, "")

    def test_waits_for_a_pending_setup_script_but_not_forever(self) -> None:
        start = time.monotonic()
        self.shell("omnigent_wait_for_repo_setup", OMNIGENT_REPO_SETUP_WAIT="5")
        self.assertLess(time.monotonic() - start, 1)  # no checkouts: nothing to wait for

        self.mount(ROADPASS, {"setup.sh": "true\n"})
        self.checkouts(ROADPASS)
        start = time.monotonic()
        result = self.shell("omnigent_wait_for_repo_setup", OMNIGENT_REPO_SETUP_WAIT="2")
        self.assertGreaterEqual(time.monotonic() - start, 2)
        self.assertIn("Waiting up to 2s for the repository's setup script", result.stderr)

        (self.run_dir / "done").touch()
        start = time.monotonic()
        result = self.shell("omnigent_wait_for_repo_setup", OMNIGENT_REPO_SETUP_WAIT="5")
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(result.stderr, "")


class ImageTests(unittest.TestCase):
    def test_the_image_installs_the_scripts_and_the_wrappers_use_them(self) -> None:
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        for line in (
            "COPY --chmod=0755 repo-env/repo-setup.sh /usr/local/bin/omnigent-repo-setup\n",
            "COPY repo-env/repo-setup-profile.sh /etc/profile.d/omnigent-repo-setup.sh\n",
            "COPY repo-env/agent-instructions.sh /usr/local/lib/omnigent/agent-instructions.sh\n",
            "COPY --chmod=0644 agent-instructions.md /usr/local/share/omnigent/agent-instructions.md\n",
        ):
            self.assertIn(line, dockerfile)
        for wrapper, target in (("claude-wrapper.sh", "/etc/claude-code/CLAUDE.md"),
                                ("codex-wrapper.sh", "/opt/codex-home/AGENTS.md")):
            with self.subTest(wrapper=wrapper):
                text = (ROOT / "images" / "runner" / wrapper).read_text()
                load = text.index("\n. /usr/local/lib/omnigent/agent-instructions.sh\n")
                wait = text.index("\nomnigent_wait_for_repo_setup\n")
                write = text.index(f"\nomnigent_agent_instructions {target} \\\n")
                self.assertLess(text.index("\n. /usr/local/lib/omnigent/repo-env.sh\n"), load)
                self.assertTrue(load < wait < write < text.index("exec "))

    def test_the_server_tells_the_pod_where_the_checkouts_are(self) -> None:
        patch = (ROOT / "images" / "server" / "patches" / "0012-repo-checkouts.patch").read_text()
        self.assertIn("+from omnigent_repo_env import checkouts_env as _repo_env_checkouts\n", patch)
        self.assertIn("+        host_env.append(repo_checkouts)\n", patch)
        profile = (REPO_ENV / "repo-setup-profile.sh").read_text()
        self.assertIn("OMNIGENT_REPO_CHECKOUTS", profile)
        self.assertIn("/run/omnigent-repo-setup/checkouts", profile)


if __name__ == "__main__":
    unittest.main()
