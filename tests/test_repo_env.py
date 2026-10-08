from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
import omnigent_repo_env as repo_env  # noqa: E402

LOADER = ROOT / "images" / "runner" / "repo-env" / "repo-env.sh"
DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def load_setup_script():
    path = ROOT / "scripts" / "setup-repo-env"
    loader = importlib.machinery.SourceFileLoader("setup_repo_env", str(path))
    spec = importlib.util.spec_from_loader("setup_repo_env", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RepoKeyTests(unittest.TestCase):
    def test_every_way_of_writing_a_repository_gives_one_key(self) -> None:
        for url in (
            "https://github.com/kunlabora/roadpass.git",
            "https://github.com/kunlabora/roadpass",
            "https://github.com/kunlabora/roadpass/",
            "git@github.com:kunlabora/roadpass.git",
            "ssh://git@github.com:22/kunlabora/roadpass.git",
            "https://x-access-token:secret@github.com/kunlabora/roadpass.git",
            "https://github.com/Kunlabora/RoadPass.git",
            "kunlabora/roadpass",
            "github.com/kunlabora/roadpass",
        ):
            with self.subTest(url=url):
                self.assertEqual(repo_env.repo_key(url), "github.com/kunlabora/roadpass")

    def test_other_hosts_and_subgroups_keep_their_path(self) -> None:
        self.assertEqual(repo_env.repo_key("https://gitlab.com/group/sub/repo.git"), "gitlab.com/group/sub/repo")
        self.assertEqual(repo_env.repo_key("git@ghe.example.com:org/repo.git"), "ghe.example.com/org/repo")

    def test_rejects_what_is_not_a_repository(self) -> None:
        for bad in ("", "roadpass", "https://github.com/", "file:///srv/repo.git", "a/../b", "owner/repo name"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repo_env.repo_key(bad)


class SecretNameTests(unittest.TestCase):
    def test_name_is_readable_and_a_dns_label(self) -> None:
        name = repo_env.secret_name("github.com/kunlabora/roadpass")
        self.assertRegex(name, r"^omnigent-repo-env-kunlabora-roadpass-[0-9a-f]{8}$")
        long = repo_env.secret_name("github.com/" + "o" * 100 + "/" + "r" * 100)
        for value in (name, long):
            self.assertLessEqual(len(value), 63)
            self.assertRegex(value, DNS_LABEL)

    def test_similar_repositories_get_different_secrets(self) -> None:
        names = {
            repo_env.secret_name(key)
            for key in ("github.com/a/my_repo", "github.com/a/my.repo", "github.com/a/my-repo", "gitlab.com/a/my-repo")
        }
        self.assertEqual(len(names), 4)


class ParseEnvTests(unittest.TestCase):
    def test_parses_a_dotenv_file_without_expanding(self) -> None:
        text = (
            "# Registry\n"
            "\n"
            "GOVFLANDERS_NPM_TOKEN=abc123\n"
            "export QUOTED='a b'\n"
            'DOUBLE="x=y"\n'
            "LITERAL=$HOME/$(whoami)\n"
            "EMPTY=\n"
        )
        self.assertEqual(repo_env.parse_env(text), {
            "GOVFLANDERS_NPM_TOKEN": "abc123",
            "QUOTED": "a b",
            "DOUBLE": "x=y",
            "LITERAL": "$HOME/$(whoami)",
            "EMPTY": "",
        })

    def test_rejects_bad_lines_reserved_names_and_duplicates(self) -> None:
        for text in ("no equals sign", "1BAD=x", "BAD-NAME=x", "PATH=/tmp", "OMNIGENT_X=1", "CLAUDE_CODE_OAUTH_TOKEN=x", "A=1\nA=2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                repo_env.parse_env(text)


class PodVolumeTests(unittest.TestCase):
    def test_each_repository_gets_an_optional_read_only_secret(self) -> None:
        volumes, mounts = repo_env.pod_volumes([
            "https://github.com/kunlabora/roadpass.git",
            "git@github.com:Kunlabora/roadpass.git",  # the same repository again
            "not a repository",
            "https://github.com/kunlabora/other.git",
        ])
        roadpass = repo_env.secret_name("github.com/kunlabora/roadpass")
        self.assertEqual(len(volumes), 2)
        self.assertEqual(volumes[0], {
            "name": "repo-env-0",
            "secret": {"secretName": roadpass, "optional": True, "defaultMode": 0o440},
        })
        self.assertEqual(mounts[0], {
            "name": "repo-env-0",
            "mountPath": f"/run/omnigent/repo-env/{roadpass}",
            "readOnly": True,
        })

    def test_no_repositories_add_nothing(self) -> None:
        self.assertEqual(repo_env.pod_volumes([]), ([], []))

    def test_server_installs_the_module_and_the_patch_uses_it(self) -> None:
        dockerfile = (ROOT / "images" / "server" / "Dockerfile").read_text()
        self.assertIn("COPY omnigent_repo_env.py /tmp/omnigent_repo_env.py\n", dockerfile)
        self.assertIn('install -m 0644 /tmp/omnigent_repo_env.py "$purelib/omnigent_repo_env.py"', dockerfile)
        patch = (ROOT / "images" / "server" / "patches" / "0004-repo-env.patch").read_text()
        self.assertIn("+from omnigent_repo_env import pod_volumes as _repo_env_volumes\n", patch)
        self.assertIn("repo_env_volumes", patch)


class LoaderTests(unittest.TestCase):
    def mount(self, root: Path, files: dict[str, str]) -> None:
        """Lay out files the way the kubelet mounts a Secret."""
        secret = root / repo_env.secret_name("github.com/kunlabora/roadpass")
        data = secret / "..2026_10_08_12_00_00.000000000"
        data.mkdir(parents=True)
        for name, value in files.items():
            (data / name).write_text(value)
        (secret / "..data").symlink_to(data.name)
        for name in files:
            (secret / name).symlink_to(f"..data/{name}")

    def run_loader(self, root: Path, script: str) -> str:
        env = {"PATH": os.environ["PATH"], "OMNIGENT_REPO_ENV_DIR": str(root)}
        return subprocess.run(
            ["sh", "-c", f"set -eu; . {LOADER}; {script}"],
            env=env, check=True, capture_output=True, text=True,
        ).stdout

    def test_exports_each_file_as_a_variable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.mount(Path(tmp), {
                "GOVFLANDERS_NPM_TOKEN": "abc123",
                "LITERAL": "$(echo ran) 'q'",
                "MULTI": "a\nb\n",
                "bad-name": "skipped",
            })
            out = self.run_loader(Path(tmp), 'printf "%s|%s|[%s]|%s" "$GOVFLANDERS_NPM_TOKEN" "$LITERAL" "$MULTI" "${omnigent_repo_env_file-unset}"')
        self.assertEqual(out, "abc123|$(echo ran) 'q'|[a\nb\n]|unset")

    def test_nothing_mounted_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_loader(Path(tmp) / "missing", "echo ok"), "ok\n")

    def test_both_agents_and_login_shells_load_it(self) -> None:
        dockerfile = (ROOT / "images" / "runner" / "Dockerfile").read_text()
        self.assertIn("COPY repo-env/repo-env.sh /usr/local/lib/omnigent/repo-env.sh\n", dockerfile)
        self.assertIn("COPY repo-env/repo-env.sh /etc/profile.d/omnigent-repo-env.sh\n", dockerfile)
        for wrapper in ("claude-wrapper.sh", "codex-wrapper.sh"):
            with self.subTest(wrapper=wrapper):
                text = (ROOT / "images" / "runner" / wrapper).read_text()
                load = text.index("\n. /usr/local/lib/omnigent/repo-env.sh\n")
                self.assertLess(load, text.index("exec "))


class SetupScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = load_setup_script()
        self.calls: list[tuple[list[str], str | None]] = []

    def fake_run(self, existing: bool):
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs.get("input")))
            stdout = "secret/x\n" if existing and argv[1] == "get" else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)
        return run

    def run_script(self, argv: list[str], existing: bool, cwd: str) -> None:
        with mock.patch.object(sys, "argv", ["setup-repo-env", *argv]), \
             mock.patch.object(self.script.subprocess, "run", self.fake_run(existing)), \
             mock.patch.dict(os.environ, {"MISE_ORIGINAL_CWD": cwd}), \
             mock.patch("sys.stdout"):
            self.script.main()

    def test_stores_a_file_relative_to_where_mise_was_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "roadpass.env").write_text("GOVFLANDERS_NPM_TOKEN=abc123\n")
            self.run_script(["git@github.com:kunlabora/roadpass.git", "roadpass.env"], existing=False, cwd=tmp)
        (argv, stdin), = [c for c in self.calls if c[0][1] != "get"]
        self.assertEqual(argv[1:], ["create", "-f", "-"])
        self.assertNotIn("abc123", " ".join(argv))
        secret = json.loads(stdin)
        self.assertEqual(secret["metadata"]["name"], repo_env.secret_name("github.com/kunlabora/roadpass"))
        self.assertEqual(secret["metadata"]["namespace"], "omnigent-sandboxes")
        self.assertEqual(secret["metadata"]["labels"], {"omnigent.dev/repo-env": "true"})
        self.assertEqual(secret["metadata"]["annotations"], {"omnigent.dev/repository": "github.com/kunlabora/roadpass"})
        self.assertEqual(secret["stringData"], {"GOVFLANDERS_NPM_TOKEN": "abc123"})

    def test_replaces_an_existing_secret_from_stdin(self) -> None:
        with mock.patch.object(sys, "stdin") as stdin:
            stdin.isatty.return_value = False
            stdin.read.return_value = "A=1\n"
            self.run_script(["kunlabora/roadpass"], existing=True, cwd=".")
        self.assertEqual([c[0][1] for c in self.calls], ["get", "replace"])

    def test_delete(self) -> None:
        self.run_script(["--delete", "kunlabora/roadpass"], existing=True, cwd=".")
        (argv, _), = self.calls
        self.assertEqual(argv[1:4], ["delete", "secret", repo_env.secret_name("github.com/kunlabora/roadpass")])

    def test_task_and_credential_status(self) -> None:
        self.assertIn("run = './scripts/setup-repo-env'", (ROOT / ".mise.toml").read_text())
        self.assertIn("-l omnigent.dev/repo-env", (ROOT / "scripts" / "credential-status").read_text())


if __name__ == "__main__":
    unittest.main()
