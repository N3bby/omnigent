# Idle sessions stop and keep their files

Idle sessions free the CPU and memory they reserved, but keep your files.

## What happens when a session is idle

| After this long without agent activity | What happens |
| --- | --- |
| 30 minutes | The agent exits. The Pod keeps running, so everything in it is still there. |
| 4 hours | The Pod stops, which frees its CPU and memory. |

Sending a message wakes a stopped session. Waking takes about as long as
starting a new session.

## What's kept, and what isn't

**Kept:** the home directory, `/home/omnigent`. Your repositories,
uncommitted changes, agent state and container images are where you left
them. The home directory is only deleted when you delete the session.

**Not kept:** everything outside the home directory starts fresh when a
stopped Pod wakes, including packages from `apt-get install`. To keep a tool,
have the agent install it into the home directory, for example with
[mise](mise-runtimes.md), `pip install --user` or `npm --prefix`.

## Good to know

- **Stopped sessions still use disk.** The home directory's size limit,
  `runner_home_limit`, isn't enforced by `local-path`, so delete sessions
  you're done with.
- **`mise run status` lists every session's volume.** Stopped sessions show
  as `Ready=False`, `SandboxExpired`.
- **An idle Pod still counts** towards `runner_max_concurrency` and the VM's
  capacity until it stops. With many sessions, a new one may have to wait for
  an old one to stop or be deleted.

## Changing the timing

Set these in `environments/production.toml` and deploy:

| Setting | Idle time before… | Default |
| --- | --- | --- |
| `runner_agent_idle_seconds` | the agent exits | 1800 (30 minutes) |
| `runner_idle_shutdown_seconds` | the Pod stops | 14400 (4 hours) |

- The Pod has to stop at least 300 seconds after the agent exits.
- Pods that are already running keep their old timing until they stop.

## How it works

- **Runner Pods are `Sandbox` objects** of the
  [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
  controller, which `mise run bootstrap` installs. Upstream Omnigent's
  `agent_sandbox` provider manages them.
- **The deadline moves while the session is active.** While a runner is
  connected, the server keeps pushing the Sandbox's `shutdownTime` forward.
  - The runner exits once it has been idle for `runner_agent_idle_seconds`
    (Omnigent's `keep_warm_s`).
  - When the session has been idle for `runner_idle_shutdown_seconds`, the
    deadline passes and the controller deletes the Pod. The Sandbox and its
    volume stay.
- **Each session gets its own volume.**
  `OMNIGENT_AGENT_SANDBOX_WORKSPACE_SIZE` in `scripts/render` puts the home
  directory on a per-session `local-path` volume instead of an `emptyDir`.
- **Deleting the session deletes the Sandbox**, and Kubernetes deletes its
  volume with it.
