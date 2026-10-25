# omnigent-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an agent outside
[Omnigent](https://github.com/omnigent-ai/omnigent), such as
[Hermes Agent](https://hermes-agent.nousresearch.com/), your Omnigent
sessions and projects: it can look into them, archived ones included, start
new sessions with or without a project, and talk to them.

It runs on the agent's machine over stdio and uses Omnigent's REST API as
you. It needs nothing else from Omnigent installed.

## Tools

| Tool | What it does |
| --- | --- |
| `list_projects` | Your projects, with the repositories, agent, model and host their new sessions start with |
| `list_agents` | The agents a session can run, such as `claude-native-ui`, `codex-native-ui` and `hermes` |
| `list_sessions` | Your sessions, newest first, filtered by project, archived state or a search |
| `get_session` | One session's status, agent, model, project, workspace, branch, last error and todo list |
| `read_session` | A session's conversation, latest first, with its tool calls if you want them |
| `create_session` | A new session in a sandbox, in a project or not, with an optional first message |
| `send_message` | A message to an existing session, as you'd type it in the chat |
| `wait_for_reply` | Waits for the agent to finish its turn and returns what it said |

`create_session` in a project uses the project's settings, as the New
session screen does: its repositories, cloned into the sandbox, its agent
and its model. Anything you pass replaces them. A project that starts its
sessions on one of your own machines starts them there, in its working
directory. Without a project, a session runs Claude Code in a sandbox with
the repositories you pass, or none.

Sending a message to a session whose sandbox is starting waits for the
sandbox. The tool answers after 15 seconds with `queued` while the server
keeps the message until the agent is up, and `wait_for_reply` picks up from
there.

## Install

```bash
uv tool install "git+https://github.com/N3bby/omnigent#subdirectory=packages/omnigent-mcp"
```

or `pip install` the same, or this directory from a checkout. It needs
Python 3.10 or newer.

## Sign in

```bash
omnigent-mcp login --server https://omni.example.com --username you
```

It asks for your password and keeps only a refresh token, in
`~/.config/omnigent-mcp/credentials.json`, readable by you alone. The token
works for 30 days, or what the server's `OMNIGENT_GRANT_MAX_LIFETIME_DAYS`
allows; sign in again after that. `omnigent-mcp status` checks it, and
`omnigent-mcp logout` revokes it.

Instead of the file, the server can take `OMNIGENT_URL` and
`OMNIGENT_REFRESH_TOKEN` from its environment. `OMNIGENT_MCP_CREDENTIALS`
moves the file.

`login` signs in with an Omnigent account's password, so it needs a server
with Omnigent's own accounts (`OMNIGENT_AUTH_PROVIDER=accounts`). The MCP
has everything your account has, so anyone who can read the token can use
Omnigent as you.

## Use it in Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  omnigent:
    command: omnigent-mcp
    timeout: 960
```

`timeout` lets `wait_for_reply` wait up to its maximum of 15 minutes. Any
other MCP client runs `omnigent-mcp` the same way.
