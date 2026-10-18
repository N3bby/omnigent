"""Environment variables for the repositories a session starts with.

A repository's variables live in one Kubernetes Secret in the runner
namespace, one key per variable. The Secret's name comes from the repository,
so the server finds it from the clone URL alone and mounts it into the runner
Pod, and the runner's agent wrappers export each file as a variable.

The server image installs this file as the top-level ``omnigent_repo_env``
module, so the Pod and the Settings page derive the same Secret name from the
same repository. The Settings page (``omnigent_repo_env_api``) reads and
writes the Secrets with the functions at the end, which take a Kubernetes
``CoreV1Api``.

The same Secret can hold the repository's own agent instructions and a setup
script, under keys that aren't variable names, so the loader skips them. The
runner appends the instructions to its own and runs the script in the
checkout (``images/runner/repo-env/``). It finds the checkout from
``OMNIGENT_REPO_CHECKOUTS``, which the server sets on the runner Pod.

It can also hold the repository's own MCP servers, which Settings → MCP
servers edits (``omnigent_mcp_servers``). Saving the variables keeps them.

A repository's own Git credentials are its ``GIT_TOKEN`` variable, for HTTPS
URLs, and an SSH private key under ``git-ssh-key``, for SSH URLs.
:func:`clone_command` clones with them, and the checkout keeps using them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import shlex
from collections.abc import Iterable, Mapping
from datetime import timezone
from typing import Any
from urllib.parse import urlsplit

NAMESPACE = "omnigent-sandboxes"
# Marks a Secret as a repository's variables, for listing them.
LABEL = "omnigent.dev/repo-env"
# The normalised repository, e.g. github.com/kunlabora/roadpass.
ANNOTATION = "omnigent.dev/repository"
# Who last saved the variables from the Settings page.
UPDATED_BY_ANNOTATION = "omnigent.dev/updated-by"
# Each repository's Secret is mounted at MOUNT_ROOT/<Secret name>.
MOUNT_ROOT = "/run/omnigent/repo-env"
DEFAULT_HOST = "github.com"
# A repository with this variable is cloned, fetched and pushed with it
# instead of the user's GitHub App token, and is listed in the repository
# picker for everyone who has connected GitHub.
TOKEN_VARIABLE = "GIT_TOKEN"
# The repository's own agent instructions, which the runner appends to its
# own, and the script it runs in the checkout before the agent starts. A
# variable name can't contain a dot, so these never clash with a variable.
INSTRUCTIONS_KEY = "agent-instructions.md"
SETUP_SCRIPT_KEY = "setup.sh"
_TEXT_KEYS = (INSTRUCTIONS_KEY, SETUP_SCRIPT_KEY)
# The repository's MCP servers, as JSON. Settings → MCP servers edits them,
# not this module, so saving the variables keeps them as they are.
MCP_SERVERS_KEY = "mcp-servers.json"
# A private key that clones, fetches and pushes the repository's SSH URLs,
# such as a deploy key. Write-only, like a variable's value, but not a
# variable name, so the agent doesn't get it in its environment.
SSH_KEY_KEY = "git-ssh-key"
_NOT_VARIABLES = (*_TEXT_KEYS, MCP_SERVERS_KEY, SSH_KEY_KEY)
# Agents read the instructions at the start of every session.
TEXT_LIMIT = 64 * 1024
# An RSA key of 16384 bits is under 13 KB.
SSH_KEY_LIMIT = 16 * 1024
# On the runner Pod's host container: one line per repository the session
# starts with, "<Secret name>\t<repository>\t<checkout>".
CHECKOUTS_ENV = "OMNIGENT_REPO_CHECKOUTS"
# Passed to save_repository to leave the instructions or the script as they are.
KEEP: Any = object()

_PREFIX = "omnigent-repo-env-"
_HASH_LENGTH = 8
# Keeps the name a valid DNS label (63 characters) with room for the hash.
_SLUG_LENGTH = 63 - len(_PREFIX) - 1 - _HASH_LENGTH
_SEGMENT = re.compile(r"^[a-z0-9._-]+$")
_SCP_URL = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")
_PRIVATE_KEY = re.compile(
    r"^-----BEGIN (?P<kind>[A-Z0-9 ]*)PRIVATE KEY-----\n(?P<body>.*)\n-----END (?P=kind)PRIVATE KEY-----$",
    re.DOTALL,
)
_OPENSSH_MAGIC = b"openssh-key-v1\0"
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# The runner and its agents rely on these, so a repository can't override them.
_RESERVED_NAMES = {"HOME", "PATH", "SHELL", "USER", "LOGNAME", "PWD", "TMPDIR"}
_RESERVED_PREFIXES = ("OMNIGENT_", "CLAUDE_", "CODEX_", "MISE_", "TAILSCALE_")


def repo_key(repository: str) -> str:
    """
    Normalise a repository to ``host/owner/repo``, in lower case.

    Every way of writing the same repository gives the same key: HTTPS and SSH
    clone URLs, with or without ``.git`` or a trailing slash, in any case. A
    bare ``owner/repo`` means a repository on github.com.

    :param repository: e.g. ``"git@github.com:Kunlabora/RoadPass.git"`` or
        ``"kunlabora/roadpass"``.
    :returns: e.g. ``"github.com/kunlabora/roadpass"``.
    :raises ValueError: When it isn't a repository URL or ``owner/repo``.
    """
    text = repository.strip()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https", "ssh", "git"}:
            raise ValueError(f"unsupported URL scheme in {repository!r}")
        host, path = parts.hostname or "", parts.path
    elif match := _SCP_URL.match(text):
        host, path = match["host"], match["path"]
    else:
        segments = text.strip("/").split("/")
        # owner/repo, or host/owner/repo when the first part looks like a host.
        if len(segments) >= 3 and "." in segments[0]:
            host, path = segments[0], "/".join(segments[1:])
        else:
            host, path = DEFAULT_HOST, text
    path = path.strip("/").lower()
    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = path.split("/")
    host = host.lower()
    if not host or not _SEGMENT.match(host):
        raise ValueError(f"no host in {repository!r}")
    if len(segments) < 2 or not all(_SEGMENT.match(s) and s not in {".", ".."} for s in segments):
        raise ValueError(f"expected owner/repo in {repository!r}")
    return f"{host}/{'/'.join(segments)}"


def secret_name(key: str) -> str:
    """
    The Secret holding a repository's variables.

    A readable part from the path, plus a hash of the whole key. The hash
    keeps apart repositories whose readable parts match, such as ``my_repo``
    and ``my.repo``, or the same path on two hosts.

    :param key: A key from :func:`repo_key`, e.g.
        ``"github.com/kunlabora/roadpass"``.
    :returns: e.g. ``"omnigent-repo-env-kunlabora-roadpass-1a2b3c4d"``.
    """
    path = key.split("/", 1)[1]
    slug = re.sub(r"[^a-z0-9]+", "-", path).strip("-")[:_SLUG_LENGTH].strip("-")
    digest = hashlib.sha256(key.encode()).hexdigest()[:_HASH_LENGTH]
    return f"{_PREFIX}{slug}-{digest}" if slug else f"{_PREFIX}{digest}"


def check_variable_name(name: str) -> None:
    """
    Reject a name a shell can't export or that the runner relies on.

    :param name: e.g. ``"GOVFLANDERS_NPM_TOKEN"``.
    :raises ValueError: When the name can't be used.
    """
    if not VARIABLE_NAME.match(name):
        raise ValueError(f"{name!r} is not a valid variable name")
    if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
        raise ValueError(f"{name} is reserved for the runner")


def parse_env(text: str) -> dict[str, str]:
    """
    Parse ``NAME=value`` lines, as in a ``.env`` file.

    Blank lines and ``#`` comments are skipped, a leading ``export`` is
    allowed, and one pair of matching quotes around a value is removed.
    Nothing is expanded: ``$OTHER`` stays as written.

    :param text: e.g. ``"export TOKEN='abc'\\n# comment\\n"``.
    :returns: e.g. ``{"TOKEN": "abc"}``.
    :raises ValueError: On a line that isn't ``NAME=value``, a name
        :func:`check_variable_name` rejects, or a name given twice.
    """
    variables: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep:
            raise ValueError(f"line {number}: expected NAME=value")
        try:
            check_variable_name(name)
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from None
        if name in variables:
            raise ValueError(f"line {number}: {name} is set twice")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        variables[name] = value
    return variables


def check_ssh_key(text: str) -> str:
    """
    A pasted SSH private key, as ``ssh`` reads it.

    ``ssh`` can't ask for a passphrase in a session, so a key with one is
    refused here instead of failing the clone.

    :param text: e.g. ``"-----BEGIN OPENSSH PRIVATE KEY-----\\n…"``.
    :returns: The key with Unix line ends and the final newline ``ssh`` needs.
    :raises ValueError: For a public key, anything that isn't a private key,
        a key with a passphrase, or more than :data:`SSH_KEY_LIMIT` bytes.
    """
    key = text.replace("\r\n", "\n").strip()
    if len(key.encode()) > SSH_KEY_LIMIT:
        raise ValueError(f"an SSH key can be at most {SSH_KEY_LIMIT // 1024} KB")
    if re.match(r"^(ssh|ecdsa|sk)-[a-z0-9@.-]+ ", key):
        raise ValueError("that's a public key: paste the private key, which starts with -----BEGIN")
    match = _PRIVATE_KEY.match(key)
    if match is None:
        raise ValueError("an SSH key starts with -----BEGIN … PRIVATE KEY----- and ends with -----END … PRIVATE KEY-----")
    body = match["body"]
    if match["kind"] == "ENCRYPTED " or "Proc-Type: 4,ENCRYPTED" in body:
        raise ValueError("that key has a passphrase: sessions can't type it, so use a key without one")
    if match["kind"] == "OPENSSH ":
        try:
            blob = base64.b64decode("".join(body.split()), validate=True)
        except binascii.Error as exc:
            raise ValueError("that SSH key is damaged: copy it again") from exc
        # The cipher follows the magic as a length-prefixed string.
        if not blob.startswith(_OPENSSH_MAGIC) or len(blob) < len(_OPENSSH_MAGIC) + 4:
            raise ValueError("that SSH key is damaged: copy it again")
        length = int.from_bytes(blob[len(_OPENSSH_MAGIC) : len(_OPENSSH_MAGIC) + 4], "big")
        start = len(_OPENSSH_MAGIC) + 4
        if blob[start : start + length] != b"none":
            raise ValueError("that key has a passphrase: sessions can't type it, so use a key without one")
    return key + "\n"


def pod_volumes(urls: Iterable[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """
    The volumes and mounts that give a runner Pod its repositories' variables.

    Each Secret is optional, so a repository without one adds a volume that
    mounts nothing, and the Pod starts as usual. A URL that can't be
    normalised is skipped rather than failing the launch.

    :param urls: The clone URLs the session starts with.
    :returns: ``(volumes, volume_mounts)`` for the Pod and its host container.
    """
    volumes: list[dict[str, object]] = []
    mounts: list[dict[str, object]] = []
    names: list[str] = []
    for url in urls:
        try:
            name = secret_name(repo_key(url))
        except ValueError:
            continue
        if name not in names:
            names.append(name)
    for i, name in enumerate(names):
        volume = f"repo-env-{i}"
        volumes.append(
            {
                "name": volume,
                "secret": {"secretName": name, "optional": True, "defaultMode": 0o440},
            }
        )
        mounts.append({"name": volume, "mountPath": f"{MOUNT_ROOT}/{name}", "readOnly": True})
    return volumes, mounts


def checkouts_env(checkouts: Iterable[tuple[str, str]]) -> dict[str, str] | None:
    """
    The runner Pod's ``OMNIGENT_REPO_CHECKOUTS``: where each repository is checked out.

    The runner runs a repository's setup script in its checkout, and names the
    repository and checkout above its instructions. A URL that can't be
    normalised is skipped, like in :func:`pod_volumes`.

    :param checkouts: ``(clone URL, checkout directory)`` for each repository
        the session starts with.
    :returns: The env entry for the host container, or ``None`` without any
        repository.
    """
    lines = []
    for url, directory in checkouts:
        try:
            key = repo_key(url)
        except ValueError:
            continue
        if "\t" in directory or "\n" in directory:
            continue
        lines.append(f"{secret_name(key)}\t{key}\t{directory}")
    return {"name": CHECKOUTS_ENV, "value": "\n".join(lines)} if lines else None


def token_file(url: str) -> str | None:
    """
    Where a runner Pod finds a repository's ``GIT_TOKEN``, if it has one.

    Only HTTPS clone URLs use a token; an SSH URL authenticates with keys.

    :param url: e.g. ``"https://github.com/kunlabora/roadpass.git"``.
    :returns: e.g. ``"/run/omnigent/repo-env/omnigent-repo-env-kunlabora-roadpass-1a2b3c4d/GIT_TOKEN"``,
        or ``None`` for an SSH or unusable URL.
    """
    if not url.strip().lower().startswith("https://"):
        return None
    try:
        name = secret_name(repo_key(url))
    except ValueError:
        return None
    return f"{MOUNT_ROOT}/{name}/{TOKEN_VARIABLE}"


def ssh_key_file(url: str) -> str | None:
    """
    Where a runner Pod finds a repository's SSH key, if it has one.

    Only SSH clone URLs use a key; an HTTPS URL uses a token.

    :param url: e.g. ``"git@github.com:kunlabora/roadpass.git"``.
    :returns: e.g. ``"/run/omnigent/repo-env/omnigent-repo-env-kunlabora-roadpass-1a2b3c4d/git-ssh-key"``,
        or ``None`` for an HTTPS or unusable URL.
    """
    text = url.strip()
    if "://" in text:
        if not text.lower().startswith("ssh://"):
            return None
    elif not _SCP_URL.match(text):
        return None
    try:
        name = secret_name(repo_key(text))
    except ValueError:
        return None
    return f"{MOUNT_ROOT}/{name}/{SSH_KEY_KEY}"


def ssh_command(path: str) -> str:
    """
    A ``core.sshCommand`` that connects with the SSH key in ``path``.

    ``ssh`` refuses a key that others may read, and a mounted Secret's files
    are readable by their group, so each connection copies the key to a
    private temporary file and removes it afterwards. Without the file, it's
    plain ``ssh``. A new host's key is accepted the first time.

    :param path: A path from :func:`ssh_key_file`.
    :returns: The ``core.sshCommand`` value, for git to run with a shell.
    """
    options = "-o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    return (
        f'f() {{ if [ -r {path} ]; then k=$(mktemp) && cat {path} > "$k" '
        f'&& ssh -i "$k" {options} "$@"; r=$?; rm -f "$k"; return $r; fi; ssh "$@"; }}; f'
    )


def credential_helper(path: str) -> str:
    """
    A git credential helper that answers with the token in ``path``.

    It reads the file on every use, so a changed token reaches running
    sessions, and answers nothing once the file is gone.

    :param path: A path from :func:`token_file`.
    :returns: The ``credential.helper`` value.
    """
    return (
        f'!f() {{ if [ "$1" = get ] && [ -r {path} ]; then '
        f"printf 'username=x-access-token\\npassword=%s\\n' \"$(cat {path})\"; fi; }}; f"
    )


def clone_command(url: str, args: str, directory: str) -> str:
    """
    The shell command that clones a repository, with its own credentials if it has them.

    With a token, the clone uses only that token, not the user's GitHub App
    token, and the checkout keeps using it: its own config clears the
    helpers inherited from the global config and adds one that reads the
    token. An SSH URL with an SSH key clones with that key, and the
    checkout's own ``core.sshCommand`` keeps using it. Without either, it's a
    plain ``git clone``. The files are checked when the command runs, because
    the Secret is mounted optionally.

    :param url: The clone URL.
    :param args: What follows ``git clone``, quoted for the shell, e.g.
        ``"-- https://github.com/kunlabora/roadpass.git /home/omnigent/workspace/roadpass.tmp/clone"``.
    :param directory: The checkout ``args`` clones into, quoted for the shell.
    :returns: A shell command.
    """
    key = ssh_key_file(url)
    if key is not None:
        # ssh.variant skips git's probe of what kind of ssh the function is.
        command = shlex.quote(ssh_command(key))
        return (
            f"if [ -r {shlex.quote(key)} ]; then "
            f"git -c core.sshCommand={command} -c ssh.variant=ssh clone {args} "
            f"&& git -C {directory} config core.sshCommand {command} "
            f"&& git -C {directory} config ssh.variant ssh; "
            f"else git clone {args}; fi"
        )
    path = token_file(url)
    if path is None:
        return f"git clone {args}"
    helper = shlex.quote(credential_helper(path))
    return (
        f"if [ -r {shlex.quote(path)} ]; then "
        f"git -c credential.helper= -c credential.helper={helper} clone {args} "
        f"&& git -C {directory} config credential.helper '' "
        f"&& git -C {directory} config --add credential.helper {helper}; "
        f"else git clone {args}; fi"
    )


def _not_found(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 404


def _updated_at(secret: Any) -> str | None:
    """When the Secret last changed: its newest managed-fields entry."""
    times = [entry.time for entry in secret.metadata.managed_fields or () if entry.time]
    latest = max(times, default=secret.metadata.creation_timestamp)
    if latest is None:
        return None
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest.isoformat()


def _names(secret: Any) -> list[str]:
    """The names of the Secret's variables, without its instructions, script and MCP servers."""
    return sorted(name for name in secret.data or {} if name not in _NOT_VARIABLES)


