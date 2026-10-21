"""MCP servers for runners' Claude Code and Codex, behind Settings → MCP servers.

A server is global, for every session, or for sessions that start with one
of its repositories. The global ones are ``servers.json`` in their own
Secret, which every runner Pod mounts read-only at ``MOUNT``. A repository's
are ``mcp-servers.json`` in its variables Secret (``omnigent_repo_env``),
which only its sessions' Pods mount, so their keys stay with the repository.
A server for several repositories is a copy in each of their Secrets, all with
the same id, which the page lists and edits as one server. The runner's agent
wrappers read both when an agent starts (``images/runner/mcp-servers.py``); a
repository's server replaces a global one of the same name.

Header and environment values are write-only: listing returns their names,
and saving a name with ``None`` keeps its value. Kubernetes' resourceVersion
keeps two admins from overwriting each other's edits.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import omnigent_repo_env as repo_env

NAMESPACE = "omnigent-sandboxes"
SECRET = "omnigent-mcp-servers"
KEY = "servers.json"
MOUNT = "/run/omnigent/mcp-servers"
AGENTS = ("claude", "codex")
TRANSPORTS = ("http", "stdio")

# Codex only takes these in a server's name, and both agents prefix the
# server's tools with it. Omnigent's own server is already called omnigent.
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESERVED_NAMES = {"omnigent"}
HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_SERVERS = 32
MAX_REPOSITORIES = 64
MAX_PAIRS = 64
MAX_ARGS = 64
VALUE_LIMIT = 32 * 1024
SECRET_LIMIT = 900 * 1024


def pod_volumes():
    """The global servers' volume and the host container's mount, for every runner Pod."""
    return ([{"name": "mcp-servers", "secret": {
        "secretName": SECRET, "optional": True, "defaultMode": 0o440,
        "items": [{"key": KEY, "path": KEY}],
    }}], [{"name": "mcp-servers", "mountPath": MOUNT, "readOnly": True}])


def _place(repository):
    """The Secret and key holding the servers of ``repository``, or the global ones for ``None``."""
    if repository is None:
        return SECRET, KEY
    return repo_env.secret_name(repository), repo_env.MCP_SERVERS_KEY


def _servers(secret, key):
    raw = (secret.data or {}).get(key) if secret is not None else None
    return json.loads(base64.b64decode(raw)) if raw else {"servers": []}


def read(core: Any, repository: str | None = None):
    """
    The servers of ``repository`` (a :func:`omnigent_repo_env.repo_key`), or
    the global ones, and the Secret they're in, or ``None`` before the first save.
    """
    name, key = _place(repository)
    try:
        secret = core.read_namespaced_secret(name, NAMESPACE)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
        secret = None
    return _servers(secret, key), secret


def entry(server, repositories=()):
    """
    One server as the page lists it: everything but the header and environment
    values. ``repositories`` is empty for a global server.
    """
    return {
        "id": server["id"],
        "name": server["name"],
        "repositories": list(repositories),
        "transport": server["transport"],
        "url": server.get("url"),
        "header_names": sorted(server.get("headers", {})),
        "command": server.get("command"),
        "args": server.get("args", []),
        "env_names": sorted(server.get("env", {})),
        "agents": server["agents"],
        "updated_at": server.get("updated_at"),
        "updated_by": server.get("updated_by"),
    }


def _repository_servers(core):
    """Each repository's servers: ``(repository, server)`` by repository."""
    found = []
    for secret in core.list_namespaced_secret(NAMESPACE, label_selector=repo_env.LABEL).items:
        repository = (secret.metadata.annotations or {}).get(repo_env.ANNOTATION)
        # Only the Secret a Pod would mount for that repository.
        if repository and secret.metadata.name == repo_env.secret_name(repository):
            found += [(repository, s) for s in _servers(secret, repo_env.MCP_SERVERS_KEY)["servers"]]
    return sorted(found, key=lambda pair: pair[0])


def summary(core):
    """Every server: the global ones, then the repositories', each server once with all its repositories."""
    data = [entry(s) for s in read(core)[0]["servers"]]
    shared = {}
    for repository, server in _repository_servers(core):
        if server["id"] in shared:
            shared[server["id"]]["repositories"].append(repository)
        else:
            shared[server["id"]] = entry(server, [repository])
    data += shared.values()
    data.sort(key=lambda s: (bool(s["repositories"]), s["repositories"], s["name"].casefold()))
    return {"object": "list", "data": data}


