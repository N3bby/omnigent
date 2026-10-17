# No permission prompts

By default, Claude Code and Codex don't ask for approval before each command
or edit. The session's sandbox is what keeps them contained. See the
[threat model](../threat-model.md) for what that does and doesn't protect.

## Switching back to bypass

A Claude Code session can leave bypass. For example, approving a plan in the
web UI switches it to **Auto** or **Manual**, and Omnigent keeps that mode
when the session restarts after being idle. To go back, pick
**Bypass permissions** in the composer's permission menu (the hand icon). The
session keeps that choice across restarts too.

A new Codex session starts on **Bypass approvals & sandbox** in the same
menu, unless you picked another preset last time; the New session screen
remembers it. **Full access** runs without the sandbox too. **Default** and
**Read only** keep Codex's own sandbox, which has no network: no `curl`, no
package downloads and no tailnet, so `tailscale status` fails. Use them only
when you want that.

## Turning it off

To make an agent ask again, turn off its setting in
`environments/production.toml` and deploy:

| Agent | Setting |
| --- | --- |
| Claude Code | `claude_bypass_permissions` |
| Codex | `codex_bypass_approvals` |

New sessions use the new setting. Sessions that already exist keep the old
one.

This guards against mistakes, not against an attacker: the agent is root in
its Pod, so it could change the setting back itself.

## How it works

- **Claude Code** (`images/runner/claude-wrapper.sh`): writes
  `/etc/claude-code/managed-settings.json` with
  `defaultMode: bypassPermissions`, which a session's own settings can't
  override. It also sets `skipDangerousModePermissionPrompt`. Otherwise
  Claude Code shows a one-time consent dialog that Omnigent can't answer.
  It also adds `--allow-dangerously-skip-permissions`, so bypass stays in
  Claude's shift+tab cycle whatever mode a session starts in. Three patches
  let the web picker use it: `images/server/web-patches/0008` lists it,
  `images/server/patches/0007` accepts it, and `images/runner/patches/0003`
  lets the runner cycle to it.
  The wrapper also turns off Claude Code's auto-updater
  (`DISABLE_AUTOUPDATER=1`). An update would replace the wrapper, or hide it
  behind a `claude` shim in a project's mise Node, and new sessions would
  then start without any of this.
  Whatever the setting, the wrapper also creates `~/.claude.json` with
  `hasCompletedOnboarding` in a new session's home, before any Claude
  starts there. Otherwise Claude Code can open its first-run theme picker,
  which Omnigent can't answer either: upstream sets the key just before the
  terminal starts, but the host's `claude auth status` may already have
  written the file without it, and Claude writes back the config it read.
  The first message then fails after 180s with "Claude Code terminal did not
  become ready". Upstream hasn't fixed this
  ([#8010](https://github.com/omnigent-ai/omnigent/pull/8010) was closed
  unmerged).
- **Codex**: upstream starts a session without its sandbox only when the
  session has the `omnigent.codex_native.bypass_sandbox` label set to `1`,
  which its New session screen sets when you pick bypass. With
  `codex_bypass_approvals` on, `images/server/patches/0017` gives that label
  to every new Codex session that didn't choose: no label, and no
  `--sandbox`, `--ask-for-approval` or `--full-auto` among its launch
  arguments. `images/server/web-patches/0025` makes bypass the screen's
  first choice for Codex, and sends `0` when you pick another preset so the
  server leaves it. `images/runner/codex-wrapper.sh` also adds
  `--dangerously-bypass-approvals-and-sandbox`, but Omnigent starts Codex's
  app-server without the variable it checks, so that alone isn't enough. The
  wrapper also links the shared Codex login into each session.