def mcp_server_names(secret: Any) -> list[str]:
    """The names of the repository's MCP servers, sorted."""
    text = _text(secret, MCP_SERVERS_KEY)
    if text is None:
        return []
    return sorted(server["name"] for server in json.loads(text).get("servers", []))


def mcp_server_badges(secret: Any) -> list[dict[str, Any]]:
    """
    The repository's MCP servers as the new-session form shows them, by name.

    Anyone signed in reads these, so only what tells a server apart: its
    name, its agents and an HTTP server's host, which picks its logo. Never
    headers, arguments or variables, which can hold keys.
    """
    text = _text(secret, MCP_SERVERS_KEY)
    if text is None:
        return []
    servers = sorted(json.loads(text).get("servers", []), key=lambda s: s["name"].casefold())
    return [
        {
            "name": server["name"],
            "agents": server["agents"],
            "host": urlsplit(server["url"]).hostname if server.get("url") else None,
        }
        for server in servers
    ]


def _text(secret: Any, key: str) -> str | None:
    value = (secret.data or {}).get(key)
    return base64.b64decode(value).decode() if value else None


def _entry(secret: Any) -> dict[str, Any]:
    annotations = secret.metadata.annotations or {}
    return {
        "repository": annotations.get(ANNOTATION, secret.metadata.name),
        "names": _names(secret),
        "instructions": _text(secret, INSTRUCTIONS_KEY),
        "setup_script": _text(secret, SETUP_SCRIPT_KEY),
        "mcp_servers": mcp_server_names(secret),
        "ssh_key": SSH_KEY_KEY in (secret.data or {}),
        "updated_at": _updated_at(secret),
        "updated_by": annotations.get(UPDATED_BY_ANNOTATION),
    }


