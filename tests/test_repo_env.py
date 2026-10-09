from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
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
        self.assertIn("COPY omnigent_repo_env.py omnigent_repo_env_api.py /tmp/omnigent-modules/\n", dockerfile)
        self.assertIn('install -m 0644 /tmp/omnigent-modules/*.py "$purelib/"', dockerfile)
        patch = (ROOT / "images" / "server" / "patches" / "0004-repo-env.patch").read_text()
        self.assertIn("+from omnigent_repo_env import pod_volumes as _repo_env_volumes\n", patch)
        self.assertIn("repo_env_volumes", patch)


class GitTokenTests(unittest.TestCase):
    def test_only_https_urls_have_a_token_file(self) -> None:
        roadpass = repo_env.secret_name("github.com/kunlabora/roadpass")
        expected = f"/run/omnigent/repo-env/{roadpass}/GIT_TOKEN"
        for url in ("https://github.com/kunlabora/roadpass.git", "https://github.com/Kunlabora/RoadPass"):
            with self.subTest(url=url):
                self.assertEqual(repo_env.token_file(url), expected)
        for url in ("git@github.com:kunlabora/roadpass.git", "ssh://git@github.com/kunlabora/roadpass.git", "https://github.com/"):
            with self.subTest(url=url):
                self.assertIsNone(repo_env.token_file(url))

    def test_an_ssh_url_is_a_plain_clone(self) -> None:
        self.assertEqual(
            repo_env.clone_command("git@github.com:kunlabora/roadpass.git", "-- url dir", "dir"),
            "git clone -- url dir",
        )

    def git(self, tmp: Path, *args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp),
            "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        return subprocess.run(args, cwd=tmp, env=env, input=stdin, check=check, capture_output=True, text=True)

    def clone(self, tmp: Path, with_token: bool) -> subprocess.CompletedProcess:
        """Clone a local repository the way workspace-prep clones roadpass."""
        url = "https://github.com/kunlabora/roadpass.git"
        with mock.patch.object(repo_env, "MOUNT_ROOT", str(tmp / "repo-env")):
            path = Path(repo_env.token_file(url))
            command = repo_env.clone_command(url, f"-q -- {tmp / 'origin'} {tmp / 'checkout'}", str(tmp / "checkout"))
        if with_token:
            path.parent.mkdir(parents=True)
            path.write_text("repo-token\n")
        self.git(tmp, "git", "init", "-q", "--bare", str(tmp / "origin"))
        # The GitHub App broker, as the init container and host install it.
        for args in (("--replace-all", ""), ("--add", "!f() { echo username=app; echo password=app-token; }; f")):
            self.git(tmp, "git", "config", "--global", args[0], "credential.https://github.com.helper", args[1])
        self.git(tmp, "bash", "-c", f"set -e; {command}")
        request = "protocol=https\nhost=github.com\npath=kunlabora/roadpass.git\n\n"
        return self.git(tmp, "git", "-C", "checkout", "credential", "fill", stdin=request, check=False)

    def test_a_checkout_with_a_token_uses_only_that_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            filled = self.clone(tmp, with_token=True)
            self.assertIn("password=repo-token\n", filled.stdout)
            self.assertNotIn("repo-token", (tmp / "checkout" / ".git" / "config").read_text())
            # A removed token answers nothing, rather than the App token.
            next(tmp.glob("repo-env/*/GIT_TOKEN")).unlink()
            filled = self.git(tmp, "git", "-C", "checkout", "credential", "fill", check=False,
                              stdin="protocol=https\nhost=github.com\npath=kunlabora/roadpass.git\n\n")
            self.assertNotEqual(filled.returncode, 0)
            self.assertNotIn("app-token", filled.stdout)

    def test_a_checkout_without_a_token_keeps_the_github_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            filled = self.clone(Path(tmp), with_token=False)
            self.assertIn("password=app-token\n", filled.stdout)

    def test_the_server_reads_tokens_only_for_github_repositories(self) -> None:
        core = FakeCore()
        repo_env.save_repository(core, ROADPASS, {"GIT_TOKEN": "tok\n", "NPM": "x"})
        repo_env.save_repository(core, "github.com/kunlabora/npm-only", {"NPM": "x"})
        repo_env.save_repository(core, "gitlab.com/kunlabora/roadpass", {"GIT_TOKEN": "gl"})
        repo_env.save_repository(core, "github.com/kunlabora/blank", {"GIT_TOKEN": " "})
        # A Secret that names a repository other than the one it's mounted for.
        forged = json.loads(json.dumps(core.secrets[repo_env.secret_name(ROADPASS)]))
        forged["metadata"]["name"] = "omnigent-repo-env-forged"
        forged["metadata"]["annotations"]["omnigent.dev/repository"] = "github.com/someone/else"
        core.secrets["omnigent-repo-env-forged"] = forged
        self.assertEqual(repo_env.token_repositories(core), {ROADPASS: "tok"})
        self.assertEqual(repo_env.repository_token(core, ROADPASS), "tok")
        self.assertIsNone(repo_env.repository_token(core, "github.com/kunlabora/npm-only"))
        self.assertIsNone(repo_env.repository_token(core, "github.com/kunlabora/missing"))

    def test_the_patch_clones_with_it_and_mounts_it_for_the_clone(self) -> None:
        patch = (ROOT / "images" / "server" / "patches" / "0010-repo-git-token.patch").read_text()
        self.assertIn("+from omnigent_repo_env import clone_command as _repo_env_clone_command\n", patch)
        self.assertIn('+        "volumeMounts": [*home_mount, *repo_env_mounts],\n', patch)
        self.assertIn("+from omnigent_repo_env_api import add_token_repositories, repository_token\n", patch)
        self.assertIn("+            repo_list = await add_token_repositories(repo_list)\n", patch)
        self.assertIn("+            token = await repository_token(owner, repo) or await resolve_access_token(\n", patch)


