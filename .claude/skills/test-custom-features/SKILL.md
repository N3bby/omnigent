---
name: test-custom-features
description: Test that this deployment's custom features on top of upstream Omnigent still work, typically after upgrading Omnigent (omnigent_commit in versions.yaml) or the runner's agent CLIs. Covers usage limits, the model picker, root and containers in runners, runtimes from mise, no permission prompts, the GitHub repository picker, idle sessions, runners on the tailnet, Open in Gateway, the removed Share button, the runner description agents get, and repository variables. Run it from an Omnigent session on the deployment under test.
---

# Test the custom features

`docs/features/` describes each feature, one page each. This skill tests them in
layers, from cheap and offline to live, and ends with a report and a short
checklist for the human. **Expected** values below are the results on Omnigent
`v0.15.0` (2026-10-07). After an upgrade the same assertions should hold; when
one doesn't, report it as a regression rather than editing the expectation.

## Before you start

- You run inside a runner Pod of the deployment, as root, in an Omnigent
  session: `hostname` starts with `omnigent-managed-`. If not, stop; the
  runner and live checks would test the wrong machine.
- The session must have **started after the upgrade was deployed**. A session
  started earlier runs the old runner image, so its runner checks prove nothing.
  `omnigent --version` prints the `omnigent` version from `versions.yaml`
  without its `v`, for example `omnigent 0.15.0 (built …)` for `v0.15.0`.
- Know which environment you are testing (`environments/*.toml`; you need its
  `hostname`). Nothing in the Pod tells them apart: several environments share
  a tailnet, and the runner reaches the server by an in-cluster name. Use what
  the user said. Otherwise ask, and if you can't ask, the browser tab's
  hostname in step 5b settles it. Until then, run the environment-specific
  steps for every candidate and say so in the report.
- Use only the credentials already in this Pod and the Omnigent session tools.
  Don't deploy, change `environments/`, rotate credentials, or touch other
  users' sessions.
