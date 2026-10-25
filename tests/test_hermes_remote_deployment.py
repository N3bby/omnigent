"""How the deployment installs the hermes-remote harness and configures it.

The harness itself is tested in test_hermes_remote_{client,turns,connection,executor}.py.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import ConfigError, load_environment, validate  # noqa: E402


def render_ci() -> tuple[str, str]:
    # A copy of ci under its own name: a --prepare-only render of ci itself
    # would delete the manifest.yaml that CI validates after the tests.
    name = "ci-hermes-remote"
    path = ROOT / "environments" / f"{name}.toml"
    generated = ROOT / ".generated" / name
    path.write_text((ROOT / "environments" / "ci.toml").read_text())
    try:
        subprocess.run(
            [str(ROOT / "scripts" / "render"), "--environment", name, "--prepare-only"],
            check=True, capture_output=True,
        )
        return (
            (generated / "omnigent-config.env").read_text(),
            (generated / "sandbox-config.yaml").read_text(),
        )
    finally:
        path.unlink()
        shutil.rmtree(generated, ignore_errors=True)


class HermesRemoteDeploymentTests(unittest.TestCase):
    def test_both_images_install_the_package(self) -> None:
        build = (ROOT / ".github" / "actions" / "build-images" / "action.yml").read_text()
        self.assertEqual(build.count("hermes-remote=packages/omnigent-hermes-remote"), 2)
        for image in ("runner", "server"):
            with self.subTest(image=image):
                dockerfile = (ROOT / "images" / image / "Dockerfile").read_text()
                self.assertIn("COPY --from=hermes-remote . /tmp/omnigent-hermes-remote/", dockerfile)
                self.assertIn("--no-deps /tmp/omnigent-hermes-remote", dockerfile)
        # The server starts runner Pods with this Python.
        runner = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("/opt/venv/bin/python3 -m pip install --no-cache-dir --no-deps", runner)

    def test_the_package_ships_only_its_own_module(self) -> None:
        pyproject = (ROOT / "packages" / "omnigent-hermes-remote" / "pyproject.toml").read_text()
        self.assertIn('only-include = ["omnigent/community/harness/hermes_remote"]', pyproject)
        self.assertIn(
            'hermes-remote = "omnigent.community.harness.hermes_remote.plugin:get_contribution"',
            pyproject,
        )
        package = ROOT / "packages" / "omnigent-hermes-remote" / "omnigent"
        # Core owns these namespace packages; shipping them would replace its files.
        for path in (package, package / "community", package / "community" / "harness"):
            self.assertFalse((path / "__init__.py").exists(), path)

    def test_runner_pods_get_the_ssh_target_and_pass_it_to_the_harness(self) -> None:
        env, sandbox = render_ci()

        self.assertIn("HERMES_REMOTE_SSH_HOST=hermes.tailnet.invalid\n", env)
        self.assertIn("HERMES_REMOTE_SSH_USER=hermes\n", env)
        self.assertIn("HERMES_REMOTE_SSH_PORT=2222\n", env)
        # The key comes from omnigent-creds, so it's passed through but not set here.
        self.assertIn(
            "OMNIGENT_RUNNER_ENV_PASSTHROUGH=HERMES_REMOTE_SSH_HOST,HERMES_REMOTE_SSH_USER,"
            "HERMES_REMOTE_SSH_PORT,HERMES_REMOTE_SSH_KEY\n",
            env,
        )
        self.assertNotIn("HERMES_REMOTE_SSH_KEY=", env)
        for name in (
            "HERMES_REMOTE_SSH_HOST", "HERMES_REMOTE_SSH_USER", "HERMES_REMOTE_SSH_PORT",
            "OMNIGENT_RUNNER_ENV_PASSTHROUGH",
        ):
            self.assertIn(f"      - {name}\n", sandbox)
        self.assertNotIn("HERMES_REMOTE_SSH_KEY", sandbox)

    def test_without_a_host_nothing_is_added(self) -> None:
        settings = {k: v for k, v in load_environment("ci").items() if not k.startswith("hermes_")}
        with mock.patch("config.load_environment", return_value=settings):
            values, _ = validate("ci")
        self.assertNotIn("hermes_remote_ssh_host", values)

    def test_bad_settings_are_refused(self) -> None:
        base = load_environment("ci")
        for change in (
            {"hermes_remote_ssh_host": "-oProxyCommand=sh"},
            {"hermes_remote_ssh_user": "-x"},
            {"hermes_remote_ssh_port": 70000},
            {"hermes_remote_ssh_port": "2222"},
        ):
            with self.subTest(change=change):
                with mock.patch("config.load_environment", return_value={**base, **change}):
                    with self.assertRaises(ConfigError):
                        validate("ci")
        no_host = {k: v for k, v in base.items() if k != "hermes_remote_ssh_host"}
        with mock.patch("config.load_environment", return_value=no_host):
            with self.assertRaisesRegex(ConfigError, "need hermes_remote_ssh_host"):
                validate("ci")

    def test_the_setup_task_stores_the_key_in_the_runner_secret(self) -> None:
        script = (ROOT / "scripts" / "setup-hermes-ssh-key").read_text()
        self.assertIn("patch secret omnigent-creds -n omnigent-sandboxes", script)
        self.assertIn("HERMES_REMOTE_SSH_KEY", script)
        self.assertIn('permitopen="127.0.0.1:8642"', script)
        self.assertIn("setup-hermes-ssh-key", (ROOT / ".mise.toml").read_text())


if __name__ == "__main__":
    unittest.main()
