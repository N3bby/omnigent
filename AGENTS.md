# Agent guide

This repo deploys a customized [Omnigent](https://github.com/omnigent-ai/omnigent)
server to a single VM running k3s. It isn't Omnigent's source: it pins an
upstream commit (`omnigent_commit` in `versions.yaml`) and adds patches and
configuration on top. See [README.md](README.md) and [docs/](docs/README.md).

- `CLAUDE.md` imports this file so Claude Code reads it too. Edit this file,
  and keep `CLAUDE.md` as the one-line import.
- `images/runner/agent-instructions.md` is not for you: it's what agents in
  every session of the deployment are told. Change it only when the user wants
  to change that.
- `.agents/skills/` is a symlink to `.claude/skills/` for Codex; keep it one.

## Rules

- **Only run `check`, `render` and `test` on your own.** The other mise tasks
  (`deploy`, `bootstrap`, `diff`, `status`, `smoke`, `lock-images`,
  `credential-status`, `setup-*`) reach the live deployment or its
  credentials, and it has no backups. Run them only when the user asks.
- **Don't hand-edit image digests in `environments/*.toml`.** The Deploy
  workflow and `mise run lock-images` write them for images GitHub Actions
  built; a typed digest points at an image nobody built.

## Testing

```bash
mise install && apt-get install -y shellcheck patch
ENV=ci INVENTORY=ansible/inventory/production/hosts.yml mise run test   # as in CI
```

Without `shellcheck`, `check` only warns and skips the lint that CI enforces.

## Patches

Most custom features are patches in `images/runner/patches/`,
`images/server/patches/` and `images/server/web-patches/`. Builds apply each
directory in order with `--fuzz=0`, so a patch must apply exactly on top of
the earlier ones. The server image leaves out `tests/` diffs, but the runner
image applies them, so **runner patches may only add test files**. Nothing
you run catches that: only the image build does, when the user runs the
Publish or Deploy workflow.

To write or change one, build a throwaway Git repo in `.generated/upstream`
(ignored) from `omnigent_commit`, with one commit per patch, applied as the
build does and stopping at the first that fails. Use the server and web sets
together, or the runner set alone (both add `omnigent/native/fast_mode.py`).
Ignore upstream's own `AGENTS.md` and contribution workflow in there. Below,
`<uv>` is the version in `.mise.toml`; `<node>` and `<pnpm>` are
`web_builder` (without `-slim`) and `pnpm` in `versions.yaml`.

- **New patch:** export it with `git diff --cached -- web/` (or
  `-- omnigent tests`) to the next free number in its directory.
- **Existing patch:** amend its commit, cherry-pick the later ones onto it,
  and re-export each that changed with `git diff <commit>~ <commit>`. Only the
  `.patch` files land in this repo.
- **Description:** an export has none, so copy the old one back, or write one
  like its neighbours'. An upstream bug fix says to delete it once upstream
  ships a fix.
- **Web changes:** the build only runs `vite build`, so also run `type-check`,
  `lint` and your vitest file in the checkout:
  `mise exec node@<node> pnpm@<pnpm> -- pnpm --dir web type-check`.
- **A new module of ours** (`images/server/omnigent_*.py`) needs a `COPY` in
  `images/server/Dockerfile`. Tests find it on `PYTHONPATH`, so a missing
  `COPY` only shows in the image build.

### Try it locally

Test as much as you can in your own session before asking the user to
deploy. The patched server and web UI run from the checkout with the server
and web sets:

```bash
OMNIGENT_SKIP_WEB_UI=true mise exec uv@<uv> -- uv sync --extra all
OMNIGENT_DATA_DIR=<this repo>/.generated/dev-data PYTHONPATH=<this repo>/images/server \
  mise exec uv@<uv> -- uv run omnigent server --port 7767 &
cd web && __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=.ts.net OMNIGENT_URL=http://127.0.0.1:7767 \
  mise exec node@<node> pnpm@<pnpm> -- pnpm run dev --host 0.0.0.0 --port 7173 &
```

Vite reloads web changes; restart the server for Python changes. The user can
open it at port 7173 on the Pod's tailnet address, without a login: the local
server runs in single-user mode. Vite answers only the hostnames it allows,
so without `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` the tailnet address gets
"Blocked request. This host is not allowed".

Drive it yourself with Playwright's headless Chromium, not the Omnigent
browser tools, which control the user's own browser. Install it once with
`mise exec node@<node> -- npx -y playwright@1 install --with-deps chromium`.

**Take screenshots of UI changes**, before and after where that helps, and
link them in your reply so the user can see the change. Use the
[Codex theme](docs/features/codex-theme.md) in dark mode, and save them in
`.generated/screenshots/`. When a feature page shows that part of the UI,
update or add its image in `docs/images/` as a small `.webp` with
descriptive alt text, like the existing ones.

For runner patches, set up a second checkout the same way with the runner
set, and connect its host to that server. Give it its own data and config directories: your
session's own runner is an Omnigent host too. Use absolute paths: a runner
opens its log after changing into the session's workspace, so a relative data
directory makes every session fail to start.

```bash
OMNIGENT_DATA_DIR=<this repo>/.generated/host-data OMNIGENT_CONFIG_HOME=<this repo>/.generated/host-config OMNIGENT_HOST_NO_OPEN=1 \
  mise exec uv@<uv> -- uv run omnigent host --server http://127.0.0.1:7767 --non-interactive &
```

Kubernetes sandboxes don't run locally; those need a deploy.

### Before you commit a patch

In `.claude/skills/test-custom-features/scripts/`, run `check-patches`, then
`check-patched-tests` (add your tests' suites to it and to its header
comment). In the same commit, update:

- **`docs/features/`:** the feature's page and the table in `README.md`. For
  an upstream bug fix, add a row to "Keeping the patches up to date" saying
  when to delete it.
- **`.claude/skills/test-custom-features/SKILL.md`:** the patch list in
  step 2, the suites and their pass counts in step 3b (from a real
  `check-patched-tests` run), and, for a new feature, a check in step 4, 5
  or 6 and its name in the `description`.

## Other changes

- **Upgrading Omnigent:** bump `omnigent`, `omnigent_commit` and both
  `omnigent_*_base_digest` in `versions.yaml`, and the same pins in both
  Dockerfiles' `ARG`s. Set `runner_digest` and `server_digest` in
  `environments/ci.toml` to the base digests, and bump the image tag in
  `kubernetes/base/server.yaml` and the version in `docs/features/README.md`
  and `SKILL.md`. Rebase every patch, and delete the ones upstream made
  unnecessary
  ([keeping the patches up to date](docs/features/README.md#keeping-the-patches-up-to-date)).
  After the user deploys, run the `test-custom-features` skill.
- **Changes needing `mise run bootstrap`** before a deploy (see
  [making changes](docs/operations.md)): say so in the commit message.
