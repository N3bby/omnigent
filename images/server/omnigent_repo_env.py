"""Environment variables for the repositories a session starts with.

A repository's variables live in one Kubernetes Secret in the runner
namespace, one key per variable. The Secret's name comes from the repository,
so the server finds it from the clone URL alone and mounts it into the runner
Pod, and the runner's agent wrappers export each file as a variable.

The server image installs this file as the top-level ``omnigent_repo_env``
module, and ``scripts/setup-repo-env`` imports it from here, so both sides
derive the same Secret name from the same repository. The server's Settings
page (``omnigent_repo_env_api``) reads and writes the Secrets with the
functions at the end, which take a Kubernetes ``CoreV1Api``.
"""

from __future__ import annotations

import base64
import hashlib
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

_PREFIX = "omnigent-repo-env-"
_HASH_LENGTH = 8
# Keeps the name a valid DNS label (63 characters) with room for the hash.
_SLUG_LENGTH = 63 - len(_PREFIX) - 1 - _HASH_LENGTH
_SEGMENT = re.compile(r"^[a-z0-9._-]+$")
_SCP_URL = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")
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
    The shell command that clones a repository, with its ``GIT_TOKEN`` if it has one.

    With a token, the clone uses only that token, not the user's GitHub App
    token, and the checkout keeps using it: its own config clears the
    helpers inherited from the global config and adds one that reads the
    token. Without one, it's a plain ``git clone``. The token file is checked
    when the command runs, because the Secret is mounted optionally.

    :param url: The clone URL.
    :param args: What follows ``git clone``, quoted for the shell, e.g.
        ``"-- https://github.com/kunlabora/roadpass.git /home/omnigent/workspace/roadpass.tmp/clone"``.
    :param directory: The checkout ``args`` clones into, quoted for the shell.
    :returns: A shell command.
    """
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


def _entry(secret: Any) -> dict[str, Any]:
    annotations = secret.metadata.annotations or {}
    return {
        "repository": annotations.get(ANNOTATION, secret.metadata.name),
        "names": sorted(secret.data or {}),
        "updated_at": _updated_at(secret),
        "updated_by": annotations.get(UPDATED_BY_ANNOTATION),
    }


def list_repositories(core: Any) -> list[dict[str, Any]]:
    """
    Every repository with variables, with the variables' names.

    :param core: A Kubernetes ``CoreV1Api``.
    :returns: e.g. ``[{"repository": "github.com/kunlabora/roadpass",
        "names": ["GOVFLANDERS_NPM_TOKEN"], "updated_at": "2026-10-08T…",
        "updated_by": "levi"}]``, sorted by repository. Never values.
    """
    secrets = core.list_namespaced_secret(NAMESPACE, label_selector=LABEL).items
    return sorted((_entry(s) for s in secrets), key=lambda entry: entry["repository"])


def _token(secret: Any) -> str | None:
    value = (secret.data or {}).get(TOKEN_VARIABLE)
    token = base64.b64decode(value).decode().strip() if value else ""
    return token or None


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
    try:
        secret = core.read_namespaced_secret(secret_name(key), NAMESPACE)
    except Exception as exc:
        if _not_found(exc):
            return []
        raise
    return sorted(secret.data or {})


def save_repository(
    core: Any,
    key: str,
    variables: Mapping[str, str | None],
    updated_by: str | None = None,
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
    :returns: The repository's entry, as in :func:`list_repositories`.
    :raises ValueError: On an empty set, a name :func:`check_variable_name`
        rejects, or ``None`` for a variable the repository doesn't have.
    """
    if not variables:
        raise ValueError("give at least one variable, or remove the repository")
    for name in variables:
        check_variable_name(name)
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
