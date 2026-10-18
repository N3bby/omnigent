# Your first message survives closing the app

Send a new session's first message and close the app straight away, before
the sandbox has started. The message still reaches the agent once the
sandbox is up.

## What you'll notice

- **Closing the app during the start doesn't lose your message.** On a
  phone, sending the first message and switching away, or locking the
  screen, used to leave the session without it: the agent started and never
  got it. Open the session again and the agent is working on it, or has
  answered.
- **Stop during the start still takes the message back.** It's back in the
  composer, and the chat says **Stopped before the sandbox started, so the
  message wasn't sent.** Edit it and send it again.
- **Until the sandbox is up, a reopened chat doesn't show the message.** It
  waits on the server, not in the chat, so after a reload you see the
  [startup card](startup-progress.md) and nothing else. The message appears
  when the agent gets it.

## Good to know

- **It's for sessions in a sandbox, while the sandbox starts.** A session
  on your own machine starts in seconds and keeps the message in the browser
  until the agent is ready, as before. So does a message sent while you
  switch the model.
- **Codex sessions already worked this way.** Only Claude Code sessions kept
  their first message in the browser.

## Turning it off

There's nothing to turn off.

## How it works

Upstream's web UI keeps a Claude Code session's first message in the browser
until Claude Code reports its model, so that Stop can still put the message
back in the composer before it reaches the agent. For a sandbox session that
wait is the whole start, a minute or more, and on a phone the browser is
suspended or closed long before it ends.

- **The web UI patch**
  `images/server/web-patches/0029-first-message-while-starting.patch` stops
  that wait while the session's sandbox is starting. The message is posted
  straight away, and the server holds it until the sandbox is up, as it
  already did for Codex. The server keeps holding it after the browser goes
  away.
- **The server patch**
  `images/server/patches/0020-stop-message-waiting-for-sandbox.patch` lets
  Stop take back a message that's waiting for the sandbox. The message's
  request answers `409`, which gives the web UI the draft back.
- **Delete both** when upstream posts a Claude Code session's first message
  while its sandbox starts: upstream's `modelSelectionPending` in
  `web/src/store/chatStore.ts` no longer waits on a sandbox that's starting.