def list_repositories(core: Any) -> list[dict[str, Any]]:
    """
    Every repository with variables, with the variables' names.

    :param core: A Kubernetes ``CoreV1Api``.
    :returns: e.g. ``[{"repository": "github.com/kunlabora/roadpass",
        "names": ["GOVFLANDERS_NPM_TOKEN"], "instructions": "Use pnpm…",
        "setup_script": None, "mcp_servers": ["figma"], "ssh_key": False,
        "updated_at": "2026-10-08T…", "updated_by": "levi"}]``, sorted by
        repository. Never a variable's value or the SSH key; the instructions
        and the script come back to be edited.
    """
    secrets = core.list_namespaced_secret(NAMESPACE, label_selector=LABEL).items
    return sorted((_entry(s) for s in secrets), key=lambda entry: entry["repository"])


def _token(secret: Any) -> str | None:
    value = (secret.data or {}).get(TOKEN_VARIABLE)
    token = base64.b64decode(value).decode().strip() if value else ""
    return token or None


def picker_repositories(core: Any) -> list[dict[str, Any]]:
    """
    Every repository with variables, on any host, for the repository picker.

    Anyone signed in may start a session on these, so they get the
    repository and how it's cloned, never a value.

    :param core: A Kubernetes ``CoreV1Api``.
    :returns: e.g. ``[{"repository": "gitlab.com/acme/api",
        "clone_url": "git@gitlab.com:acme/api.git", "git_token": False,
        "ssh_key": True}]``, sorted by repository. A repository with only an
        SSH key gets its SSH URL; every other one its HTTPS URL.
    """
    entries = []
    for secret in core.list_namespaced_secret(NAMESPACE, label_selector=LABEL).items:
        key = (secret.metadata.annotations or {}).get(ANNOTATION, "")
        # Only the Secret a Pod would mount for it.
        try:
            if secret.metadata.name != secret_name(key):
                continue
        except (IndexError, ValueError):
            continue
        data = secret.data or {}
        host, path = key.split("/", 1)
        token, ssh_key = _token(secret) is not None, SSH_KEY_KEY in data
        clone_url = f"git@{host}:{path}.git" if ssh_key and not token else f"https://{host}/{path}.git"
        entries.append({"repository": key, "clone_url": clone_url, "git_token": token, "ssh_key": ssh_key})
    return sorted(entries, key=lambda entry: entry["repository"])


