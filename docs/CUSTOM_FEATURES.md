# Custom features

This deployment runs Omnigent `v0.15.0` (`omnigent_commit` in
`versions.yaml`) with a few additions on top of upstream. Each one is either an
upstream patch applied at image build time or deployment-side configuration.
The image build and `mise run test` fail if a patch no longer applies. Delete a
patch once upstream ships the same behaviour.

| Feature | Where it lives | Toggle |
| --- | --- | --- |
| Usage limits in the composer | `images/runner/patches/0001-rate-limits.patch`, `images/server/patches/0002-rate-limits.patch`, `images/server/web-patches/0001-composer-rate-limits.patch` | Always on |
| Sandbox model picker before first runner | `images/server/patches/0001-sandbox-model-catalog-fallback.patch`, `scripts/render` | Always on |
| Root + Podman in runner Pods | `kubernetes/base/runner-userns.yaml`, `images/runner/containers/`, `scripts/check-userns` | Always on |
| Claude Code permission bypass | `images/runner/claude-wrapper.sh` | `claude_bypass_permissions` |
| Codex approval/sandbox bypass | `images/runner/codex-wrapper.sh` | `codex_bypass_approvals` |
| GitHub App repository picker | `kubernetes/base/vault.yaml`, `scripts/setup-github-app` | `mise run setup-github-app` |

## Harness usage limits next to the context ring

The composer shows each harness login's subscription usage (the 5-hour and
weekly windows for Claude and Codex) next to the context ring. This appears in
the browser, desktop and mobile apps, because all three load the server's web UI.

- **Runner patch:** the Claude and Codex forwarders report the rate-limit
  windows their CLI exposes.
- **Server patch:** stores the latest windows per session and broadcasts them
  as a session event.
- **Web patch:** adds `ComposerRateLimits` and wires the event into the chat
  store.

The upstream image ships a prebuilt web UI, so the server image rebuilds it
from `omnigent_commit` with `images/server/web-patches/` applied. It uses the
pinned `web_builder` Node image and `pnpm`. A session shows the windows once
its harness first reports them: for Claude that is after the first response,
and for Codex at the first turn. Drop the web rebuild once no web patches
remain.

## Sandbox models before a session starts

With subscription logins the server has no way to list models, so the
Kubernetes sandbox picker showed "Models unavailable" until a runner existed.
The server patch keeps the model catalog that runners last reported for each
harness and serves it as a preview. The catalog is stored in
`/data/artifacts/.deployment/sandbox-model-catalogs.json`, set through
`OMNIGENT_SANDBOX_CATALOG_FALLBACK_PATH`. The runner still validates the chosen
model at launch.

## Root and Podman inside runner Pods

Runner agents can `apt-get install` packages and build or run containers.

- A `MutatingAdmissionPolicy` rewrites runner Pods (`omnigent.ai/role:
  sandbox-host`) to use `hostUsers: false` and run as UID 0. That is root
  inside a per-Pod user namespace, not on the host. A validating policy rejects
  any Pod in `omnigent-sandboxes` that shares host users or namespaces, mounts
  `hostPath`, runs privileged, or uses host ports.
- The runner image ships Podman, with `docker` as an alias. Containers run
  without cgroups and share the Pod's network, because containerd does not
  delegate cgroups to user-namespaced Pods. The Pod's own resource limits
  still apply. Image storage lives on the HOME volume, which is bounded by
  `runner_home_limit`.
- Not available: the Docker daemon API, `docker buildx`, and container port
  publishing.
- Run `scripts/check-userns` to re-verify user-namespace support after k3s
  upgrades.

## Agent permission bypass policies

Both of these are explicit settings in `environments/production.toml`, and
both are currently enabled per the accepted policy. See
[the threat model](THREAT_MODEL.md).

- `claude_bypass_permissions`: the runner's `claude` wrapper writes
  `/etc/claude-code/managed-settings.json` with
  `defaultMode: bypassPermissions`. Managed settings outrank user and project
  settings, so the policy holds whatever the session configures. It also sets
  `skipDangerousModePermissionPrompt`. Without it, Claude Code shows a consent
  dialog the first time it starts in bypass mode, and session start blocks
  because Omnigent cannot answer the dialog.
- `codex_bypass_approvals`: the `codex` wrapper adds
  `--dangerously-bypass-approvals-and-sandbox`. It also links the shared Codex
  auth from the `codex-home` PVC into each session's `CODEX_HOME`.

## GitHub App integration

`mise run setup-github-app` configures a GitHub App (Client ID, secret and
slug) for the repository picker under Settings -> Sandbox Integrations. The
credentials are encrypted by an in-cluster Vault Transit engine. The Vault
unseal material stays in Kubernetes Secrets so restarts need no one present.
The server image adds the pinned `hvac` client for this.