def _places_of(core, server_id):
    """Where the server with ``server_id`` is: ``None`` for every session, or its repositories."""
    places = [None] if any(s["id"] == server_id for s in read(core)[0]["servers"]) else []
    return places + [r for r, s in _repository_servers(core) if s["id"] == server_id]


def _prepare(store, secret, repository=None, updated_by=None):
    """
    The Kubernetes call that stores ``store`` in place of ``secret``, checked
    for size before anything is written.
    """
    name, key = _place(repository)
    text = json.dumps(store)
    data = dict(secret.data or {}) if secret is not None else {}
    if store["servers"]:
        data[key] = base64.b64encode(text.encode()).decode()
    else:
        data.pop(key, None)
    if sum(len(v) for v in data.values()) * 3 // 4 > SECRET_LIMIT:
        raise ValueError("The MCP servers are too large to store. Remove a server or shorten its values.")
    metadata = {"name": name, "namespace": NAMESPACE}
    if repository is not None:
        annotations = dict(secret.metadata.annotations or {}) if secret is not None else {}
        annotations[repo_env.ANNOTATION] = repository
        if updated_by:
            annotations[repo_env.UPDATED_BY_ANNOTATION] = updated_by
        metadata |= {"labels": {repo_env.LABEL: "true"}, "annotations": annotations}
        if secret is not None and not data:
            return lambda core: core.delete_namespaced_secret(name, NAMESPACE)
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": metadata, "data": data}
    if secret is not None:
        metadata["resourceVersion"] = secret.metadata.resource_version
        return lambda core: core.replace_namespaced_secret(name, NAMESPACE, body)
    return lambda core: core.create_namespaced_secret(NAMESPACE, body)


def write(core, store, secret, repository=None, updated_by=None):
    """
    Store the servers of ``repository``, or the global ones, in the Secret read with them.

    A repository's Secret keeps its variables, instructions and script. One
    left with nothing at all is removed, as Settings → Repository variables
    would. A Secret changed since it was read fails with 409.
    """
    _prepare(store, secret, repository, updated_by)(core)


def _places(value):
    """
    The repositories a server is for, as stored and without repeats, or
    ``[None]`` for every session.
    """
    if value is None:
        return [None]
    if not isinstance(value, list) or len(value) > MAX_REPOSITORIES:
        raise ValueError(f"Give up to {MAX_REPOSITORIES} repositories, as owner/repo or clone URLs.")
    places = []
    for repository in value:
        if not isinstance(repository, str):
            raise ValueError("Give each repository as owner/repo or a clone URL.")
        key = repo_env.repo_key(repository)
        if key not in places:
            places.append(key)
    return places or [None]


def _text(value, what, limit=VALUE_LIMIT, *, newlines=True):
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text.")
    if len(value.encode()) > limit:
        raise ValueError(f"{what} is too long.")
    if "\0" in value:
        raise ValueError(f"{what} can't contain NUL characters.")
    if not newlines and ("\n" in value or "\r" in value):
        raise ValueError(f"{what} can't contain line breaks.")
    return value


def _pairs(sent, current, pattern, what, *, newlines):
    """
    Merge the values sent for headers or variables with the stored ones.

    ``sent`` maps each name to its new value, or to ``None`` to keep the
    current one. Names left out are removed.
    """
    if not isinstance(sent, dict):
        raise ValueError(f"Send the {what}s as name: value pairs.")
    if len(sent) > MAX_PAIRS:
        raise ValueError(f"A server can have up to {MAX_PAIRS} {what}s.")
    merged = {}
    for name, value in sent.items():
        if not isinstance(name, str) or not pattern.match(name):
            raise ValueError(f"{name!r} isn't a valid {what} name.")
        if value is None:
            if name not in current:
                raise ValueError(f"The {what} {name} has no value yet.")
            value = current[name]
        merged[name] = _text(value, f"The {what} {name}", newlines=newlines)
    return merged


def _clean_url(url):
    url = _text(url or "", "The URL", 2048, newlines=False).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in url):
        raise ValueError("Give the server's URL, starting with https://.")
    return url