def token_repositories(core: Any) -> dict[str, str]:
    """
    The github.com repositories with a ``GIT_TOKEN``, and their tokens.

    For the server only, to list them in the repository picker. The tokens
    never leave the server.

    :param core: A Kubernetes ``CoreV1Api``.
    :returns: e.g. ``{"github.com/kunlabora/roadpass": "github_pat_…"}``.
    """
    tokens: dict[str, str] = {}
    for secret in core.list_namespaced_secret(NAMESPACE, label_selector=LABEL).items:
        key = (secret.metadata.annotations or {}).get(ANNOTATION, "")
        # Only owner/repo on github.com, and only the Secret a Pod would
        # mount for it.
        if not key.startswith(f"{DEFAULT_HOST}/") or key.count("/") != 2:
            continue
        if secret.metadata.name != secret_name(key) or (token := _token(secret)) is None:
            continue
        tokens[key] = token
    return tokens


def repository_token(core: Any, key: str) -> str | None:
    """
    A repository's ``GIT_TOKEN``, or ``None``.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    """
    try:
        secret = core.read_namespaced_secret(secret_name(key), NAMESPACE)
    except Exception as exc:
        if _not_found(exc):
            return None
        raise
    return _token(secret)


def variable_names(core: Any, key: str) -> list[str]:
    """
    The names of a repository's variables, or none.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    """
    return repository_summary(core, key)["names"]


