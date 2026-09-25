from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import load_environment, load_versions, validate  # noqa: E402


class ConfigurationTests(unittest.TestCase):
    def test_versions_are_complete_and_pinned(self) -> None:
        versions = load_versions()
        self.assertRegex(versions["omnigent_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(versions["omnigent_host_base_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["omnigent_server_base_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["vault_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("latest", versions.values())

    def test_ci_environment_encodes_accepted_policies(self) -> None:
        values, _ = validate("ci")
        self.assertEqual(values["runner_network_policy"], "unrestricted")
        self.assertFalse(values["backups_enabled"])
        self.assertTrue(values["claude_bypass_permissions"])

    def test_runner_lock_matches_authoritative_versions(self) -> None:
        versions = load_versions()
        package = json.loads((ROOT / "images" / "runner" / "package.json").read_text())
        self.assertEqual(package["dependencies"]["@anthropic-ai/claude-code"], versions["claude_code"])
        self.assertEqual(package["dependencies"]["@openai/codex"], versions["codex_cli"])
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn(versions["omnigent_host_base_digest"], dockerfile)
        server_dockerfile = (ROOT / "images" / "server" / "Dockerfile").read_text()
        self.assertIn(versions["omnigent_server_base_digest"], server_dockerfile)
        self.assertIn(versions["hvac"], server_dockerfile)

    def test_render_has_no_secret_or_mutable_application_image(self) -> None:
        subprocess.run([str(ROOT / "scripts" / "render"), "--environment", "ci"], check=True)
        manifest = (ROOT / ".generated" / "ci" / "manifest.yaml").read_text()
        self.assertNotIn("kind: Secret\n", manifest)
        self.assertNotIn(":latest", manifest)
        self.assertNotIn("kind: NetworkPolicy", manifest)
        self.assertIn("@sha256:", manifest)
        self.assertIn("ephemeral-storage", manifest)
        self.assertIn("memory: 2Gi", manifest)
        self.assertIn("name: vault", manifest)
        self.assertIn(f"hashicorp/vault@{load_versions()['vault_digest']}", manifest)
        for line in manifest.splitlines():
            if line.lstrip().startswith("image:"):
                self.assertIn("@sha256:", line)

    def test_production_contains_no_secret_values(self) -> None:
        text = (ROOT / "environments" / "production.toml").read_text().lower()
        for forbidden in ("password", "oauth_token", "client_secret", "git_token"):
            self.assertNotIn(f"{forbidden} =", text)

    def test_remote_apply_can_adopt_existing_resources(self) -> None:
        apply_script = (ROOT / "scripts" / "apply-manifest").read_text()
        self.assertIn("--prune", apply_script)
        self.assertNotIn("--server-side", apply_script)
        cert_tasks = (ROOT / "ansible" / "roles" / "cert_manager" / "tasks" / "main.yml").read_text()
        self.assertIn("--force-conflicts", cert_tasks)
        self.assertIn("kubectl, wait, --for=condition=Available, deployment, --all", cert_tasks)
        self.assertNotIn("rollout, status, deployment, --all", cert_tasks)

    def test_github_app_setup_is_a_deployed_operator_task(self) -> None:
        helper = (ROOT / "scripts" / "setup-github-app").read_text()
        self.assertIn("OMNIGENT_GITHUB_APP_CLIENT_SECRET", helper)
        self.assertIn("OMNIGENT_CREDENTIAL_CIPHER", helper)
        self.assertIn("vault-bootstrap", helper)
        self.assertNotIn("PRIVATE_KEY", helper)
        tasks = (ROOT / ".mise.toml").read_text()
        self.assertIn("[tasks.setup-github-app]", tasks)

    def test_ci_uses_mise_and_matches_published_architecture(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
        self.assertIn("jdx/mise-action@v4", workflow)
        self.assertIn("mise run test", workflow)
        self.assertNotIn("make bootstrap", workflow)
        preflight = (ROOT / "ansible" / "roles" / "preflight" / "tasks" / "main.yml").read_text()
        self.assertIn("ansible_architecture == 'x86_64'", preflight)


if __name__ == "__main__":
    unittest.main()
