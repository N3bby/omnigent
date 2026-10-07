# Agents know where they're running

Every Claude Code and Codex session starts with a short description of the
session's runner, so you don't have to explain it yourself.

## What agents know

- They're root, and can run containers.
- They should install tools with [mise](mise-runtimes.md) before `apt-get`.
- Only the home directory survives an [idle session](idle-sessions.md)
  stopping.
- Which name and email to commit as, when git has none set. That happens when
  you haven't connected GitHub.
- To link files as a Markdown link to the absolute path, so you can open them
  from the browser.
- To give the [tailnet address](tailscale.md), not `localhost`, when you ask
  how to reach something running in the session.

## Changing the text

The text is in `images/runner/agent-instructions.md`. It's the same for every
deployment. To change it, edit the file and deploy.

- **New sessions** get the new text.
- **Existing sessions** get it the next time their Pod starts.
- **A repository's own `CLAUDE.md` or `AGENTS.md`** still applies on top, so
  keep project rules there.

Agents read the file at the start of every session, so keep it short and
accurate. A wrong port or path there misleads every agent.

## How it works

The runner image copies the file to two places:

| Agent | Path | Why there |
| --- | --- | --- |
| Claude Code | `/etc/claude-code/CLAUDE.md` | The managed instructions file, which Claude Code always loads, even when an agent turns off other setting sources. |
| Codex | `/opt/codex-home/AGENTS.md` | Omnigent gives each Codex session a private `CODEX_HOME`, and links `AGENTS.md` into it from `/opt/codex-home`, the same way it links the login. |