def repository_summary(core: Any, key: str) -> dict[str, Any]:
    """
    What sessions on a repository get, without any value or text.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    :returns: e.g. ``{"names": ["GOVFLANDERS_NPM_TOKEN"], "instructions": True,
        "setup_script": False, "mcp_servers": [{"name": "figma",
        "agents": ["claude", "codex"], "host": "mcp.figma.com"}], "ssh_key": False}``.
    """
    try:
        secret = core.read_namespaced_secret(secret_name(key), NAMESPACE)
    except Exception as exc:
        if _not_found(exc):
            return {"names": [], "instructions": False, "setup_script": False, "mcp_servers": [],
                    "ssh_key": False}
        raise
    data = secret.data or {}
    return {
        "names": _names(secret),
        "instructions": INSTRUCTIONS_KEY in data,
        "setup_script": SETUP_SCRIPT_KEY in data,
        "mcp_servers": mcp_server_badges(secret),
        "ssh_key": SSH_KEY_KEY in data,
    }


def _clean_text(text: str | None, what: str) -> str | None:
    """Text as stored: Unix line ends, or ``None`` when there's nothing but space."""
    if text is None or not text.strip():
        return None
    text = text.replace("\r\n", "\n")
    if len(text.encode()) > TEXT_LIMIT:
        raise ValueError(f"the {what} can be at most {TEXT_LIMIT // 1024} KB")
    return text