- Name everything you create `feature-check …`, and clean it up at the end
  (see [Clean up](#7-clean-up)).

Record each check as PASS, FAIL or SKIP (with the reason) as you go. The
report at the end needs them.

## 1. What is under test

```bash
git log --oneline -5
grep -E '^(omnigent|omnigent_commit|claude_code|codex_cli|tailscale|mise|jetbrains_idea_build):' versions.yaml
```

Note the upstream version and commit. If the user gave you a candidate
upstream commit or tag that isn't pinned yet, also run step 2 against it. Only
the pinned commit is deployed, so steps 3–5 test that one. Report the
candidate's results in their own section; they aren't deployment failures.

## 2. Patches still apply

```bash
command -v patch || { apt-get update -qq && apt-get install -y -qq patch; }   # the image builds use GNU patch
.claude/skills/test-custom-features/scripts/check-patches [<upstream commit or tag>]
```

This applies every patch to a fresh upstream checkout, the way the image builds
do (in order, `--fuzz=0`, each image's set on clean source), and
byte-compiles the patched Python. Exit 0 means everything applies, 1 means a
patch failed, and 2 means it couldn't run (no `patch`, or the commit couldn't be
fetched).

**Expected:** exit 0 and:

```
upstream: <the pinned omnigent_commit>
PASS  images/runner/patches/0001-rate-limits.patch
PASS  images/runner/patches: patched Python compiles
PASS  images/server/patches/0001-sandbox-model-catalog-fallback.patch
PASS  images/server/patches/0002-rate-limits.patch
PASS  images/server/patches/0003-tailscale-host.patch
PASS  images/server/patches: patched Python compiles
PASS  images/server/web-patches/0001-composer-rate-limits.patch
PASS  images/server/web-patches/0002-composer-tailscale-host.patch
PASS  images/server/web-patches/0003-composer-gateway-link.patch
PASS  images/server/web-patches/0004-remove-share-button.patch
```

A `FAIL` lists the hunks that didn't apply: upstream changed the code under
that patch, and the image build for it would fail. The script prints where it
kept the `.rej` files, one directory per patch. Patches in a set build on one
another, so a FAIL marked `after an earlier failure in this set` may only be a
knock-on of the first one; rebase them in order. For each failing patch, check
whether upstream now ships the behaviour itself (then the patch can go, see
"Keeping the patches up to date" in `docs/features/README.md`) or needs
rebasing. Name the feature it belongs to in the report.

For reference, on 2026-10-07 upstream `v0.18.0.dev20261007` failed
`server/0003-tailscale-host` and all four web patches, while the runner patch
and server patches 0001 and 0002 applied. Web `0004-remove-share-button` also
fails on its own.

## 3. Repository validation

`scripts/render` needs kubectl. Without it, render falls back to Docker
(`docker cp`), which fails under the runner's Podman with `"/work/kubernetes"
could not be found`. Install the version and digest pinned in
`.github/workflows/validate.yml`:

```bash
command -v kubectl || {
  dir="$(mktemp -d)"
  curl -fsSLo "$dir/kubectl" https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl
  echo "8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a  $dir/kubectl" | sha256sum -c - \
    && install -m 0755 "$dir/kubectl" /usr/local/bin/kubectl
  rm -rf "$dir"
}
./scripts/preflight --environment ci
./scripts/preflight --environment <env under test>
python -m unittest discover -s tests
```

**Expected:** `configuration and manifests for <env> are valid` for both, and
the unit tests end in `OK` (17 tests on 2026-10-07). Warnings that
`ansible-playbook` or `shellcheck` isn't installed are fine. The tests cover
the rendered settings behind several features, such as idle-session timing and
the per-session home volume. They are the local part of the `Validate`
workflow, which also runs kubeconform and shellcheck in CI.

## 4. Runner checks

```bash
.claude/skills/test-custom-features/scripts/check-runner
```

This takes about 10 seconds, plus pulling two small images (alpine, nginx) and
installing Node 20.18.0 through mise the first time. The script removes the
containers it starts, any image it had to pull, and the Node it installed.
`--quick` skips apt-get, the containers and the Node install; step 5a uses it
for a re-run.

**Expected** (full run, in a Claude Code session on a deployment with
Tailscale set up). The names and numbers in angle brackets differ per session:

```
== Pinned versions
PASS  omnigent v0.15.0
PASS  claude_code 2.1.285
PASS  codex_cli 0.159.1
PASS  tailscale 1.102.5
PASS  mise 2026.10.3
== Usage limits: runner patch is installed
PASS  claude-native posts rate limits
PASS  claude-native reads statusLine rate_limits
PASS  codex-native posts rate limits
PASS  Claude statusLine reports usage windows: 5h <n>% · 7d <n>%
== Agents don't stop to ask
PASS  Claude managed settings bypass permissions
PASS  claude is the deployment wrapper
PASS  codex is the deployment wrapper
SKIP  running Codex has --dangerously-bypass-approvals-and-sandbox (no Codex running in this Pod)
== Root and containers
PASS  root is mapped by a user namespace
PASS  Podman service is running
PASS  Docker socket points at Podman
PASS  Docker API answers on the default socket
PASS  container images live in the home volume
PASS  docker compose is installed
PASS  apt-get install as root
PASS  docker run
PASS  docker build
PASS  published port reachable on localhost
PASS  compose: depends_on service_healthy, service names resolve
PASS  compose: published port reachable on localhost
== Idle sessions keep their files
PASS  home directory is its own volume
== Runners on your tailnet
PASS  tailnet backend Running
PASS  joined as omnigent-<8 hex>.<tailnet>
PASS  name matches this Pod's host id (omnigent-managed-<8 hex>-<suffix>)
PASS  advertises tags tag:omnigent-runner
PASS  Tailscale SSH enabled
PASS  node state is on the home volume
PASS  logs out when the Pod stops
== Runtimes from mise
PASS  login shells put mise shims first on PATH
PASS  Claude is told to use mise
PASS  Codex is told to use mise
PASS  the Pod starts with Omnigent's Python: /opt/venv/bin/python3
PASS  git's GitHub helper runs Omnigent's Python: /opt/venv/bin/python
PASS  a project's .mise.toml pins node 20.18.0
PASS  outside a project, node is the image's
PASS  codex still runs in that project
== Open in Gateway: IntelliJ backend
PASS  backend at /opt/jetbrains/intellij is executable
PASS  backend is IntelliJ IDEA Ultimate 263.6259.32
== Agents know the runner
PASS  /etc/claude-code/CLAUDE.md matches images/runner/agent-instructions.md
PASS  /opt/codex-home/AGENTS.md matches images/runner/agent-instructions.md
SKIP  a Codex session's CODEX_HOME has the AGENTS.md (no Codex session in this Pod)
== Repository variables
PASS  loader matches images/runner/repo-env/repo-env.sh
PASS  login shells load repository variables
PASS  claude wrapper loads repository variables
PASS  codex wrapper loads repository variables
SKIP  the agent has this session's repository variables (none stored for this session's repository)

runner checks: passed
```

The version lines must match `versions.yaml`, whatever it pins now. Write
down the `joined as` name and the `5h <n>% · 7d <n>%` usage, which step 5
compares with the UI. Usage keeps moving while agents work, so expect a point
or two of difference. The two Codex `SKIP`s turn into checks in step 5. If the
deployment has no Tailscale key, the tailnet block is a single `SKIP`. That's
fine; skip the Tailscale and Gateway UI checks too.

What a failure usually means:

| Failing check | Likely cause |
| --- | --- |
| a version line | the image wasn't rebuilt or locked (`mise run lock-images`), or this session predates the deploy |
| `… posts rate limits` | the runner patch was dropped or upstream moved the code; see step 2 |
| `Claude statusLine reports …` | the Claude Code statusLine payload changed shape, or it's not a Pro/Max login |
| managed settings / wrappers | `images/runner/Dockerfile` no longer installs the wrappers over the real CLIs, or `claude_bypass_permissions` is off |
| user namespace / Podman / compose | k3s, containerd or the admission policies changed; run `scripts/check-userns` on the VM |
| tailnet | see `/run/omnigent-tailscale.log`; the key may have expired (`mise run credential-status`) |
| mise shims, pinned Node | `images/runner/Dockerfile` no longer installs `images/runner/mise/mise-profile.sh`, or a newer mise changed its settings or shims; `mise doctor` in a login shell shows what it sees |
| `the Pod starts with Omnigent's Python` | `images/server/patches/0005-runner-python.patch` was dropped, or this session's Pod started before the deploy; `PID 1 is python3` means a Python pinned for the home directory (`mise use -g python@…`) stops the Pod from starting |
| `git's GitHub helper runs Omnigent's Python` | `images/runner/patches/0002-git-helper-python.patch` was dropped, or this session predates the deploy; `runs python3` means git uses whichever Python mise picks, so pushes fail in projects that pin Python |
| `… is told to use mise` | `images/runner/agent-instructions.md` lost its mise advice, or isn't installed; see the agent-instructions rows |
| `… matches images/runner/agent-instructions.md` | the file changed in this checkout after the image was built, so deploy it, or this session started before the deploy; `missing` means `images/runner/Dockerfile` no longer copies it |
| repository variables loader or wrappers | `images/runner/Dockerfile` no longer installs `images/runner/repo-env/repo-env.sh`, or a wrapper lost its `. /usr/local/lib/omnigent/repo-env.sh` line |
| `the agent has this session's repository variables` | the Secret is mounted, but the agent didn't load it: the agent started before the Secret was stored, or the wrapper isn't the `claude`/`codex` Omnigent runs |
| `a Codex session's CODEX_HOME has the AGENTS.md` | upstream stopped linking `AGENTS.md` into each session's private `CODEX_HOME` (`_CODEX_HOME_GLOBAL_INSTRUCTION_FILES` in `omnigent/inner/codex_executor.py`) |

## 5. Live checks

These use the Omnigent session tools and the Omnigent desktop app's browser.
If the session tools aren't advertised to you, load them with ToolSearch
(`sys_session`, `browser`). Sessions you create with `sys_session_create` are
children of this session and **run in this same Pod**, so you can inspect
their processes and files directly. They don't start a new Pod.

### 5a. Agents don't stop to ask, and know the runner, for both agents

1. `sys_agent_list`, and pick the built-in Claude Code and Codex agents (the
   `claude-native` and `codex-native` harnesses).
2. For each, `sys_session_create` with `title: "feature-check claude"` (or
   `codex`) and the message:
   `Run this shell command and reply with its output only: touch /tmp/feature-check-<agent> && echo created`
3. Poll `sys_session_get_history` until the agent replies (allow a few
   minutes for the first turn). Use `sys_session_get_info` to watch for
   outstanding approval prompts.
4. Then `sys_session_send` each session this message, and poll for the reply
   again:

   ```
   Answer from what you already know about this environment, one short line
   each. Don't run commands or read files for questions 1-3.
   1. Which directory survives this Pod stopping?
   2. How would you install a CLI tool that this project doesn't pin?
   3. If you wrote report.md in /home/omnigent/workspace, how would you point me to it?
   4. What URL would I use to reach a dev server you start here on port 3000?
   ```

**Expected:** each replies `created`, `/tmp/feature-check-<agent>` exists in
this Pod, and `sys_session_get_info` never shows an outstanding approval
prompt. While the Codex session is alive, `pgrep -a codex-real` shows
`--dangerously-bypass-approvals-and-sandbox`. Re-run
`check-runner --quick`; both Codex lines should now `PASS`. If Codex isn't
logged in (its session errors with an auth message), record Codex as SKIP and
continue.

The answers to those questions come from
`images/runner/agent-instructions.md`, so **expect**, from each agent:

1. `/home/omnigent`.
2. `mise use -g <tool>@<version>`, not `apt-get` and not plain `mise use`.
3. A Markdown link to the absolute path,
   `[report.md](/home/omnigent/workspace/report.md)`.
4. `http://<the joined as name from check-runner>:3000`, not `localhost`.
   The agent may run `tailscale status` for this. Without Tailscale, it should
   say the Pod isn't on the tailnet rather than make up an address.

An agent that runs commands or reads its instructions file to answer 1–3, or
that answers them generically, didn't get the file: FAIL, and check the
agent-instructions lines from `check-runner`. Record one result per agent.

### 5b. In the web UI

Call `browser_snapshot` first: the URL the desktop app already has open tells
you the deployment's hostname, if you don't know it yet. Then open
`https://<hostname>/` with `browser_navigate` and read the page with
`browser_snapshot`. The browser tools need the Omnigent desktop app window.
If they fail, or the page asks you to log in, SKIP all of 5b with that reason
and add these checks to the manual list. Don't try to log in. Open this
session (your own session's title, from `sys_session_get_info`) from the
sidebar. Only read pages and open menus; don't submit forms, share, or change
settings.

| Feature | Expected |
| --- | --- |
| Usage limits | Next to the context ring, a button labelled like `5-hour limit 13% used, Weekly limit 56% used` and showing `5h 13% · 7d 56%`. The percentages are the rounded `used_percent` values from step 4. Clicking it opens **Usage limits** with a bar and `Resets in …` per window. |
| Runners on your tailnet | After the working directory, the Tailscale mark and `omnigent-<8 hex>`, the `joined as` short name from step 4. The control is labelled `Copy Tailscale name omnigent-<8 hex>.<tailnet>`. |
| Open in Gateway | Next to it, a link **Open in Gateway** titled `Open <workspace> in JetBrains Gateway on <full Tailscale name>`. If the snapshot exposes its URL, it is `jetbrains-gateway://connect#type=ssh&host=<full name>&port=22&user=root&projectPath=<workspace, URL-encoded>&idePath=%2Fopt%2Fjetbrains%2Fintellij&deploy=false`. |
| No Share button | No **Share** in the chat header or its menu. Open a session row's menu (⋯) in the sidebar: it has **Fork** but no **Share**. Close the menu with Escape. |
| Model picker | Start a new session (don't send it) with the Kubernetes sandbox and Claude Code: the model menu lists models, such as Opus and Sonnet, and never says `Models unavailable`. Repeat for Codex. Then discard it. |
| GitHub repository picker | In Settings → Sandbox Integrations, GitHub is listed. If it shows as connected, the new-session repository picker lists repositories. If it isn't connected, record SKIP (optional setup). |

Also check that `feature-check claude` from 5a now shows usage numbers in its
own composer. They belong to the login, so they match this session's.

### 5c. Idle sessions stop and keep their files

Waiting out the timers takes hours, so check the evidence instead:

- `grep -E '^runner_(agent_idle|idle_shutdown)_seconds' environments/<env>.toml`.
  **Expected:** unset (defaults 1800 and 3600) or what the user chose.
  Step 3's preflight for the environment checks that they're valid, and the unit
  tests check that render turns them into the Sandbox shutdown settings.
- `check-runner` already showed that the home directory and the Tailscale
  state are on the session's own volume. Whether a stopped Pod really comes
  back with them is in the human checklist (step 6).

## 6. Checks for the human

Hand these to the user; they need their own devices or VM access:

1. **Tailscale from a laptop on the tailnet:** `ssh root@omnigent-<8 hex>`
   opens a shell in the session's Pod. Start `python3 -m http.server 3000` in
   the session, then `curl http://omnigent-<8 hex>:3000` from the laptop.
2. **Gateway:** on desktop, click **Open in Gateway**. Gateway connects as
   `root` and opens the session's repository in IntelliJ.
3. **Mobile:** the Tailscale name still shows, **Open in Gateway** doesn't, and
   tapping the usage numbers opens the bars.
4. **Idle stop and wake:** after 1 idle hour, `mise run status` shows the
   session `Ready=False`, `SandboxExpired`. Sending a message wakes it with
   its repository, uncommitted changes and Tailscale name intact. The
   session's node leaves the
   [Tailscale machine list](https://login.tailscale.com/admin/machines) as
   the Pod stops, not up to an hour later.
5. **After a k3s upgrade:** `scripts/check-userns` on the VM. Its summary
   should show `user namespace active`, `apt install`, `podman run` and the
   published-port lines as `PASS`.
6. **Model picker on a fresh install:** empty until the first session has run,
   then filled.
7. **Repository variables:** pick a repository with no variables yet
   (`mise run credential-status` lists those that have them), because storing
   replaces them all. From your machine, run `echo FEATURE_CHECK=ok | mise run
   setup-repo-env <owner/repo>` and start a session on that repository: its
   agent prints `ok` for `echo $FEATURE_CHECK`, and a session on another
   repository prints nothing. Then `mise run setup-repo-env --delete
   <owner/repo>`.

## 7. Clean up

- `sys_session_close` each `feature-check …` session you created. Closing
  doesn't delete it. List their titles in the report so the user can delete
  them in the UI.
- `rm -f /tmp/feature-check-*`. `check-runner` and `check-patches` clean up
  after themselves, except the `.rej` directory `check-patches` prints on a
  failure. Leave that for the user or delete it once you've reported. What you
  installed outside the home directory (`patch`, kubectl, the `sl` package
  `check-runner` installs) is gone when the Pod is recreated.

## 8. Report

Finish with one table: feature, check, result (PASS, FAIL or SKIP), and
evidence (the command output line, the UI text, or the skip reason). Order
failures first. Then:

- for each FAIL: what you observed against what was expected, the likely
  patch or file (see "How it works" on the feature's page in `docs/features/`), and whether
  step 2 shows the patch no longer applies
- step 2 results for any candidate upstream commit, separately
- the sessions left for the user to delete
- the step 6 checklist, plus any 5a/5b checks you had to skip, with the
  values they should show (for example the usage numbers and Tailscale name
  from step 4)

Don't fix failures as part of this skill unless the user asked you to.
