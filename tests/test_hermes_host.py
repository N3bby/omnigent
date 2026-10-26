"""The always-on host for Hermes sessions (docs/features/hermes-host.md).

Tests images/server/omnigent_hermes_host.py on its own, and how the deployment
renders and signs in the host. The create route and the automations' host pick
are tested against a patched upstream checkout
(tests/server/integration/test_hermes_host.py in 0024-hermes-host.patch).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
sys.path.insert(0, str(ROOT / "scripts"))
import omnigent_hermes_host as hermes_host  # noqa: E402
from config import ConfigError, load_environment, validate  # noqa: E402

HOST_ID = "6d1c8e0a4b2f4e8f9a7b3c5d2e1f0a9b"
ENV = {hermes_host.HOST_ID_ENV_VAR: HOST_ID}


def render_module() -> Any:
    from importlib.machinery import SourceFileLoader

    return SourceFileLoader("render", str(ROOT / "scripts" / "render")).load_module()


def render(settings: dict[str, Any] | None = None) -> dict[str, str]:
    # A copy of ci under its own name: a render of ci itself would replace the
    # manifest CI validates after the tests.
    name = "ci-hermes-host"
    path = ROOT / "environments" / f"{name}.toml"
    generated = ROOT / ".generated" / name
    text = (ROOT / "environments" / "ci.toml").read_text()
    for key, value in (settings or {}).items():
        text += f"{key} = {value}\n"
    path.write_text(text)
    try:
        subprocess.run(
            [str(ROOT / "scripts" / "render"), "--environment", name, "--prepare-only"],
            check=True, capture_output=True,
        )
        return {
            "env": (generated / "omnigent-config.env").read_text(),
            "dynamic": (generated / "dynamic.yaml").read_text(),
            "platform": (generated / "platform" / "kustomization.yaml").read_text(),
        }
    finally:
        path.unlink()
        shutil.rmtree(generated, ignore_errors=True)


class ModuleTests(unittest.TestCase):
    def test_the_host_id_is_normalized_like_host_rows(self) -> None:
        dashed = "6D1C8E0A-4B2F-4E8F-9A7B-3C5D2E1F0A9B"
        self.assertEqual(hermes_host.host_id({hermes_host.HOST_ID_ENV_VAR: dashed}), HOST_ID)
        self.assertTrue(hermes_host.is_hermes_host(dashed, ENV))
        self.assertFalse(hermes_host.is_hermes_host("0" * 32, ENV))
        self.assertFalse(hermes_host.is_hermes_host(None, ENV))

    def test_without_a_valid_id_nothing_changes(self) -> None:
        for env in ({}, {hermes_host.HOST_ID_ENV_VAR: " "}, {hermes_host.HOST_ID_ENV_VAR: "nope"}):
            with self.subTest(env=env):
                self.assertIsNone(hermes_host.host_id(env))
                self.assertFalse(hermes_host.is_hermes_host(HOST_ID, env))
                self.assertIsNone(
                    hermes_host.refusal(requested_host_id=HOST_ID, harness="codex", environ=env)
                )

    def test_the_host_refuses_other_agents(self) -> None:
        self.assertIn(
            "only runs Hermes sessions",
            hermes_host.refusal(requested_host_id=HOST_ID, harness="codex-native", environ=ENV),
        )
        self.assertIsNotNone(hermes_host.refusal(requested_host_id=HOST_ID, harness=None, environ=ENV))
        self.assertIsNone(
            hermes_host.refusal(requested_host_id=HOST_ID, harness=hermes_host.HARNESS, environ=ENV)
        )
        self.assertIsNone(
            hermes_host.refusal(requested_host_id="0" * 32, harness="codex-native", environ=ENV)
        )

class DeploymentTests(unittest.TestCase):
    def test_the_server_and_the_pod_share_a_fixed_host_id(self) -> None:
        rendered = render()
        server_id = re.search(r"^OMNIGENT_HERMES_HOST_ID=([0-9a-f]{32})$", rendered["env"], re.M)
        assert server_id is not None, rendered["env"]
        self.assertEqual(render()["env"], rendered["env"])
        dynamic = rendered["dynamic"]
        self.assertIn("name: omnigent-hermes-host\n  namespace: omnigent-sandboxes", dynamic)
        self.assertIn(
            f'- name: OMNIGENT_HOST_ID\n              value: "{server_id.group(1)}"', dynamic
        )
        # Rewritten on admission like runner Pods, which needs a container
        # named host with a securityContext.
        self.assertIn("omnigent.ai/role: sandbox-host", dynamic)
        self.assertIn("- name: host\n", dynamic)
        self.assertIn("securityContext: {}", dynamic)
        self.assertIn('command: ["bash", "-lc", "exec omnigent-hermes-host"]', dynamic)
        # The sign-in comes from its own Secret, never omnigent-creds.
        self.assertIn(
            "name: OMNIGENT_REFRESH_TOKEN\n              valueFrom:\n                secretKeyRef:\n"
            "                  name: omnigent-hermes-host\n                  key: refresh-token",
            dynamic,
        )
        self.assertIn("- name: HERMES_REMOTE_SSH_HOST\n", dynamic)
        self.assertIn("type: Recreate", dynamic)

    def test_the_host_fits_the_runner_quotas_spare_room(self) -> None:
        # Not changing the quota keeps `mise run bootstrap` out of turning it on.
        self.assertEqual(render()["platform"], render({"hermes_host": "false"})["platform"])
        # The quota keeps 500m, 512Mi and 1Gi of requests, and 3 CPUs, 3Gi and
        # 1Gi of limits, beyond the runners'.
        resources = render_module().HERMES_HOST_RESOURCES
        self.assertEqual(resources["requests"], {"cpu": "100m", "memory": "256Mi", "ephemeral-storage": "256Mi"})
        self.assertEqual(resources["limits"], {"cpu": "2", "memory": "2Gi", "ephemeral-storage": "1Gi"})

    def test_hermes_host_false_leaves_it_out(self) -> None:
        rendered = render({"hermes_host": "false"})
        self.assertNotIn("OMNIGENT_HERMES_HOST_ID", rendered["env"])
        self.assertNotIn("omnigent-hermes-host", rendered["dynamic"])

    def test_it_needs_hermes(self) -> None:
        base = {k: v for k, v in load_environment("ci").items() if not k.startswith("hermes_")}
        with mock.patch("config.load_environment", return_value=base):
            self.assertIs(validate("ci")[0]["hermes_host"], False)
        with mock.patch("config.load_environment", return_value={**base, "hermes_host": True}):
            with self.assertRaisesRegex(ConfigError, "hermes_host needs hermes_remote_ssh_host"):
                validate("ci")
        full = load_environment("ci")
        with mock.patch("config.load_environment", return_value={**full, "hermes_host": "yes"}):
            with self.assertRaisesRegex(ConfigError, "hermes_host must be true or false"):
                validate("ci")

    def test_the_images_carry_the_pieces(self) -> None:
        runner = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("COPY --chmod=0755 hermes-host.sh /usr/local/bin/omnigent-hermes-host", runner)
        server = (ROOT / "images" / "server" / "Dockerfile").read_text()
        self.assertIn("COPY omnigent_hermes_host.py /tmp/omnigent-modules/", server)
        self.assertIn('"$purelib/omnigent_hermes_host.py"', server)

    def test_the_pod_waits_without_a_sign_in_and_keeps_it_from_runners(self) -> None:
        script = (ROOT / "images" / "runner" / "hermes-host.sh").read_text()
        self.assertIn("exec sleep infinity", script)
        self.assertLess(script.index("unset OMNIGENT_REFRESH_TOKEN"), script.index("exec omnigent host"))
        self.assertIn("--non-interactive", script)

    def test_the_setup_task_signs_in_and_restarts_the_host(self) -> None:
        script = (ROOT / "scripts" / "setup-hermes-host").read_text()
        self.assertIn('"${server}/auth/login"', script)
        self.assertIn("issue_refresh: true", script)
        self.assertIn('"${server}/oauth/revoke"', script)
        self.assertIn('"refresh-token": .refresh_token', script)
        self.assertIn("rollout restart deployment/omnigent-hermes-host", script)
        # The password goes through stdin, never a command line.
        self.assertNotIn('--arg password', script)
        self.assertIn("setup-hermes-host", (ROOT / ".mise.toml").read_text())


if __name__ == "__main__":
    unittest.main()
