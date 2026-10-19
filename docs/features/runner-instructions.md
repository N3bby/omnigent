# Agents know where they're running

Every Claude Code and Codex session starts with a short description of the
session's runner, so you don't have to explain it yourself.

## What agents know

- They're root, and can run containers.
- They should install tools with [mise](mise-runtimes.md) before `apt-get`.
- Only the home directory survives an [idle session](idle-sessions.md)
  stopping.
- Not to save memories. The home directory belongs to one session, so a new
  session would never see them.
- Which name and email to commit as, when git has none set. That happens when
  you haven't connected GitHub.
- To link files as a Markdown link to the absolute path, so you can open them
  from the browser, and to keep those files in the home directory, not in
  `/tmp`, where links don't open and files don't survive the Pod stopping.
- To [show images](inline-media.md), such as screenshots, in the chat rather
  than link them, several in a row so they share a grid, and that video and
  audio files play there too.
- To give the [tailnet address](tailscale.md), not `localhost`, when you ask
  how to reach something running in the session.
- How to [reach your other devices](tailscale.md#reaching-your-devices-from-a-session)
  on the tailnet: SSH as usual, anything else through the proxy on
  `localhost:1055`.

## Changing the text

The text is in `images/runner/agent-instructions.md`. It's the same for every
deployment. To change it, edit the file and deploy.

- **New sessions** get the new text.
- **Existing sessions** get it the next time their Pod starts.
- **A repository's own `CLAUDE.md` or `AGENTS.md`** still applies on top, so
  keep project rules there.
- **Your own rules for one repository** go in its
  [agent instructions](repo-instructions.md), which Omnigent appends to this
  text in that repository's sessions.

Agents read the file at the start of every session, so keep it short and
accurate. A wrong port or path there misleads every agent.

## How it works

The runner image copies the file to two places, and keeps an untouched copy
at `/usr/local/share/omnigent/agent-instructions.md`. Before an agent
starts, its wrapper rewrites its file from that copy, adding the
[instructions of the session's repositories](repo-instructions.md#how-it-works)
when they have any:

| Agent | Path | Why there |
| --- | --- | --- |
| Claude Code | `/etc/claude-code/CLAUDE.md` | The managed instructions file, which Claude Code always loads, even when an agent turns off other setting sources. |
| Codex | `/opt/codex-home/AGENTS.md` | Omnigent gives each Codex session a private `CODEX_HOME`, and links `AGENTS.md` into it from `/opt/codex-home`, the same way it links the login. |
