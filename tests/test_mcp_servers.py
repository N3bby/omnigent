"""Settings → MCP servers: the store against fake Kubernetes, and the runner's agent wrappers."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images/server"))
sys.path.insert(0, str(ROOT / "tests"))
import omnigent_mcp_servers as mcp_servers
import omnigent_repo_env as repo_env
from test_repo_env import FakeCore, FakeApiException

SCRIPT = ROOT / "images/runner/mcp-servers.py"
FIGMA_KEY = "figd_test-personal-access-token"
BEARER = "Bearer test-bearer-token"

FIGMA = {"name": "figma", "transport": "stdio", "command": "npx",
         "args": ["-y", "figma-developer-mcp", "--stdio"],
         "env": {"FIGMA_API_KEY": FIGMA_KEY}, "agents": ["claude", "codex"]}
LINEAR = {"name": "linear", "transport": "http", "url": "https://mcp.example.com/mcp",
          "headers": {"Authorization": BEARER}, "agents": ["codex"]}


ROADPASS = "github.com/kunlabora/roadpass"


def decoded_keys(core, key=ROADPASS):
    return set(core.secrets[repo_env.secret_name(key)]["data"])


def stored(core):
    secret = core.secrets[mcp_servers.SECRET]
    return json.loads(base64.b64decode(secret["data"][mcp_servers.KEY]))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()

    def test_listing_never_returns_header_or_variable_values(self):
        figma = mcp_servers.save(self.core, FIGMA, updated_by="levi")
        linear = mcp_servers.save(self.core, LINEAR)
        listing = mcp_servers.summary(self.core)
        self.assertEqual([s["name"] for s in listing["data"]], ["figma", "linear"])
        self.assertEqual(figma["env_names"], ["FIGMA_API_KEY"])
        self.assertEqual(figma["updated_by"], "levi")
        self.assertEqual(linear["header_names"], ["Authorization"])
        self.assertEqual(linear["agents"], ["codex"])
        for text in (json.dumps(listing), json.dumps(figma), json.dumps(linear)):
            self.assertNotIn(FIGMA_KEY, text)
            self.assertNotIn(BEARER, text)
        self.assertEqual(stored(self.core)["servers"][0]["env"], {"FIGMA_API_KEY": FIGMA_KEY})

    def test_null_keeps_a_value_and_left_out_names_are_removed(self):
        figma = mcp_servers.save(self.core, {**FIGMA, "env": {"FIGMA_API_KEY": FIGMA_KEY, "DEBUG": "1"}})
        mcp_servers.save(self.core, {**FIGMA, "id": figma["id"], "args": ["-y", "figma-developer-mcp"],
                                     "env": {"FIGMA_API_KEY": None}})
        server = stored(self.core)["servers"][0]
        self.assertEqual(server["env"], {"FIGMA_API_KEY": FIGMA_KEY})
        self.assertEqual(server["args"], ["-y", "figma-developer-mcp"])
        with self.assertRaisesRegex(ValueError, "no value yet"):
            mcp_servers.save(self.core, {**FIGMA, "id": figma["id"], "env": {"OTHER": None}})
        with self.assertRaisesRegex(ValueError, "no value yet"):
            mcp_servers.save(self.core, {**LINEAR, "headers": {"Authorization": None}})

    def test_changing_transport_drops_the_old_transports_values(self):
        figma = mcp_servers.save(self.core, FIGMA)
        mcp_servers.save(self.core, {"id": figma["id"], "name": "figma", "transport": "http",
                                     "url": "https://mcp.figma.com/mcp", "agents": ["claude"]})
        server = stored(self.core)["servers"][0]
        self.assertEqual(server["url"], "https://mcp.figma.com/mcp")
        self.assertNotIn("env", server)
        self.assertNotIn("command", server)
        # The new transport's values can't be kept from the old one.
        with self.assertRaisesRegex(ValueError, "no value yet"):
            mcp_servers.save(self.core, {**FIGMA, "id": figma["id"], "env": {"FIGMA_API_KEY": None}})

    def test_rejects_what_the_agents_cant_use(self):
        for body, message in [
            ({**FIGMA, "name": "Omnigent"}, "Omnigent's own"),
            ({**FIGMA, "name": "figma tools"}, "letters, digits"),
            ({**FIGMA, "name": "x" * 65}, "too long"),
            ({**FIGMA, "transport": "sse"}, "HTTP or a command"),
            ({**FIGMA, "agents": ["cursor"]}, "Claude Code, Codex"),
            ({**FIGMA, "command": " "}, "command that starts"),
            ({**FIGMA, "env": {"1BAD": "x"}}, "valid variable name"),
            ({**FIGMA, "args": ["a\0b"]}, "NUL"),
            ({**LINEAR, "url": "mcp.example.com"}, "https://"),
            ({**LINEAR, "url": "file:///etc/passwd"}, "https://"),
            ({**LINEAR, "headers": {"Bad Header": "x"}}, "valid header name"),
            ({**LINEAR, "headers": {"X-Token": "a\nb"}}, "line breaks"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                mcp_servers.save(self.core, body)
        self.assertEqual(self.core.secrets, {})
        mcp_servers.save(self.core, FIGMA)
        with self.assertRaisesRegex(ValueError, "already"):
            mcp_servers.save(self.core, {**LINEAR, "name": "FIGMA"})

    def test_a_concurrent_edit_is_refused(self):
        mcp_servers.save(self.core, FIGMA)
        store, secret = mcp_servers.read(self.core)
        mcp_servers.save(self.core, LINEAR)
        with self.assertRaises(FakeApiException):
            mcp_servers.write(self.core, store, secret)

    def test_delete_and_a_server_that_is_gone(self):
        figma = mcp_servers.save(self.core, FIGMA)
        mcp_servers.delete(self.core, figma["id"])
        self.assertEqual(mcp_servers.read(self.core)[0]["servers"], [])
        # Runners then find no servers file, and agents start as before.
        self.assertNotIn(mcp_servers.KEY, self.core.secrets[mcp_servers.SECRET]["data"])
        for call in (lambda: mcp_servers.delete(self.core, figma["id"]),
                     lambda: mcp_servers.save(self.core, {**FIGMA, "id": figma["id"]})):
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                call()

    def test_a_repositorys_servers_live_with_its_variables(self):
        repo_env.save_repository(self.core, ROADPASS, {"NPM_TOKEN": "npm-secret"}, instructions="Use pnpm.")
        figma = mcp_servers.save(self.core, {**FIGMA, "repository": "Kunlabora/RoadPass"}, updated_by="levi")
        self.assertEqual(figma["repository"], ROADPASS)
        self.assertNotIn(mcp_servers.SECRET, self.core.secrets)
        # Saving the variables on their own page keeps the servers, and the
        # servers are no variable.
        entry = repo_env.save_repository(self.core, ROADPASS, {"NPM_TOKEN": None})
        self.assertEqual(entry["names"], ["NPM_TOKEN"])
        self.assertEqual(entry["mcp_servers"], ["figma"])
        self.assertEqual(entry["instructions"], "Use pnpm.")
        # Only the repository's sessions mount that Secret, values included.
        data = self.core.secrets[repo_env.secret_name(ROADPASS)]["data"]
        self.assertEqual(json.loads(base64.b64decode(data[repo_env.MCP_SERVERS_KEY]))["servers"][0]["env"],
                         {"FIGMA_API_KEY": FIGMA_KEY})
        mcp_servers.delete(self.core, figma["id"], ROADPASS)
        self.assertEqual(decoded_keys(self.core), {"NPM_TOKEN", "agent-instructions.md"})

    def test_the_new_session_form_sees_a_repositorys_servers_without_their_keys(self):
        mcp_servers.save(self.core, {**FIGMA, "repository": ROADPASS})
        mcp_servers.save(self.core, {**LINEAR, "repository": ROADPASS,
                                     "url": "https://user:pass@MCP.Linear.app/mcp?token=query-secret"})
        summary = repo_env.repository_summary(self.core, ROADPASS)
        self.assertEqual(summary["mcp_servers"], [
            {"name": "figma", "agents": ["claude", "codex"], "host": None},
            {"name": "linear", "agents": ["codex"], "host": "mcp.linear.app"},
        ])
        text = json.dumps(summary)
        for secret in (FIGMA_KEY, BEARER, "figma-developer-mcp", "pass", "query-secret"):
            self.assertNotIn(secret, text)

    def test_global_and_repository_servers_list_together(self):
        mcp_servers.save(self.core, FIGMA)
        # The same name for a repository replaces the global one in its sessions.
        roadpass = mcp_servers.save(self.core, {**FIGMA, "repository": ROADPASS, "agents": ["claude"]})
        with self.assertRaisesRegex(ValueError, f"{ROADPASS} already has"):
            mcp_servers.save(self.core, {**LINEAR, "name": "FIGMA", "repository": ROADPASS})
        listing = mcp_servers.summary(self.core)["data"]
        self.assertEqual([(s["repository"], s["name"]) for s in listing], [(None, "figma"), (ROADPASS, "figma")])
        self.assertNotIn(FIGMA_KEY, json.dumps(listing))
        # A repository left with nothing else loses its Secret, as on the variables page.
        mcp_servers.delete(self.core, roadpass["id"], ROADPASS)
        self.assertNotIn(repo_env.secret_name(ROADPASS), self.core.secrets)
        for repository in ("not a repository", ["github.com/a/b"]):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                mcp_servers.save(self.core, {**FIGMA, "repository": repository})

    def test_every_runner_mounts_the_servers_read_only_and_optionally(self):
        volumes, mounts = mcp_servers.pod_volumes()
        self.assertTrue(volumes[0]["secret"]["optional"])
        self.assertEqual(volumes[0]["secret"]["secretName"], mcp_servers.SECRET)
        self.assertEqual(mounts, [{"name": "mcp-servers", "mountPath": "/run/omnigent/mcp-servers",
                                   "readOnly": True}])


class RunnerTests(unittest.TestCase):
    """images/runner/mcp-servers.py, as the wrappers call it."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.servers = self.root / "servers.json"
        self.repo_env = self.root / "repo-env"
        self.env = {**os.environ, "OMNIGENT_MCP_SERVERS_FILE": str(self.servers),
                    "OMNIGENT_MCP_CONFIG_DIR": str(self.root / "config"),
                    "OMNIGENT_REPO_ENV_DIR": str(self.repo_env),
                    "OMNIGENT_REPO_SETUP_RUN": str(self.root / "repo-setup")}
        self.env.pop("CODEX_HOME", None)
        self.env.pop("HARNESS_CODEX_MINIMAL_CONFIG", None)
        # Prints the command line and environment it was started with.
        self.cli = self.root / "fake-cli.py"
        self.cli.write_text("import json, os, sys; print(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ)}))")

    def tearDown(self):
        self.directory.cleanup()

    def write(self, *servers):
        stored = []
        for i, server in enumerate(servers):
            stored.append({"id": str(i), **server})
        self.servers.write_text(json.dumps({"servers": stored}))

    def write_repository(self, secret, *servers):
        directory = self.repo_env / secret
        directory.mkdir(parents=True, exist_ok=True)
        stored = [{"id": f"{secret}-{i}", **server} for i, server in enumerate(servers)]
        (directory / "mcp-servers.json").write_text(json.dumps({"servers": stored}))
        return directory / "mcp-servers.json"

    def start(self, agent, *args, **env):
        command = [sys.executable, str(SCRIPT), "exec", agent, sys.executable, str(self.cli), "--", *args]
        result = subprocess.run(command, env={**self.env, **env}, text=True, capture_output=True, check=True)
        return json.loads(result.stdout), result.stderr

    def test_claude_gets_a_config_without_values_in_front_of_its_arguments(self):
        self.write(FIGMA, {**LINEAR, "agents": ["claude"]})
        started, _ = self.start("claude", "-p", "hello")
        first, *rest = started["args"]
        self.assertEqual(rest, ["-p", "hello"])
        self.assertTrue(first.startswith("--mcp-config="))
        config_text = Path(first.split("=", 1)[1]).read_text()
        self.assertNotIn(FIGMA_KEY, config_text)
        self.assertNotIn(BEARER, config_text)
        config = json.loads(config_text)["mcpServers"]
        self.assertEqual(config["figma"]["args"][-3:], ["run", str(self.servers), "figma"])
        self.assertEqual(config["linear"]["url"], LINEAR["url"])
        # Claude Code runs the helper for the headers.
        helper = subprocess.run(config["linear"]["headersHelper"], shell=True, env=self.env,
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(helper.stdout), {"Authorization": BEARER})
        self.assertNotIn(FIGMA_KEY, json.dumps(started))

    def test_claude_probes_and_agents_without_servers_start_unchanged(self):
        self.write({**LINEAR, "agents": ["codex"]})
        self.assertEqual(self.start("claude", "-p", "hi")[0]["args"], ["-p", "hi"])
        self.write(FIGMA)
        probe = ["-p", "/model", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        self.assertEqual(self.start("claude", *probe)[0]["args"], probe)

    def test_stdio_servers_start_with_their_own_variables_taken_literally(self):
        self.write({**FIGMA, "command": sys.executable,
                    "args": ["-c", "import os, sys; print(sys.argv[1], os.environ['FIGMA_API_KEY'])", "$HOME"],
                    "env": {"FIGMA_API_KEY": FIGMA_KEY + " ${HOME}"}})
        output = subprocess.check_output([sys.executable, str(SCRIPT), "run", str(self.servers), "figma"],
                                         env=self.env, text=True)
        self.assertEqual(output, f"$HOME {FIGMA_KEY} ${{HOME}}\n")
        missing = subprocess.run([sys.executable, str(SCRIPT), "run", str(self.servers), "gone"],
                                 env=self.env, text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("removed in Settings", missing.stderr)

    def test_codex_gets_overrides_and_its_headers_only_in_its_environment(self):
        self.write(FIGMA, LINEAR)
        started, _ = self.start("codex", "app-server", CODEX_HOME="/home/omnigent/.omnigent/codex-native/s1/codex-home")
        args = started["args"]
        self.assertEqual(args[-1], "app-server")
        overrides = dict(a.split("=", 1) for a in args[1:-1:2])
        self.assertEqual(args[0:-1:2], ["-c"] * (len(args) // 2))
        self.assertEqual(json.loads(overrides["mcp_servers.linear.url"]), LINEAR["url"])
        self.assertEqual(overrides["mcp_servers.linear.env_http_headers"],
                         '{ "Authorization" = "OMNIGENT_MCP_SECRET_1" }')
        self.assertEqual(started["env"]["OMNIGENT_MCP_SECRET_1"], BEARER)
        self.assertEqual(json.loads(overrides["mcp_servers.figma.args"])[-3:], ["run", str(self.servers), "figma"])
        self.assertEqual(overrides["mcp_servers.figma.startup_timeout_sec"], "60")
        self.assertNotIn(BEARER, json.dumps(args))
        self.assertNotIn(FIGMA_KEY, json.dumps(started))

    def test_a_repositorys_servers_join_and_replace_the_global_ones(self):
        self.write(FIGMA, {**LINEAR, "agents": ["claude"]})
        # The session picked roadpass first, then other.
        (self.root / "repo-setup").mkdir()
        (self.root / "repo-setup" / "checkouts").write_text(
            "omnigent-repo-env-roadpass-1\tgithub.com/k/roadpass\t/w/roadpass\n"
            "omnigent-repo-env-a-other-2\tgithub.com/k/other\t/w/other\n")
        roadpass = self.write_repository("omnigent-repo-env-roadpass-1",
                                         {**FIGMA, "env": {"FIGMA_API_KEY": "roadpass-key"}},
                                         {**LINEAR, "name": "linear", "agents": ["codex"]})
        self.write_repository("omnigent-repo-env-a-other-2",
                              {**FIGMA, "env": {"FIGMA_API_KEY": "other-key"}},
                              {**LINEAR, "name": "sentry", "agents": ["claude"]})
        started, _ = self.start("claude")
        config = json.loads(Path(started["args"][0].split("=", 1)[1]).read_text())["mcpServers"]
        # roadpass's figma wins over other's and the global one; its linear,
        # off for Claude Code, still hides the global linear.
        self.assertEqual(sorted(config), ["figma", "sentry"])
        self.assertEqual(config["figma"]["args"][-2:], [str(roadpass), "figma"])
        codex, _ = self.start("codex", CODEX_HOME="/opt/codex-home")
        self.assertIn(f'mcp_servers.linear.url={json.dumps(LINEAR["url"])}', codex["args"])
        # Only a repository's servers, without global ones, still start them.
        self.servers.unlink()
        self.assertTrue(self.start("claude")[0]["args"][0].startswith("--mcp-config="))

    def test_codex_probes_start_without_the_servers(self):
        self.write(FIGMA)
        for env in ({"CODEX_HOME": str(self.root / "probe" / "codex-home")},
                    {"CODEX_HOME": "/opt/codex-home", "HARNESS_CODEX_MINIMAL_CONFIG": "1"}):
            with self.subTest(env=env):
                self.assertEqual(self.start("codex", "app-server", **env)[0]["args"], ["app-server"])
        for home in ("/opt/codex-home", "/tmp/omnigent-codex-home-abc"):
            with self.subTest(home=home):
                self.assertIn("-c", self.start("codex", "app-server", CODEX_HOME=home)[0]["args"])

    def test_a_broken_file_starts_the_agent_without_servers(self):
        self.servers.write_text("{not json")
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                started, stderr = self.start(agent, "--version")
                self.assertEqual(started["args"], ["--version"])
                self.assertIn("without the MCP servers", stderr)


class WrapperTests(unittest.TestCase):
    """The claude and codex wrappers go through mcp-servers.py only once there are servers."""

    def wrapper(self, root, name, replacements):
        library = root / "library.sh"
        library.write_text('omnigent_wait_for_repo_setup() { :; }\nomnigent_agent_instructions() { :; }\n')
        source = (ROOT / "images/runner" / name).read_text()
        replacements = {
            "/usr/local/lib/omnigent/repo-env.sh": str(library),
            "/usr/local/lib/omnigent/agent-instructions.sh": str(library),
            "/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py": "true",
            "/opt/venv/bin/python /usr/local/lib/omnigent/mcp-servers.py":
                shlex.quote(sys.executable) + " " + shlex.quote(str(SCRIPT)),
            **replacements,
        }
        for old, new in replacements.items():
            self.assertIn(old, source)
            source = source.replace(old, new)
        path = root / name
        path.write_text(source)
        return path

    def test_both_wrappers_add_the_servers_and_keep_their_own_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "fake-cli.py"
            cli.write_text("import json, sys; print(json.dumps(sys.argv[1:]))")
            fake = root / "fake-cli"
            fake.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(cli))} \"$@\"\n")
            fake.chmod(0o755)
            managed = root / "managed-settings.json"
            managed.write_text('{"permissions":{"defaultMode":"bypassPermissions"}}')
            claude = self.wrapper(root, "claude-wrapper.sh", {
                "/usr/local/bin/claude-real": str(fake),
                "managed=/etc/claude-code/managed-settings.json": f"managed={managed}",
            })
            codex = self.wrapper(root, "codex-wrapper.sh", {
                "node=/usr/local/bin/node": "node=" + shlex.quote(sys.executable),
                "real_codex=/usr/local/bin/codex-real": "real_codex=" + shlex.quote(str(cli)),
            })
            servers = root / "servers.json"
            env = {**os.environ, "OMNIGENT_MCP_SERVERS_FILE": str(servers),
                   "OMNIGENT_MCP_CONFIG_DIR": str(root / "config"),
                   "OMNIGENT_REPO_ENV_DIR": str(root / "repo-env"),
                   "CODEX_HOME": str(root / "omnigent-codex-home-test"),
                   "OMNIGENT_CODEX_BYPASS_APPROVALS": "1"}
            env.pop("HARNESS_CODEX_MINIMAL_CONFIG", None)

            def run(wrapper):
                return json.loads(subprocess.check_output(["sh", str(wrapper), "-p", "hi"], env=env, text=True))

            # Before any server is saved, the agents start exactly as before.
            self.assertEqual(run(claude), ["--allow-dangerously-skip-permissions", "-p", "hi"])
            self.assertEqual(run(codex), ["--dangerously-bypass-approvals-and-sandbox", "-p", "hi"])
            servers.write_text(json.dumps({"servers": [{"id": "1", **FIGMA}]}))
            claude_args = run(claude)
            self.assertTrue(claude_args[0].startswith("--mcp-config="))
            self.assertEqual(claude_args[1:], ["--allow-dangerously-skip-permissions", "-p", "hi"])
            codex_args = run(codex)
            self.assertEqual(codex_args[0], "-c")
            self.assertEqual(codex_args[-3:], ["--dangerously-bypass-approvals-and-sandbox", "-p", "hi"])
            # A repository's servers alone are enough.
            servers.unlink()
            repository = root / "repo-env" / "omnigent-repo-env-roadpass-1"
            repository.mkdir(parents=True)
            (repository / "mcp-servers.json").write_text(json.dumps({"servers": [{"id": "2", **FIGMA}]}))
            self.assertTrue(run(claude)[0].startswith("--mcp-config="))
            self.assertEqual(run(codex)[0], "-c")


if __name__ == "__main__":
    unittest.main()
