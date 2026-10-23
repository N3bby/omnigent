# omnigent-hermes-remote

An [Omnigent](https://github.com/omnigent-ai/omnigent) community harness,
`hermes-remote`, that hands each turn to an existing
[Hermes Agent](https://hermes-agent.nousresearch.com/) through its API server,
over SSH.

Each Omnigent conversation is one Hermes session (`omnigent-<conversation id>`),
so it shares that Hermes's history, memory, skills and model login with its
desktop app, terminal and chat platforms. Nothing about Omnigent runs on the
Hermes machine.

## How it works

On each turn the harness:

1. Reads the API server's port and key from the env file Hermes's gateway uses
   on the Hermes machine, over SSH (by default `~/.config/hermes/api-server.env`,
   with `API_SERVER_KEY` and optionally `API_SERVER_PORT` and `API_SERVER_HOST`).
2. Forwards a local Unix socket to the API server, which stays bound to
   `127.0.0.1` there. The tunnel is reused across turns and reopened if it drops.
3. Starts a run (`POST /v1/runs`) with only the new message: Hermes loads the
   rest of the conversation from its own history.
4. Streams the run's text and tool calls into the chat. Stop in Omnigent stops
   the run.

Hermes's approval prompts can't be answered from Omnigent yet, so the harness
denies them straight away and says so in the chat, rather than leaving the run
waiting until Hermes gives up.

## Settings

The harness reads these from its environment. Omnigent's runner passes only
allowlisted variables to harnesses, so list them in
`OMNIGENT_RUNNER_ENV_PASSTHROUGH` on the host.

| Variable | Required | What it is |
| --- | --- | --- |
| `HERMES_REMOTE_SSH_HOST` | Yes | The Hermes machine, such as `home-hermes` |
| `HERMES_REMOTE_SSH_USER` | No | The user Hermes runs as, such as `n3bby` |
| `HERMES_REMOTE_SSH_PORT` | No | SSH's port, such as `2222` |
| `HERMES_REMOTE_SSH_KEY` | No | A private key's text; without it, SSH uses its own keys and config |
| `HERMES_REMOTE_ENV_FILE` | No | The gateway's env file on the Hermes machine |

## Limiting the key

On the Hermes machine, the key can be allowed to do only what the harness
needs, in `~/.ssh/authorized_keys`:

```text
restrict,port-forwarding,permitopen="127.0.0.1:8642",command="cat ~/.config/hermes/api-server.env" ssh-ed25519 AAAA... omnigent-hermes-remote
```

`command=` makes every login print the env file, whatever it asked to run;
`permitopen=` allows forwarding only to the API server. The API key still lets
its holder use Hermes fully, which can run commands as that user.

## Installing

Omnigent's server checks harness names against its own installed plugins, so
install the package where the server runs, and on every host that should run
the harness:

```bash
pip install ./packages/omnigent-hermes-remote
```