def save_repository(
    core: Any,
    key: str,
    variables: Mapping[str, str | None],
    updated_by: str | None = None,
    *,
    instructions: str | None = KEEP,
    setup_script: str | None = KEEP,
    ssh_key: str | None = KEEP,
) -> dict[str, Any]:
    """
    Store a repository's variables, replacing the ones it had.

    A name without a value (``None``) keeps that variable's current value, so
    a value never has to come back to the browser to be kept. Names that
    aren't given are removed.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    :param variables: e.g. ``{"GOVFLANDERS_NPM_TOKEN": None, "NEW": "value"}``.
    :param updated_by: The user saving them, kept as an annotation.
    :param instructions: The repository's agent instructions; ``None`` or
        blank removes them, and :data:`KEEP` leaves them as they are.
    :param setup_script: The repository's setup script, likewise.
    :param ssh_key: The repository's SSH private key; ``None`` removes it, and
        :data:`KEEP` leaves it as it is.
    :returns: The repository's entry, as in :func:`list_repositories`.
    :raises ValueError: When there'd be nothing left to store (the
        repository's MCP servers count), on a name
        :func:`check_variable_name` rejects, ``None`` for a variable the
        repository doesn't have, text over :data:`TEXT_LIMIT` bytes, or an
        SSH key :func:`check_ssh_key` rejects.
    """
    for name in variables:
        check_variable_name(name)
    if ssh_key is not KEEP and ssh_key is not None:
        ssh_key = check_ssh_key(ssh_key)
    texts = {
        INSTRUCTIONS_KEY: instructions if instructions is KEEP else _clean_text(instructions, "agent instructions"),
        SETUP_SCRIPT_KEY: setup_script if setup_script is KEEP else _clean_text(setup_script, "setup script"),
    }
    name = secret_name(key)
    try:
        current = core.read_namespaced_secret(name, NAMESPACE)
    except Exception as exc:
        if not _not_found(exc):
            raise
        current = None
    current_data = (current.data or {}) if current is not None else {}
    data: dict[str, str] = {}
    for variable, value in variables.items():
        if value is not None:
            data[variable] = base64.b64encode(value.encode()).decode()
        elif variable in current_data:
            data[variable] = current_data[variable]
        else:
            raise ValueError(f"{variable} has no value yet")
    if MCP_SERVERS_KEY in current_data:
        data[MCP_SERVERS_KEY] = current_data[MCP_SERVERS_KEY]
    if ssh_key is KEEP:
        if SSH_KEY_KEY in current_data:
            data[SSH_KEY_KEY] = current_data[SSH_KEY_KEY]
    elif ssh_key is not None:
        data[SSH_KEY_KEY] = base64.b64encode(ssh_key.encode()).decode()
    for text_key, text in texts.items():
        if text is KEEP:
            if text_key in current_data:
                data[text_key] = current_data[text_key]
        elif text is not None:
            data[text_key] = base64.b64encode(text.encode()).decode()
    if not data:
        raise ValueError(
            "give at least one variable, agent instructions or a setup script, "
            "or remove the repository"
        )
    return _write(core, key, current, data, updated_by)


