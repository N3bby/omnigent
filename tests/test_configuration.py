from __future__ import annotations

import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config import (  # noqa: E402
    MIN_RUNNER_SUSPEND_WINDOW_SECONDS, ConfigError, load_environment, load_versions, validate,
)


class ConfigurationTests(unittest.TestCase):
    def test_versions_are_complete_and_pinned(self) -> None:
        versions = load_versions()
        self.assertRegex(versions["omnigent_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(versions["omnigent_host_base_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["omnigent_server_base_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["vault_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["web_builder_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(versions["agent_sandbox_manifest_sha256"], r"^[0-9a-f]{64}$")
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
        self.assertIn(
            f"node:{versions['web_builder']}@{versions['web_builder_digest']}", server_dockerfile
        )
        self.assertIn(f"PNPM_VERSION={versions['pnpm']}", server_dockerfile)
        for key in ("build", "download_url", "sha256", "path"):
            self.assertIn(f"JETBRAINS_IDEA_{key.upper()}={versions['jetbrains_idea_' + key]}\n", dockerfile)
        self.assertIn(f"JETBRAINS_IDEA_PATH={versions['jetbrains_idea_path']}\n", server_dockerfile)
        self.assertIn(f"OMNIGENT_COMMIT={versions['omnigent_commit']}", server_dockerfile)
        self.assertIn(f"TAILSCALE_VERSION={versions['tailscale']}\n", dockerfile)
        self.assertIn(f"TAILSCALE_SHA256={versions['tailscale_sha256']}\n", dockerfile)
        self.assertIn(f"MISE_VERSION={versions['mise']}\n", dockerfile)
        self.assertIn(f"MISE_SHA256={versions['mise_sha256']}\n", dockerfile)

    def test_project_runtimes_come_first_but_not_for_the_agent_clis(self) -> None:
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        # Sorts after the base image's omnigent-venv.sh, so its shims stay first.
        self.assertIn("mise/mise-profile.sh /etc/profile.d/zz-omnigent-mise.sh\n", dockerfile)
        self.assertIn("agent-instructions.md /etc/claude-code/CLAUDE.md\n", dockerfile)
        self.assertIn("agent-instructions.md /opt/codex-home/AGENTS.md\n", dockerfile)
        profile = (ROOT / "images" / "runner" / "mise" / "mise-profile.sh").read_text()
        self.assertIn("export MISE_DATA_DIR=/home/omnigent/", profile)
        wrapper = (ROOT / "images" / "runner" / "codex-wrapper.sh").read_text()
        self.assertNotRegex(wrapper, r'exec "\$real_codex"')

    def test_render_has_no_secret_or_mutable_application_image(self) -> None:
        subprocess.run([str(ROOT / "scripts" / "render"), "--environment", "ci"], check=True)
        manifest = (ROOT / ".generated" / "ci" / "manifest.yaml").read_text()
        self.assertNotIn("kind: Secret\n", manifest)
        self.assertNotIn(":latest", manifest)
        self.assertNotIn("kind: NetworkPolicy", manifest)
        self.assertIn("@sha256:", manifest)
        self.assertIn("ephemeral-storage", manifest)
        self.assertIn("memory: 512Mi", manifest)
        self.assertIn("name: vault", manifest)
        self.assertIn(f"hashicorp/vault@{load_versions()['vault_digest']}", manifest)
        for line in manifest.splitlines():
            if line.lstrip().startswith("image:"):
                self.assertIn("@sha256:", line)

    def test_runners_share_only_the_codex_login(self) -> None:
        subprocess.run([str(ROOT / "scripts" / "render"), "--environment", "ci"], check=True)
        env = (ROOT / ".generated" / "ci" / "omnigent-config.env").read_text()
        self.assertIn("CODEX_HOME=/opt/codex-home\n", env)
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("ln -s /mnt/codex-home/auth.json /opt/codex-home/auth.json", dockerfile)
        self.assertIn("codex-config.toml /opt/codex-home/config.toml", dockerfile)

    def test_idle_runners_suspend_and_keep_their_home(self) -> None:
        subprocess.run([str(ROOT / "scripts" / "render"), "--environment", "ci"], check=True)
        values, _ = validate("ci")
        generated = ROOT / ".generated" / "ci"
        sandbox = (generated / "sandbox-config.yaml").read_text()
        self.assertIn("  provider: agent_sandbox\n", sandbox)
        agent_idle = values["runner_agent_idle_seconds"]
        self.assertIn(f"  keep_warm_s: {agent_idle}\n", sandbox)
        env = (generated / "omnigent-config.env").read_text()
        window = values["runner_idle_shutdown_seconds"] - agent_idle
        self.assertIn(f"OMNIGENT_AGENT_SANDBOX_SHUTDOWN_WINDOW_S={window}\n", env)
        self.assertIn(f"OMNIGENT_AGENT_SANDBOX_WORKSPACE_SIZE={values['runner_home_limit']}\n", env)
        self.assertIn("OMNIGENT_AGENT_SANDBOX_STORAGE_CLASS=local-path\n", env)
        # The controller is part of the platform, installed from a pinned manifest.
        self.assertIn("    - agent_sandbox\n", (ROOT / "ansible" / "bootstrap.yml").read_text())
        tasks = (ROOT / "ansible" / "roles" / "agent_sandbox" / "tasks" / "main.yml").read_text()
        self.assertIn('checksum: "sha256:{{ agent_sandbox_manifest_sha256 }}"', tasks)
        self.assertIn("agent_sandbox_manifest_sha256:", (generated / "deployment-vars.yml").read_text())
        # The server's Role must cover what the agent_sandbox launcher calls.
        role = (ROOT / "kubernetes" / "platform" / "runner-rbac.yaml").read_text()
        self.assertIn("resources: [sandboxes]\n    verbs: [create, get, patch, delete]", role)
        too_short = dict(
            load_environment("ci"),
            runner_idle_shutdown_seconds=agent_idle + MIN_RUNNER_SUSPEND_WINDOW_SECONDS - 1,
        )
        with mock.patch("config.load_environment", return_value=too_short):
            with self.assertRaises(ConfigError):
                validate("ci")

    def test_acme_challenge_selects_the_issuer_solver(self) -> None:
        values, _ = validate("ci")
        self.assertEqual(values["acme_challenge"], "http-01")
        source = (ROOT / "environments" / "ci.toml").read_text()
        for challenge, solver, absent in (
            ("http-01", "      - http01:\n          ingress:\n", "dns01"),
            ("cloudflare-dns-01", "      - dns01:\n          cloudflare:\n", "http01"),
        ):
            with self.subTest(challenge=challenge):
                name = f"ci-{challenge}"
                path = ROOT / "environments" / f"{name}.toml"
                self.assertFalse(path.exists())
                path.write_text(source + f'acme_challenge = "{challenge}"\n')
                try:
                    subprocess.run(
                        [str(ROOT / "scripts" / "render"), "--environment", name, "--prepare-only"],
                        check=True, capture_output=True,
                    )
                    generated = ROOT / ".generated" / name
                    issuer = (generated / "platform" / "dynamic.yaml").read_text()
                    variables = (generated / "deployment-vars.yml").read_text()
                finally:
                    path.unlink()
                    shutil.rmtree(ROOT / ".generated" / name, ignore_errors=True)
                self.assertIn(solver, issuer)
                self.assertNotIn(absent, issuer)
                self.assertIn(f'omnigent_acme_challenge: "{challenge}"\n', variables)
        # The issuer reads the Secret that the setup task writes.
        self.assertIn("name: cloudflare-api-token\n              key: api-token", issuer)
        setup = (ROOT / "scripts" / "setup-cloudflare-token").read_text()
        self.assertIn("create secret generic cloudflare-api-token -n cert-manager", setup)
        self.assertIn("--from-file=api-token=/dev/stdin", setup)
        invalid = dict(load_environment("ci"), acme_challenge="dns-01")
        with mock.patch("config.load_environment", return_value=invalid):
            with self.assertRaises(ConfigError):
                validate("ci")

    def test_runners_join_the_tailnet_under_the_name_the_server_shows(self) -> None:
        subprocess.run(
            [str(ROOT / "scripts" / "render"), "--environment", "ci", "--prepare-only"],
            check=True, capture_output=True,
        )
        generated = ROOT / ".generated" / "ci"
        env = (generated / "omnigent-config.env").read_text()
        self.assertIn("OMNIGENT_TAILSCALE_TAILNET=tailnet.invalid\n", env)
        self.assertIn("OMNIGENT_TAILSCALE_TAGS=tag:omnigent-runner\n", env)
        sandbox = (generated / "sandbox-config.yaml").read_text()
        self.assertIn("      - OMNIGENT_TAILSCALE_TAGS\n", sandbox)
        # The Pod and the server derive the same name from the host id.
        script = (ROOT / "images" / "runner" / "tailscale" / "tailscale-service.sh").read_text()
        self.assertIn("""name="omnigent-$(printf '%.8s' "$host_id" | tr '[:upper:]' '[:lower:]')\"""", script)
        self.assertIn("--ssh", script)
        self.assertIn("ephemeral=true&preauthorized=true", script)
        server_patch = (ROOT / "images" / "server" / "patches" / "0003-tailscale-host.patch").read_text()
        self.assertIn('return f"omnigent-{host_id[:8].lower()}.{tailnet}"', server_patch)
        self.assertIn('_TAILSCALE_TAILNET_ENV_VAR = "OMNIGENT_TAILSCALE_TAILNET"', server_patch)
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("tailscale/tailscale-service.sh /usr/local/bin/omnigent-tailscale", dockerfile)
        self.assertIn("tailscale/tailscale-service-profile.sh /etc/profile.d/", dockerfile)
        self.assertIn("[tasks.setup-tailscale]", (ROOT / ".mise.toml").read_text())
        self.assertIn("TAILSCALE_AUTHKEY", (ROOT / "scripts" / "setup-tailscale").read_text())
        base = load_environment("ci")
        for key, value in (
            ("tailscale_tailnet", "Tail1.ts.net"),
            ("tailscale_tailnet", "ts.net\nx"),
            ("tailscale_tags", "omnigent"),
            ("tailscale_tags", "tag:a,b"),
        ):
            with self.subTest(key=key, value=value):
                with mock.patch("config.load_environment", return_value=dict(base, **{key: value})):
                    with self.assertRaises(ConfigError):
                        validate("ci")
        without = {k: v for k, v in base.items() if k != "tailscale_tailnet"}
        with mock.patch("config.load_environment", return_value=without):
            values, _ = validate("ci")
        self.assertNotIn("tailscale_tailnet", values)

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

    def test_production_deploys_are_manual_and_gated(self) -> None:
        # ci/workflows holds replacements until someone who may change workflows moves them.
        for directory in (ROOT / ".github" / "workflows", ROOT / "ci" / "workflows"):
            path = directory / "deploy.yml"
            if not path.exists():
                continue
            with self.subTest(path=str(path.relative_to(ROOT))):
                workflow = path.read_text()
                triggers = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
                self.assertIn("workflow_dispatch:", triggers)
                self.assertNotIn("push:", triggers)
                self.assertNotIn("pull_request", triggers)
                # Each run gets one Environment, whose required reviewer gates it.
                self.assertIn("environment: ${{ inputs.environment }}", workflow)
                self.assertIn("type: choice", workflow)
                # The API server only accepts tokens from the Environment named
                # in the environment file, so every option must match its own.
                options = workflow.split("options:\n", 1)[1].split("default:", 1)[0]
                for name in re.findall(r"^\s+- (\S+)$", options, re.M):
                    with self.subTest(environment=name):
                        cfg = load_environment(name)
                        self.assertEqual(cfg.get("deploy_github_environment"), name)
                        self.assertTrue((ROOT / "ansible" / "inventory" / name / "hosts.yml").is_file())
                self.assertIn("github.ref == 'refs/heads/main'", workflow)
                for action in re.findall(r"uses: (\S+)", workflow):
                    if not action.startswith("./"):
                        self.assertRegex(action, r"@[0-9a-f]{40}$")
                # No stored credentials: OIDC to Tailscale and Kubernetes. The two
                # Tailscale identifiers are secrets only so public run logs mask them.
                self.assertIn("id-token: write", workflow)
                self.assertEqual(set(re.findall(r"secrets\.(\w+)", workflow)), {"TS_OAUTH_CLIENT_ID", "TS_AUDIENCE"})
                self.assertNotIn("vars.", workflow)
                # Bootstrap needs sudo on the VM, so it never runs from CI.
                code = "\n".join(line for line in workflow.splitlines() if not line.lstrip().startswith("#"))
                self.assertNotIn("bootstrap", code)
        # The shared image build feeds release deploys, so pin it too.
        build = (ROOT / ".github" / "actions" / "build-images" / "action.yml").read_text()
        for action in re.findall(r"uses: (\S+)", build):
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        tasks = (ROOT / ".mise.toml").read_text()
        self.assertNotIn("--ask-become-pass", tasks)
        self.assertIn("become_ask_pass = True", (ROOT / "ansible.cfg").read_text())

    def test_deploy_layer_needs_only_namespaced_access(self) -> None:
        subprocess.run([str(ROOT / "scripts" / "render"), "--environment", "ci"], check=True)
        generated = ROOT / ".generated" / "ci"
        app = generated / "manifest.yaml"
        platform = (generated / "platform.yaml").read_text()
        # Plural resource name for every kind the deploy identity must manage.
        resources = {
            "ConfigMap": "configmaps", "Deployment": "deployments", "Ingress": "ingresses",
            "Middleware": "middlewares", "PersistentVolumeClaim": "persistentvolumeclaims",
            "Service": "services", "ServiceAccount": "serviceaccounts", "StatefulSet": "statefulsets",
        }
        rbac = (ROOT / "kubernetes" / "platform" / "deployer-rbac.yaml").read_text()
        apply_script = (ROOT / "scripts" / "apply-manifest").read_text()
        namespaces = set(re.findall(r"^  namespace: (\S+)$", app.read_text(), re.MULTILINE))
        self.assertEqual(namespaces, {"omnigent", "omnigent-sandboxes"})
        for kind in set(re.findall(r"^kind: (\S+)$", app.read_text(), re.MULTILINE)):
            with self.subTest(kind=kind):
                self.assertIn(kind, resources, "new app kinds need a deployer-rbac.yaml rule")
                self.assertIn(resources[kind], rbac)
                self.assertRegex(apply_script, rf"--prune-allowlist=\S+/{kind} ")
        for kind in ("Namespace", "ValidatingAdmissionPolicy", "ClusterIssuer", "ResourceQuota", "Role"):
            self.assertIn(f"kind: {kind}\n", platform)
            self.assertNotRegex(apply_script, rf"--prune-allowlist=\S+/{kind} ")
        self.assertNotIn("managed-by: omnigent-deployment", platform)
        revision = (generated / "platform-revision").read_text().strip()
        self.assertIn(f'revision: "{revision}"', platform)
        # The deployer must never be able to loosen the platform or reach the host.
        granted = set()
        for items in re.findall(r"(?:resources|verbs): \[([^\]]*)\]", rbac):
            granted.update(item.strip() for item in items.split(","))
        for forbidden in (
            "*", "namespaces", "roles", "rolebindings", "clusterroles", "clusterrolebindings",
            "validatingadmissionpolicies", "mutatingadmissionpolicies", "nodes/proxy",
            "pods/exec", "serviceaccounts/token", "escalate", "bind", "impersonate",
        ):
            self.assertNotIn(forbidden, granted)

    def test_github_oidc_trust_is_pinned_to_one_environment(self) -> None:
        template = (ROOT / "ansible" / "roles" / "k3s" / "templates" / "authentication-config.yaml.j2").read_text()
        self.assertIn("url: https://token.actions.githubusercontent.com", template)
        self.assertIn("anonymous:\n  enabled: false", template)
        for claim in ("repository_id", "environment", "ref"):
            self.assertIn(f"claims.?{claim}.orValue('')", template)
        self.assertIn("name: github-actions:deploy", (ROOT / "kubernetes" / "platform" / "deployer-rbac.yaml").read_text())
        self.assertIn("'github-actions:deploy'", template)
        values, _ = validate("ci")
        with tempfile.TemporaryDirectory() as tmp:
            ca = ROOT / "environments" / "ci.kubernetes-ca.crt"
            self.assertFalse(ca.exists())
            ca.write_text("-----BEGIN CERTIFICATE-----\nci\n-----END CERTIFICATE-----\n")
            try:
                output = Path(tmp) / "kubeconfig.json"
                subprocess.run([str(ROOT / "scripts" / "ci-kubeconfig"), "--environment", "ci", str(output)], check=True, capture_output=True)
                kubeconfig = json.loads(output.read_text())
            finally:
                ca.unlink()
        self.assertEqual(kubeconfig["clusters"][0]["cluster"]["server"], f"https://{values['kubernetes_api_host']}:6443")
        self.assertEqual(kubeconfig["users"][0]["user"]["exec"]["args"], [f"https://{values['hostname']}/kubernetes"])

    def test_ci_uses_mise_and_matches_published_architecture(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
        self.assertIn("jdx/mise-action@v4", workflow)
        self.assertIn("mise run test", workflow)
        self.assertNotIn("make bootstrap", workflow)
        preflight = (ROOT / "ansible" / "roles" / "preflight" / "tasks" / "main.yml").read_text()
        self.assertIn("ansible_architecture == 'x86_64'", preflight)

    def test_upstream_patches_apply_to_pinned_upstream(self) -> None:
        # Each image applies its directory's patches in order with --fuzz=0.
        patch_sets = {
            ("server", "patches"): "COPY patches/",
            ("runner", "patches"): "COPY patches/",
            ("server", "web-patches"): "COPY web-patches/",
        }
        commit = load_versions()["omnigent_commit"]
        for (image, directory), copy in patch_sets.items():
            with self.subTest(image=image, directory=directory):
                patches = sorted((ROOT / "images" / image / directory).glob("*.patch"))
                self.assertTrue(patches)
                dockerfile = (ROOT / "images" / image / "Dockerfile").read_text()
                self.assertIn(copy, dockerfile)
                self.assertIn("--fuzz=0", dockerfile)
                with tempfile.TemporaryDirectory() as tmp:
                    for patch in patches:
                        touched = set(re.findall(r"^--- a/(\S+)$", patch.read_text(), re.MULTILINE))
                        for path in touched:
                            target = Path(tmp) / path
                            if target.exists():
                                continue
                            target.parent.mkdir(parents=True, exist_ok=True)
                            url = f"https://raw.githubusercontent.com/omnigent-ai/omnigent/{commit}/{path}"
                            with urllib.request.urlopen(url, timeout=30) as response:
                                target.write_bytes(response.read())
                        subprocess.run(["git", "apply", str(patch)], cwd=tmp, check=True)
                    for source in Path(tmp).rglob("*.py"):
                        py_compile.compile(str(source), doraise=True)


if __name__ == "__main__":
    unittest.main()