def save(core, body, *, updated_by=None):
    """
    Add a server, or replace the one with ``body["id"]``.

    A saved server can change where it is: it's written to each repository in
    ``body["repositories"]``, or for every session when there are none, and
    removed from where it no longer goes. Its stored header and variable values
    go along, so a repository added later gets them without typing them again.
    Every Secret is checked before any is written, and the removals come last,
    so a failed save never loses the server.

    :param body: ``repositories`` (empty for every session), ``name``,
        ``transport``, ``agents``, and for HTTP ``url`` and
        ``headers``, for stdio ``command``, ``args`` and ``env``. Header and
        variable values that are ``None`` keep the stored ones.
    :returns: The server as :func:`entry` lists it.
    """
    places = _places(body.get("repositories") or None)
    server_id = body.get("id")
    before = _places_of(core, server_id) if server_id else []
    if server_id and not before:
        raise ValueError("That MCP server no longer exists. Reload the page.")
    stores = {place: read(core, place) for place in dict.fromkeys([*places, *before])}
    existing = next(s for s in stores[before[0]][0]["servers"] if s["id"] == server_id) if before else None

    name = _text(body.get("name") or "", "The name", 64, newlines=False).strip()
    if not NAME.match(name):
        raise ValueError("Name the server with letters, digits, - and _, up to 64 characters.")
    if name.casefold() in RESERVED_NAMES:
        raise ValueError(f"{name} is Omnigent's own MCP server. Choose another name.")
    for repository in places:
        if any(s["name"].casefold() == name.casefold() and s["id"] != server_id
               for s in stores[repository][0]["servers"]):
            where = f"{repository} already has" if repository else "There's already"
            raise ValueError(f"{where} an MCP server called {name}.")

    transport = body.get("transport")
    if transport not in TRANSPORTS:
        raise ValueError("Choose HTTP or a command (stdio).")
    agents = body.get("agents")
    if not isinstance(agents, list) or any(a not in AGENTS for a in agents):
        raise ValueError("Choose Claude Code, Codex, or both.")

    # A server that changes transport starts its headers or variables afresh.
    current = existing if existing and existing["transport"] == transport else {}
    server = {"id": existing["id"] if existing else uuid4().hex, "name": name,
              "transport": transport, "agents": [a for a in AGENTS if a in agents]}
    if transport == "http":
        server["url"] = _clean_url(body.get("url"))
        server["headers"] = _pairs(body.get("headers") or {}, current.get("headers", {}),
                                   HEADER_NAME, "header", newlines=False)
    else:
        server["command"] = _text(body.get("command") or "", "The command", 1024, newlines=False).strip()
        if not server["command"]:
            raise ValueError("Give the command that starts the server, such as npx.")
        args = body.get("args") or []
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            raise ValueError(f"Give up to {MAX_ARGS} arguments.")
        server["args"] = [_text(a, "An argument", 4096) for a in args]
        server["env"] = _pairs(body.get("env") or {}, current.get("env", {}),
                               ENV_NAME, "variable", newlines=True)
    server["updated_at"] = datetime.now(timezone.utc).isoformat()
    server["updated_by"] = updated_by

    writes = []
    for repository in places:
        store, secret = stores[repository]
        index = next((i for i, s in enumerate(store["servers"]) if s["id"] == server["id"]), None)
        if index is not None:
            store["servers"][index] = server
        elif len(store["servers"]) >= MAX_SERVERS:
            where = repository or "every session"
            raise ValueError(f"You can add up to {MAX_SERVERS} MCP servers for {where}.")
        else:
            store["servers"].append(server)
        writes.append(_prepare(store, secret, repository, updated_by))
    for repository in before:
        if repository not in places:
            store, secret = stores[repository]
            store["servers"] = [s for s in store["servers"] if s["id"] != server["id"]]
            writes.append(_prepare(store, secret, repository, updated_by))
    for call in writes:
        call(core)
    return entry(server, [p for p in places if p is not None])


def delete(core, server_id, *, updated_by=None):
    """Remove the server with ``server_id`` from every session or all its repositories."""
    places = _places_of(core, server_id)
    if not places:
        raise ValueError("That MCP server no longer exists. Reload the page.")
    writes = []
    for repository in places:
        store, secret = read(core, repository)
        store["servers"] = [s for s in store["servers"] if s["id"] != server_id]
        writes.append(_prepare(store, secret, repository, updated_by))
    for call in writes:
        call(core)