def save_credentials(
    core: Any,
    key: str,
    *,
    token: str | None = None,
    ssh_key: str | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """
    Give a repository its own Git token or SSH key, keeping everything else.

    For the new-session form, which adds a repository with its credentials
    without knowing what else it has. A token or key it had is replaced.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    :param token: The new ``GIT_TOKEN``, or ``None`` to leave it.
    :param ssh_key: The new SSH private key, or ``None`` to leave it.
    :param updated_by: The user saving them, kept as an annotation.
    :returns: The repository's entry, as in :func:`list_repositories`.
    :raises ValueError: Without a token or key, for a blank token, or for an
        SSH key :func:`check_ssh_key` rejects.
    """
    if token is None and ssh_key is None:
        raise ValueError("give a Git token or an SSH key")
    if token is not None and not token.strip():
        raise ValueError("the Git token is blank")
    if ssh_key is not None:
        ssh_key = check_ssh_key(ssh_key)
    name = secret_name(key)
    try:
        current = core.read_namespaced_secret(name, NAMESPACE)
    except Exception as exc:
        if not _not_found(exc):
            raise
        current = None
    data = dict((current.data or {}) if current is not None else {})
    if token is not None:
        data[TOKEN_VARIABLE] = base64.b64encode(token.strip().encode()).decode()
    if ssh_key is not None:
        data[SSH_KEY_KEY] = base64.b64encode(ssh_key.encode()).decode()
    return _write(core, key, current, data, updated_by)


def _write(core: Any, key: str, current: Any, data: dict[str, str], updated_by: str | None) -> dict[str, Any]:
    """Create the repository's Secret, or replace ``current`` unless someone changed it since."""
    name = secret_name(key)
    annotations = {ANNOTATION: key}
    if updated_by:
        annotations[UPDATED_BY_ANNOTATION] = updated_by
    metadata: dict[str, Any] = {
        "name": name,
        "namespace": NAMESPACE,
        "labels": {LABEL: "true"},
        "annotations": annotations,
    }
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": metadata, "data": data}
    if current is None:
        saved = core.create_namespaced_secret(NAMESPACE, body)
    else:
        # A save based on an older read fails with 409 instead of undoing
        # someone else's change.
        metadata["resourceVersion"] = current.metadata.resource_version
        saved = core.replace_namespaced_secret(name, NAMESPACE, body)
    return _entry(saved)


def delete_repository(core: Any, key: str) -> None:
    """
    Remove a repository's variables. Removing what isn't there is fine.

    :param core: A Kubernetes ``CoreV1Api``.
    :param key: A key from :func:`repo_key`.
    """
    try:
        core.delete_namespaced_secret(secret_name(key), NAMESPACE)
    except Exception as exc:
        if not _not_found(exc):
            raise
