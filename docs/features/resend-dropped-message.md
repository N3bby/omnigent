# A message whose connection dropped is sent again

When you send a message while a session's sandbox starts, the server holds
the request open until the agent can take it, often for half a minute. If
your connection drops in that time, the web UI sends the message again,
instead of telling you **Load failed** about a message the agent gets
anyway. The agent still gets it once.

## What you'll notice

- **No more "Load failed" while a sandbox wakes.** It showed up mostly on a
  phone, where Tailscale on iOS reapplies its routes when the session's Pod
  joins the tailnet, and that drops connections that are open. The message
  stays in the chat with **Working…**, and the agent answers it as usual.
- **When the connection stays down** for about two minutes, the message goes
  back in the composer with: "Couldn't reach the server, so your message may
  not have been sent. It's back in the composer, and sending it again won't
  send it twice." Sending it again, unchanged, is safe: if the first one got
  through, the agent doesn't get it a second time. If you change it first,
  it's sent as a new message.

## Good to know

- **Only messages are sent again.** Approvals, Stop and the other controls
  fail as before, and so does a message the server answered with an error,
  such as "The runner didn't come online in time".
- **The server remembers a message for 15 minutes.** A copy that comes later
  is delivered again.
- **It's per server process.** The deployment runs one; after the server
  restarts, it doesn't remember earlier messages.

## Turning it off

There's nothing to turn off.

## How it works

- **The web UI** (`images/server/web-patches/0024-resend-dropped-message.patch`):
  when `fetch` fails without a response, `postEvent` sends a message that has
  a `stable_id` again, unchanged, after 1, 2, 4 and 8 seconds and then every
  15 seconds. The web UI already gives every message a `stable_id`, and keeps
  it when a failed message goes back in the composer.
- **The server** (`images/server/patches/0017-resend-dropped-message.patch`
  and `images/server/omnigent_message_resend.py`): a message with a
  `stable_id` is delivered once per session, user, `stable_id` and content.
  A copy that comes while the first is still waiting for the sandbox waits
  with it and gets the same answer, or the same error. A copy that comes
  after the first was delivered gets its answer without being delivered
  again. After an error, the next copy is delivered, since the first most
  likely wasn't. Upstream delivered every copy: a Claude Code session got the
  message typed into its terminal twice.
