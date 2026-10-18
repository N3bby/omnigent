"""Give Claude Code and Codex the MCP servers from Settings → MCP servers.

The server mounts the global ones at ``/run/omnigent/mcp-servers/servers.json``,
and each of the session's repositories' at
``/run/omnigent/repo-env/<Secret>/mcp-servers.json``
(``images/server/omnigent_mcp_servers.py``). Kubernetes updates the files
when an admin saves. A repository's server replaces a global one with the same
name, and with several repositories, the first one the session started with
wins. The agent wrappers start their agent through this script whenever one
of the files exists, so each agent start reads the current servers:

    mcp-servers.py exec claude /usr/local/bin/claude-real -- ARGS...
    mcp-servers.py exec codex /usr/local/bin/node /usr/local/bin/codex-real -- ARGS...

Claude Code gets ``--mcp-config=<file>`` in front of its arguments, which adds
to Omnigent's own ``--mcp-config``. Codex gets ``-c mcp_servers.<name>.*``
overrides, in a session's home only, not in Omnigent's model probes. Neither
the file nor the arguments hold a header or variable value: a command (stdio)
server starts through ``run``, which adds its variables, Claude Code gets
headers from ``headers``, and Codex reads them from variables set only for its
own process. If anything goes wrong the agent starts without these servers.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

SERVERS = Path(os.environ.get("OMNIGENT_MCP_SERVERS_FILE", "/run/omnigent/mcp-servers/servers.json"))
REPO_ENV = Path(os.environ.get("OMNIGENT_REPO_ENV_DIR", "/run/omnigent/repo-env"))
REPO_SERVERS = "mcp-servers.json"
# "<Secret>\t<repository>\t<checkout>" per repository, in the session's order.
CHECKOUTS = Path(os.environ.get("OMNIGENT_REPO_SETUP_RUN", "/run/omnigent-repo-setup")) / "checkouts"
CONFIG_DIR = Path(os.environ.get("OMNIGENT_MCP_CONFIG_DIR", "/run/omnigent-mcp-servers"))
PYTHON = sys.executable
SCRIPT = str(Path(__file__).resolve())
# Codex's default is 10 seconds, too short for npx to fetch a server.
STDIO_STARTUP_TIMEOUT_SEC = 60
# Codex homes that belong to a session: Omnigent's per-session native homes,
# its executor's temporary ones, and the image's own for a shell's `codex`.
# Model probes use homes of their own, which start nothing.
CODEX_SESSION_HOMES = (
    "/opt/codex-home",
    "/home/omnigent/.omnigent/codex-native/*/codex-home",
    "*/omnigent-codex-home-*",
)


def load(path: Path = SERVERS) -> list[dict]:
    if not path.is_file():
        return []
    return json.loads(path.read_text()).get("servers", [])


def repository_files() -> list[Path]:
    """The session's repositories' server files, first-picked first."""
    order = []
    if CHECKOUTS.is_file():
        order = [line.split("\t", 1)[0] for line in CHECKOUTS.read_text().splitlines() if line]
    files = sorted(REPO_ENV.glob(f"*/{REPO_SERVERS}"),
                   key=lambda f: (order.index(f.parent.name) if f.parent.name in order else len(order), f.parent.name))
    return files


def merged(agent: str) -> list[tuple[Path, dict]]:
    """Each server ``agent`` gets, with the file it's in; a repository's replaces a global one."""
    servers: dict[str, tuple[Path, dict]] = {}
    for path in repository_files():
        for server in load(path):
            servers.setdefault(server["name"], (path, server))
    for server in load(SERVERS):
        servers.setdefault(server["name"], (SERVERS, server))
    # A repository's server switched off for this agent still hides the global one.
    return [(path, server) for path, server in servers.values() if agent in server.get("agents", [])]


def find(path: Path, name: str) -> dict:
    for server in load(path):
        if server["name"] == name:
            return server
    raise SystemExit(f"mcp-servers: no MCP server called {name} in {path}; it may have been removed in Settings")


def launcher(path: Path, server: dict) -> tuple[str, list[str]]:
    """The command and arguments that start a stdio server through ``run``."""
    return PYTHON, [SCRIPT, "run", str(path), server["name"]]


def claude_config(servers: list[tuple[Path, dict]]) -> dict:
    config = {}
    for path, server in servers:
        if server["transport"] == "http":
            entry = {"type": "http", "url": server["url"]}
            if server.get("headers"):
                entry["headersHelper"] = shlex.join([PYTHON, SCRIPT, "headers", str(path), server["name"]])
        else:
            command, args = launcher(path, server)
            entry = {"type": "stdio", "command": command, "args": args}
        config[server["name"]] = entry
    return {"mcpServers": config}


def write_claude_config(servers: list[tuple[Path, dict]]) -> Path:
    """Write the ``--mcp-config`` file, replacing it whole so concurrent starts never read half of it."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    target = CONFIG_DIR / "claude.json"
    fd, temporary = tempfile.mkstemp(prefix=".claude.json.", dir=CONFIG_DIR)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(claude_config(servers), file)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def toml(value) -> str:
    """A TOML value for ``-c``: JSON's strings, arrays and numbers are valid TOML."""
    return json.dumps(value)


