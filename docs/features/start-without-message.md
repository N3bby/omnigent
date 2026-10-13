# Start a session without a message

On the **New session** screen, **Start session** works with an empty
composer. The session's sandbox starts straight away, so you can use
[@ file search](fuzzy-file-mentions.md) in its repository, or SSH in over
[Tailscale](tailscale.md), before you've decided what to ask.

## What you'll notice

- **The send button is never greyed out for want of a message.** Press it,
  or Enter, with nothing typed. It's still greyed out while there's no
  sandbox or host to start on, and its tooltip says why.
- **The chat opens empty.** There's no message bubble and no **Working…**.
  The [startup card](startup-progress.md) shows the sandbox starting, then
  **What should we work on?** once the agent is ready.
- **The sidebar lists the session under its agent**, such as **Claude Code**,
  until it has a title.
- **Starting with a message works as before.**

## Good to know

- **Smart Routing** chooses the agent from your first message. Started
  without one, the session keeps the default agent.
- **The sandbox keeps the usual [idle timeout](idle-sessions.md).** A session
  you start and never use stops when it's idle, like any other.

## Turning it off

There's nothing to turn off. Type a message first if you want the old
behaviour.

## How it works

The web UI patch
`images/server/web-patches/0022-start-without-message.patch`:

- Drops the "Enter a message to get started" condition from the New session
  screen's send button.
- Creating a session was always separate from sending its first message:
  `POST /v1/sessions` starts the sandbox, and the message follows as an event.
  Without a message, the web UI adds no optimistic bubble, leaves the session
  idle, and opens the session's event stream instead of posting a message, so
  the startup card and the agent's readiness still arrive.
