# Omnigent as tools for your Hermes

Give your own [Hermes Agent](https://hermes-agent.nousresearch.com/) your
Omnigent sessions and projects as MCP tools. From its desktop app, terminal
or chat platforms, Hermes can then:

- **Look into your sessions:** list them by project, search them, see what
  each is doing, and read what was said, archived sessions included.
- **Start sessions:** in a project, with the project's repositories, agent
  and model, or on their own, with the repositories you name or none.
- **Talk to them:** send a session a message, and wait for its agent's
  answer.

It's the other way round from [your own Hermes as an agent](hermes-remote.md),
which runs Hermes inside an Omnigent session. You can use both.

## Set it up

All of it happens on the Hermes machine, as the user Hermes runs as. It needs
to reach the deployment's hostname, which it does over your tailnet.

### 1. Install it

```bash
uv tool install "git+https://github.com/N3bby/omnigent#subdirectory=packages/omnigent-mcp"
```

This puts `omnigent-mcp` on your `PATH`. Without `uv`, `pipx install` the
same address. To update it later, run `uv tool upgrade omnigent-mcp`.

### 2. Sign in

```bash
omnigent-mcp login --server https://omni.n3b.by --username <your Omnigent username>
```

It asks for your Omnigent password, and keeps a token in
`~/.config/omnigent-mcp/credentials.json` that only you can read. Your
password isn't kept. `omnigent-mcp status` checks that the sign-in works.

### 3. Add it to Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  omnigent:
    command: omnigent-mcp
    timeout: 960
```

Give `command` the full path, such as `~/.local/bin/omnigent-mcp`, if the
gateway service doesn't have it on its `PATH`. Then restart Hermes, and the
gateway with `systemctl --user restart hermes-gateway`. Hermes lists the
tools as `mcp_omnigent_list_sessions` and so on.

## What you'll see

Ask Hermes something like "what are my Omnigent sessions in roadpass doing?",
"find the session where we fixed the login page and summarize it", or "start
a Claude Code session in roadpass to upgrade the dependencies and tell me
what it did".

| Tool | What Hermes gets |
| --- | --- |
| `list_projects` | Your projects, with the repositories, agent, model and host their new sessions start with |
| `list_agents` | The agents a session can run: `claude-native-ui` (Claude Code), `codex-native-ui` (Codex), `hermes` and the others |
| `list_sessions` | Your sessions, newest first, by project or in none, archived ones left out, included or alone, or matching a search |
| `get_session` | A session's status, agent, model, project, workspace and branch, last error, todo list, and any question it's waiting on |
| `read_session` | What was said in a session, latest first, with the agent's tool calls if it asks for them |
| `create_session` | A new session, with or without a first message |
| `send_message` | A message to an existing session, as you'd type it in the chat |
| `wait_for_reply` | The agent's answer, once it's done |

- **A session Hermes starts is yours,** like one you started: it shows in
  the sidebar, in its project, and Omnigent titles it from the conversation.
  Each answer Hermes gets has the session's link.
- **In a project,** a new session clones the project's repositories, with
  their branches, and gets the project's agent and model, as on the New
  session screen with [a project's repositories](project-repositories.md).
  Hermes can name others instead. A project that starts its sessions on one
  of your own machines starts them there.
- **Without a project,** a session runs Claude Code in a sandbox, with the
  repositories Hermes names or none.
- **A new sandbox takes a minute or two to start.** Hermes's first message
  is queued until the agent is ready, and `wait_for_reply` waits until the
  agent has answered.
- **When the agent asks a question** in the web UI, such as to approve a
  command, `wait_for_reply` stops with what it asked. Answer it in Omnigent.

## Good to know

- **The sign-in lasts a year,** counted from `omnigent-mcp login`. After
  that every tool says to sign in again: run `omnigent-mcp login` once more.
  `grant_max_lifetime_days` in `environments/<name>.toml` sets how long, for
  `omnigent login` on your computers too. Omnigent's default is 30 days.
  Changing it applies to sign-ins already made, after a deploy.
- **Hermes can do what you can.** The token is as good as your account for
  sessions and projects, so keep the Hermes machine as safe as your own. A
  copied token works until `omnigent-mcp logout`, or the year runs out.
- **Sessions cost a sandbox.** A session Hermes starts runs a Pod like any
  other, until it's [idle](idle-sessions.md), and counts toward
  `runner_max_concurrency`. Hermes is told to look for an existing session
  when it continues earlier work.
- **Reading never wakes a sandbox.** Listing and reading sessions, archived
  or stopped, only reads Omnigent's database. Sending a message to a stopped
  session starts its sandbox again, as in the web UI.
- **It reads what was said, not files.** It can't open files in a session's
  workspace. Hermes can ask the session's agent instead.
- **It can't delete or archive sessions,** or change projects.

## Turn it off

On the Hermes machine, remove `omnigent` from `mcp_servers` in
`~/.hermes/config.yaml` and restart Hermes. Then `omnigent-mcp logout`,
which revokes the token, and `uv tool uninstall omnigent-mcp`.

## How it works

[`packages/omnigent-mcp`](../../packages/omnigent-mcp/README.md) is a
standalone Python package, a stdio MCP server that Hermes starts. It needs
nothing from Omnigent but its REST API, so it changes nothing in the images
and needs no deploy.

- **Signing in:** `login` posts your password to `/auth/login` with
  `issue_refresh`, as `omnigent login` does, and keeps only the refresh token
  the server issues. Omnigent doesn't rotate the refresh tokens of these
  CLI logins, so one token works for the whole of the grant's lifetime,
  which `grant_max_lifetime_days` sets as the server's
  `OMNIGENT_GRANT_MAX_LIFETIME_DAYS`. The MCP trades it
  at `/oauth/token` for access tokens, which last an hour, as it needs them.
- **Reading:** `GET /v1/projects` and `/v1/sessions/projects` for projects,
  with their settings; `GET /v1/sessions`, with `visibility=archived` or
  `include_archived` for archived ones; and `GET /v1/sessions/{id}/items` for
  what was said.
- **Starting a session:** `POST /v1/sessions` with `host_type: managed` and
  the repositories as `workspaces`, or the project's connected host. In a
  project, it sends `project_id` and lets the server fill in the project's
  agent. The repositories come from the project's `sandbox_repos`, which the
  web UI's project settings keep.
- **Sending a message:** `POST /v1/sessions/{id}/events`. While a sandbox
  starts, the server holds that request until the agent is ready, for up to
  four minutes. The MCP keeps it open in the background and answers Hermes
  after 15 seconds with `queued`.
- **Waiting for the answer:** `wait_for_reply` polls the session and its
  items every 3 seconds. It's done when the agent has said something after
  the message and the session is no longer running, or when the session
  waits on a question.

The tests are `tests/test_omnigent_mcp.py`. They drive the tools against a
fake Omnigent, without the MCP SDK.