def codex_overrides(servers: list[tuple[Path, dict]], env: dict[str, str]) -> list[str]:
    """``-c`` arguments for Codex, setting each header's value in ``env`` under a name of its own."""
    arguments = []
    secret = 0
    for path, server in servers:
        key = f"mcp_servers.{server['name']}"
        if server["transport"] == "http":
            arguments += ["-c", f"{key}.url={toml(server['url'])}"]
            headers = {}
            for header, value in server.get("headers", {}).items():
                secret += 1
                variable = f"OMNIGENT_MCP_SECRET_{secret}"
                env[variable] = value
                headers[header] = variable
            if headers:
                table = ", ".join(f"{toml(h)} = {toml(v)}" for h, v in headers.items())
                arguments += ["-c", f"{key}.env_http_headers={{ {table} }}"]
        else:
            command, args = launcher(path, server)
            arguments += [
                "-c", f"{key}.command={toml(command)}",
                "-c", f"{key}.args={toml(args)}",
                "-c", f"{key}.startup_timeout_sec={STDIO_STARTUP_TIMEOUT_SEC}",
            ]
    return arguments


def codex_session(env: dict[str, str]) -> bool:
    if env.get("HARNESS_CODEX_MINIMAL_CONFIG", "").strip().lower() in {"1", "true", "yes"}:
        return False
    home = env.get("CODEX_HOME", "")
    return not home or any(fnmatch(home.rstrip("/"), pattern) for pattern in CODEX_SESSION_HOMES)


def command_line(agent: str, prefix: list[str], args: list[str], env: dict[str, str]) -> list[str]:
    """The agent's command line with this deployment's MCP servers, or as it was."""
    servers = merged(agent)
    if not servers:
        return [*prefix, *args]
    if agent == "claude":
        # Omnigent's model probe asks for no MCP servers at all.
        if "--strict-mcp-config" in args:
            return [*prefix, *args]
        return [*prefix, f"--mcp-config={write_claude_config(servers)}", *args]
    if not codex_session(env):
        return [*prefix, *args]
    return [*prefix, *codex_overrides(servers, env), *args]


def exec_agent(argv: list[str]) -> None:
    agent, rest = argv[0], argv[1:]
    split = rest.index("--")
    prefix, args = rest[:split], rest[split + 1:]
    env = dict(os.environ)
    try:
        command = command_line(agent, prefix, args, env)
    except Exception as exc:  # noqa: BLE001 - the agent must start whatever the file holds
        print(f"mcp-servers: starting {agent} without the MCP servers from Settings: {exc}", file=sys.stderr)
        command, env = [*prefix, *args], dict(os.environ)
    os.execve(command[0], command, env)


def run(path: str, name: str) -> None:
    """Start a stdio server with its variables, which only this process gets."""
    server = find(Path(path), name)
    env = {**os.environ, **server.get("env", {})}
    command = [server["command"], *server.get("args", [])]
    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        raise SystemExit(f"mcp-servers: couldn't start {name} with {command[0]}: {exc}") from None


def headers(path: str, name: str) -> None:
    """Print a server's headers as JSON, for Claude Code's ``headersHelper``."""
    print(json.dumps(find(Path(path), name).get("headers", {})))


def main(argv: list[str]) -> None:
    if len(argv) >= 3 and argv[0] == "exec" and argv[1] in ("claude", "codex") and "--" in argv:
        exec_agent(argv[1:])
    elif len(argv) == 3 and argv[0] == "run":
        run(argv[1], argv[2])
    elif len(argv) == 3 and argv[0] == "headers":
        headers(argv[1], argv[2])
    else:
        raise SystemExit("usage: mcp-servers.py exec claude|codex COMMAND... -- ARGS... | run FILE NAME | headers FILE NAME")


if __name__ == "__main__":
    main(sys.argv[1:])
