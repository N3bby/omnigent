# Custom features

This deployment runs upstream Omnigent `v0.15.0` (pinned as `omnigent_commit`
in `versions.yaml`) with a few extras on top. This page explains what each one
does, what you'll notice, and how to turn it off where that's possible.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Usage limits in the UI](#see-your-usage-limits) | Your Claude and Codex usage, right in the composer | No |
| [Model picker before the first session](#pick-a-model-straight-away) | Choose a model without starting a session first | No |
| [Root and containers in runners](#install-packages-and-run-containers) | Agents can `apt-get install` and use Docker commands | No |
| [No permission prompts](#agents-dont-stop-to-ask) | Agents work without waiting for approval | Yes, one setting per agent |
| [GitHub repository picker](#pick-repositories-from-github) | Choose repositories from a list | Optional setup step |

## See your usage limits

Claude and Codex subscriptions have a 5-hour limit and a weekly limit. The
composer shows how much of each you've used, next to the context ring, so you
don't hit a limit halfway through a task. Click or tap the numbers to see bars
and reset times. It works in the browser and in the desktop and mobile apps.

![Illustration of the usage limits in the composer](images/usage-limits.svg)

The numbers appear once the agent first reports them. For Claude that's after
its first reply; for Codex it's when the first turn starts. They belong to the
login, so every session using the same account shows the same numbers.

<details>
<summary>How it works</summary>

Three small patches work together:

- **Runner** (`images/runner/patches/0001-rate-limits.patch`): passes on the
  usage numbers that the Claude and Codex CLIs report.
- **Server** (`images/server/patches/0002-rate-limits.patch`): keeps the latest
  numbers per session and sends them to the UI.
- **Web UI** (`images/server/web-patches/0001-composer-rate-limits.patch`):
  adds the `ComposerRateLimits` display.

The upstream server image comes with a prebuilt web UI, so the server image
build rebuilds the UI from source with the web patch applied.

</details>

## Pick a model straight away

Upstream Omnigent can only list models once a runner has started. With
subscription logins, the model picker would say "Models unavailable" until
then. This deployment remembers the list that runners last reported and shows
it straight away. The runner still checks the chosen model when it starts.

![How the model list is remembered](images/model-picker.svg)

On a brand-new install the list is empty until the first session has run once.

<details>
<summary>How it works</summary>

The server patch (`images/server/patches/0001-sandbox-model-catalog-fallback.patch`)
saves the list to the artifacts volume, at the path set by
`OMNIGENT_SANDBOX_CATALOG_FALLBACK_PATH` in `scripts/render`.

</details>

## Install packages and run containers

Agents in a runner are root, so they can install whatever a task needs with
`apt-get install`. They can also build and run containers with Podman, and
`docker` commands work too.

That root only applies inside the pod. A Linux user namespace maps it to an
ordinary, unprivileged user on the host.

![Root inside the pod maps to an unprivileged user on the host](images/runner-root.svg)

A few things don't work:

- `docker buildx`, and tools that talk to the Docker daemon API
- publishing container ports
- per-container resource limits (the pod's limits still apply)

Container images count towards the runner's home volume, which is capped by
`runner_home_limit`.

<details>
<summary>How it works</summary>

- `kubernetes/base/runner-userns.yaml` holds two admission policies. One makes
  every runner pod use its own user namespace (`hostUsers: false`) and run as
  UID 0. The other rejects pods in `omnigent-sandboxes` that are privileged,
  mount `hostPath`, share host namespaces, or use host ports.
- The runner image installs Podman with `docker` as an alias, configured by
  `images/runner/containers/`. Containers
  share the pod's network and run without their own cgroups, because
  containerd doesn't hand cgroups to user-namespaced pods.
- After upgrading k3s, run `scripts/check-userns` on the VM to check that
  all of this still works.

</details>

## Agents don't stop to ask

By default, Claude Code and Codex run without asking for approval before each
command or edit. The runner sandbox is what keeps them contained. See the
[threat model](THREAT_MODEL.md) for what that does and doesn't protect.

To make an agent ask again, turn off its setting in
`environments/production.toml` and deploy:

| Setting | Agent |
| --- | --- |
| `claude_bypass_permissions` | Claude Code |
| `codex_bypass_approvals` | Codex |

New runners use the new setting; runners that already exist keep the old one.

<details>
<summary>How it works</summary>

- **Claude Code** (`images/runner/claude-wrapper.sh`): writes
  `/etc/claude-code/managed-settings.json` with
  `defaultMode: bypassPermissions`, which a session's own settings can't
  override. It also sets `skipDangerousModePermissionPrompt`, because Claude
  Code otherwise shows a one-time consent dialog that Omnigent can't answer.
- **Codex** (`images/runner/codex-wrapper.sh`): adds
  `--dangerously-bypass-approvals-and-sandbox`. It also links the shared Codex
  login into each session.

</details>

## Pick repositories from GitHub

Connect a GitHub App and you can pick repositories from your GitHub account
when you start a session. Runners can clone and push to whichever
repositories the App is installed on, private ones included, so you don't
also need `mise run setup-git-token`.

1. Run `mise run setup-github-app`. It prints the settings to use for a new
   GitHub App, then asks for its Client ID, Client secret and slug.
2. In Omnigent, go to Settings -> Sandbox Integrations and connect GitHub.

Each user's GitHub tokens are encrypted by a small Vault service inside the
cluster before they're stored in the database.

<details>
<summary>How it works</summary>

Vault runs from `kubernetes/base/vault.yaml` and uses its Transit engine for
the encryption. The key that unlocks Vault is stored in a Kubernetes Secret,
so Vault comes back by itself after a restart. The server image adds the
`hvac` Python client so the server can talk to Vault.

</details>

## Keeping the patches up to date

The patches are applied when the images are built. If a patch no longer
applies to the pinned `omnigent_commit`, the image build and `mise run test`
fail. Once upstream Omnigent ships the same behaviour, delete the patch. Once
no web patches are left, the server image can stop rebuilding the web UI.
