# A host for Hermes sessions

A session with the [hermes agent](hermes-remote.md) only passes your messages
on to your own Hermes, which works on its own machine, so a sandbox gives it
nothing but a start of about 8 seconds. Instead, pick the **Hermes** host: a
Pod named `omnigent-hermes-host` that stays up, so a session there starts
straight away.

![A Hermes session's composer: its host menu shows Hermes, online](../images/hermes-host-composer.webp)

## What you'll notice

- **The host pickers list Hermes** under **My machines**, next to the
  sandbox and your own machines. Pick it on the **New session** screen, or
  make it the **Host** of a project in its **Project settings**, so every new
  session there starts on it.
- **A Hermes session there answers sooner.** No sandbox starts: the
  session's runner starts as soon as the session is created, and only the
  first turn's SSH connection to Hermes is left.
- **The session's host is Hermes.** The composer shows the host chip
  instead of the environment menu, and its menu says **Hermes**. There's no
  sandbox to stop or wake up.
- **It runs only Hermes sessions.** Starting another agent there is refused
  with "The Hermes host only runs Hermes sessions", and an automation that
  picks a host itself never picks it. The host is signed in as you, so an
  agent with a shell there could act as you.
- **Hermes sessions elsewhere still work.** With the sandbox picked, a Hermes
  session gets a sandbox, as before.
- **When the host is down**, such as while it restarts after a deploy,
  sessions on it can't answer until it's back.

Only the person who signed it in sees the host: an Omnigent host belongs to
one user.

## Set it up

It's on wherever [Hermes is set up](hermes-remote.md#set-it-up)
(`hermes_remote_ssh_host` in `environments/<name>.toml`), from the next
deploy. Then sign the host in as you, once:

```bash
mise run setup-hermes-host
```

It asks for your Omnigent username and password, signs in, and keeps only a
token that lets the host stay signed in, in the `omnigent-hermes-host`
Secret, which only the host's Pod gets. Your password isn't kept. The token
lasts `grant_max_lifetime_days` from signing in; run the task again before
then, or after changing your password. Running it again signs the old token
out, and restarts the host.

`mise run credential-status` shows whether the host is signed in. Until it
is, the Pod waits, and the pickers don't list the host.

## Turn it off

Set `hermes_host = false` in `environments/<name>.toml` and deploy, which
removes the Pod. Then remove its sign-in:

```bash
./scripts/kube delete secret omnigent-hermes-host -n omnigent-sandboxes
```

Sessions that ran on the host can't answer any more; start new ones in the
sandbox.

## How it works

The Pod runs the runner image, so it's set up like a runner Pod: root in its
own user namespace, and on your tailnet, through which it reaches the Hermes
machine. It counts toward the runners' quota, within the room that keeps
spare beyond `runner_max_concurrency` runners. Its start script
(`images/runner/hermes-host.sh`) writes the sign-in where `omnigent login`
would, then runs `omnigent host`. The host renews its access with the token
when it connects, and whenever the access runs out. Each session gets its own
runner process there; a runner idle for an hour exits, and the next message
starts it again.

The render gives the host a fixed id, the same in the Pod and in the server's
`OMNIGENT_HERMES_HOST_ID`, so a restarted Pod is the same host and its
sessions find it again. With it, the server patch
`images/server/patches/0024-hermes-host.patch` and
`images/server/omnigent_hermes_host.py` refuse a new session on that host
whose agent doesn't use the `hermes-remote` harness, and leave the host out
when an automation picks one of your hosts.

Whoever can sign in to the Pod, such as over Tailscale SSH, can use the
token as you. See the [threat model](../threat-model.md).
