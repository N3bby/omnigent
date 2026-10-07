# No permission prompts

By default, Claude Code and Codex don't ask for approval before each command
or edit. The session's sandbox is what keeps them contained. See the
[threat model](../threat-model.md) for what that does and doesn't protect.

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
- **Codex** (`images/runner/codex-wrapper.sh`): adds
  `--dangerously-bypass-approvals-and-sandbox`. It also links the shared Codex
  login into each session.
