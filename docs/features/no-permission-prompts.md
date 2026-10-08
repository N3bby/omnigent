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

Codex has no separate bypass entry. Its **Full Access** preset is the same
stance: no approval prompts and no sandbox.

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
  let the web picker use it: `images/server/web-patches/0006` lists it,
  `images/server/patches/0007` accepts it, and `images/runner/patches/0003`
  lets the runner cycle to it.
  The wrapper also turns off Claude Code's auto-updater
  (`DISABLE_AUTOUPDATER=1`). An update would replace the wrapper, or hide it
  behind a `claude` shim in a project's mise Node, and new sessions would
  then start without any of this.
- **Codex** (`images/runner/codex-wrapper.sh`): adds
  `--dangerously-bypass-approvals-and-sandbox`. It also links the shared Codex
  login into each session.