class FakeApiException(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class FakeCore:
    """The CoreV1Api calls the store makes, on Secrets kept in memory."""

    def __init__(self) -> None:
        self.secrets: dict[str, dict] = {}
        self.versions = 0

    def _object(self, body: dict) -> SimpleNamespace:
        meta = body["metadata"]
        return SimpleNamespace(
            data=dict(body.get("data") or {}),
            metadata=SimpleNamespace(
                name=meta["name"],
                annotations=dict(meta.get("annotations") or {}),
                resource_version=meta["resourceVersion"],
                creation_timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
                managed_fields=[SimpleNamespace(time=datetime(2026, 10, 8, 12, 0))],
            ),
        )

    def _store(self, body: dict) -> SimpleNamespace:
        self.versions += 1
        body = json.loads(json.dumps(body))
        body["metadata"]["resourceVersion"] = str(self.versions)
        self.secrets[body["metadata"]["name"]] = body
        return self._object(body)

    def list_namespaced_secret(self, namespace: str, label_selector: str) -> SimpleNamespace:
        assert (namespace, label_selector) == (repo_env.NAMESPACE, repo_env.LABEL)
        return SimpleNamespace(items=[self._object(b) for b in self.secrets.values()])

    def read_namespaced_secret(self, name: str, namespace: str) -> SimpleNamespace:
        if name not in self.secrets:
            raise FakeApiException(404)
        return self._object(self.secrets[name])

    def create_namespaced_secret(self, namespace: str, body: dict) -> SimpleNamespace:
        if body["metadata"]["name"] in self.secrets:
            raise FakeApiException(409)
        return self._store(body)

    def replace_namespaced_secret(self, name: str, namespace: str, body: dict) -> SimpleNamespace:
        if self.secrets[name]["metadata"]["resourceVersion"] != body["metadata"].get("resourceVersion"):
            raise FakeApiException(409)
        return self._store(body)

    def delete_namespaced_secret(self, name: str, namespace: str) -> None:
        if self.secrets.pop(name, None) is None:
            raise FakeApiException(404)


ROADPASS = "github.com/kunlabora/roadpass"


def decoded(core: FakeCore, key: str = ROADPASS) -> dict[str, str]:
    data = core.secrets[repo_env.secret_name(key)]["data"]
    return {name: base64.b64decode(value).decode() for name, value in data.items()}


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = FakeCore()

    def test_saves_a_secret_the_pod_and_the_cli_agree_on(self) -> None:
        entry = repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "abc\n", "URL": "https://x"}, updated_by="levi")
        secret = self.core.secrets[repo_env.secret_name(ROADPASS)]
        self.assertEqual(secret["metadata"]["namespace"], "omnigent-sandboxes")
        self.assertEqual(secret["metadata"]["labels"], {"omnigent.dev/repo-env": "true"})
        self.assertEqual(secret["metadata"]["annotations"], {
            "omnigent.dev/repository": ROADPASS, "omnigent.dev/updated-by": "levi",
        })
        self.assertEqual(decoded(self.core), {"TOKEN": "abc\n", "URL": "https://x"})
        self.assertEqual(entry, {
            "repository": ROADPASS, "names": ["TOKEN", "URL"],
            "updated_at": "2026-10-08T12:00:00+00:00", "updated_by": "levi",
        })

    def test_a_name_without_a_value_keeps_it_and_left_out_names_go(self) -> None:
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "abc", "OLD": "x", "URL": "u"})
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": None, "URL": "new", "ADDED": ""})
        self.assertEqual(decoded(self.core), {"TOKEN": "abc", "URL": "new", "ADDED": ""})

    def test_rejects_unknown_kept_names_bad_names_and_nothing(self) -> None:
        with self.assertRaisesRegex(ValueError, "TOKEN has no value yet"):
            repo_env.save_repository(self.core, ROADPASS, {"TOKEN": None})
        with self.assertRaisesRegex(ValueError, "reserved"):
            repo_env.save_repository(self.core, ROADPASS, {"PATH": "/bin"})
        with self.assertRaisesRegex(ValueError, "at least one"):
            repo_env.save_repository(self.core, ROADPASS, {})
        self.assertEqual(self.core.secrets, {})

    def test_a_save_based_on_an_older_read_conflicts(self) -> None:
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "abc"})
        stale = self.core.read_namespaced_secret
        old = stale(repo_env.secret_name(ROADPASS), repo_env.NAMESPACE)
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "newer"})
        with mock.patch.object(self.core, "read_namespaced_secret", return_value=old):
            with self.assertRaises(FakeApiException) as raised:
                repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "older"})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(decoded(self.core), {"TOKEN": "newer"})

    def test_lists_names_never_values(self) -> None:
        repo_env.save_repository(self.core, "github.com/n3bby/omnigent", {"ZONE": "z"})
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "abc"})
        listed = repo_env.list_repositories(self.core)
        self.assertEqual([e["repository"] for e in listed], [ROADPASS, "github.com/n3bby/omnigent"])
        self.assertNotIn("abc", json.dumps(listed))
        self.assertEqual(repo_env.variable_names(self.core, ROADPASS), ["TOKEN"])
        self.assertEqual(repo_env.variable_names(self.core, "github.com/a/b"), [])

    def test_delete_is_fine_when_nothing_is_there(self) -> None:
        repo_env.save_repository(self.core, ROADPASS, {"TOKEN": "abc"})
        repo_env.delete_repository(self.core, ROADPASS)
        repo_env.delete_repository(self.core, ROADPASS)
        self.assertEqual(self.core.secrets, {})


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
