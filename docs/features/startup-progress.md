# The startup card

See what a session's sandbox is doing while it starts, instead of just
**Connecting host** and **Starting agent**. The card shows each step with how
long it took, says in plain words what went wrong when a start fails, and has
the whole startup log, from Kubernetes, the clone, the host and your setup
scripts.

It appears in the chat while the sandbox starts: when you start a new
session, and when your message wakes a stopped one.

## What you see

The card lists the steps as they happen, each with its time:

| Step | Example | What it means |
| --- | --- | --- |
| **Sandbox** | `Sandbox created · 0.4s` | Omnigent asked Kubernetes for the session's sandbox. |
| **Room on the VM** | `Pod scheduled` | Kubernetes found the CPU, memory and disk for the Pod. While it can't, the step turns amber and says what it's waiting for, such as `Waiting for memory`. |
| **Runner image** | `Runner image ready · Already on the VM` | The VM has the runner image, or downloaded it (`Downloaded in 48s`). |
| **Repositories** | `Repositories cloned` | Each repository, with its own line and time. |
| **Host** | `Host connected` | Omnigent's host in the Pod connected back, with Tailscale and Docker as its own lines. |
| **Setup scripts** | `Setup scripts finished` | Each repository's [setup script](repo-setup-script.md), while it runs and when it finished. |
| **Agent** | `Agent ready` | Claude Code or Codex started and connected. |

A step only appears once Omnigent sees it, so a session without
repositories or setup scripts doesn't show those. The header shows the step
that's running and the time since the start.

**Log** opens the startup log as it comes, every line with its time and
where it came from: **Omnigent**, **Kubernetes** (the Pod's events),
**Clone**, **Host**, **Setup script** and **Agent**. The chips at the top
show one source at a time, and **Copy log** copies what's shown. Repository
variable values in the log show as `[hidden]`.

### When it's done

A start that went well folds into one line, such as `Sandbox ready in 31s`,
which opens again when you click it. It stays below your message, above the
agent's working line, until the agent replies. A message you send later, to
the sandbox that's running since, doesn't bring it back. A start no message
asked for, such as **Start now** in the
[environment menu](environment-menu.md), stays until your next message.

A setup script that failed doesn't stop the session: the agent starts
anyway. The card stays open with the script's last lines, **Show log** and
**Dismiss**, until you dismiss it.

### When it fails

The card says which step failed and why, in plain words, instead of the
Kubernetes error. For example:

| You see | What happened |
| --- | --- |
| **Couldn't clone roadpass**: GitHub refused the Git token for roadpass… | The repository's [own token](repo-git-token.md) expired, or the GitHub App can't reach it. Also: a branch that doesn't exist, a repository Git can't find, a Git server the sandbox can't reach. |
| **No room on the VM**: Waiting for memory… | The VM doesn't have enough free CPU, memory or disk for another sandbox. Stopping idle sessions frees some. |
| **Couldn't get the runner image**: The registry refused to send the runner image | The image is missing from the registry, or the VM can't pull it. |
| **The sandbox ran out of memory while starting** | The host used more memory than the sandbox has. |
| **Kubernetes didn't create the sandbox's Pod** | Usually a quota. The card has the Kubernetes events that say so. |
| **The host started but never connected** | The Pod runs, but its host didn't reach Omnigent. The card has the host's last log lines. |
| **The agent couldn't start** | Its process exited before connecting. |

Under it are the failing step's last log lines, and:

- **Try again** starts the sandbox again without a message. When it can't,
  the card says that your next message starts it again.
- **Repository variables**, for a clone that failed, opens
  **Settings → Repository variables**, where its token is.
- **Show log** opens the whole startup log.
- **Copy error details** copies the explanation, the original error and the
  log lines, for an issue or a message to an admin.

While the card shows the failure, the chat doesn't repeat it as a generic
error. The [environment menu](environment-menu.md) shows the sandbox as
failed too.

### Waking a stopped session

A session that [stopped while idle](idle-sessions.md), or that you stopped,
starts its sandbox again with the same card. Steps that don't happen again
say so: **Repositories kept from last time**, and **Setup scripts already
ran**.

### Terminal sessions

A session that opens in the agent's terminal shows the card in place of
**Starting up…**. While the sandbox starts, its terminal has no **Resume
session** button, so you can't start it twice.

## Good to know

- **The record lives in the server's memory.** After the server restarts, a
  session's old start has no card, and the next start has a new one. The
  server keeps the last 500 sessions' starts.
- **The log keeps the last 3000 lines** of a start.
- **A clone that fails twice ends the start.** Kubernetes tries the clone
  again once, for a network hiccup. Before, it kept trying until the start
  timed out after minutes.
- **An agent that exits while starting ends the start straight away**,
  instead of after Omnigent's connect timeout.
- **A message you send while the sandbox starts waits for it**, for as long
  as Kubernetes is given to start the Pod, so a slow image download doesn't
  time out your message before the sandbox.
- **It's for sandboxes Omnigent starts.** A session on your own machine has
  no sandbox to start, and keeps the old **Connecting host** and **Starting
  agent** lines.

## Turning it off

There's nothing to turn off.

## How it works

- **The record:** `images/server/omnigent_startup.py` keeps one record per
  session: the steps, the problem, and the log. Upstream's sandbox status
  (`provisioning`, `connecting`, `ready`, `failed`) starts and ends it, and
  `explain()` turns a failed start's error into the card's title and text.
  Every change is sent to the browser as a `session.startup` event, and the
  session itself has the record as `startup`.
- **The Pod:** while it waits for the Pod, the Kubernetes launcher
  (`images/server/patches/0016-startup-progress.patch`) passes each poll of
  the Pod, its events and its containers' logs to the record. The clone
  script prints `omnigent-prep: cloning|cloned|failed|kept <checkout>` for
  each repository, which become the clone's lines. The same patch stops the
  start when the clone container failed twice, keeps reading the host
  container's log until the host connects, and has the server end the start
  when the agent's runner exits.
- **Inside the Pod:** the host container gets
  `OMNIGENT_STARTUP_REPORT_URL`, `/v1/hosts/<host id>/startup`. There,
  `omnigent-startup-report` (`images/runner/startup/startup-report.py`) posts
  a step's state with the host's launch token, and follows a setup script's
  log into the startup log, with the repository's variable values hidden.
  The [setup script](repo-setup-script.md#how-it-works), Tailscale
  (`images/runner/tailscale/tailscale-service.sh`) and Podman
  (`images/runner/containers/podman-service.sh`) services call it. It never
  fails the script that calls it, and without the URL it does nothing.
- **The routes:** `images/server/omnigent_startup_api.py` adds
  `GET /v1/sessions/<id>/startup-log?after=<line>`, which anyone who can read
  the session can page through, and the hosts' `POST`.
- **The card:** `images/server/web-patches/0021-startup-progress.patch` adds
  `SandboxStartupCard` to the chat and the terminal view, keeps the record in
  the chat store, and hides the generic failure and **Starting agent** lines
  while the card is there. A dismissed warning is remembered in the browser
  tab.
