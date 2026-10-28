# No upstream voice dictation

Upstream Omnigent's voice dictation didn't work reliably, and a phone's
keyboard has its own voice input, so its microphone button is gone from:

- a session's composer
- the **New session** screen
- the side chat

## Good to know

- **⌘⌥V does nothing,** and **Keyboard shortcuts** no longer lists
  **Toggle voice dictation**.
- **Your phone's voice input still works,** as it does in any text field.
- **The mic button you see is push-to-talk's,** this deployment's own
  dictation through your speech server. Hold it, or Right ⌥, and talk; see
  [push-to-talk](push-to-talk.md).

## Turning it off

You can't.

## How it works

The web UI patch `images/server/web-patches/0035-remove-voice-dictation.patch`
removes the mic button from the three composers, with its ⌘⌥V shortcut. The
server's dictation endpoint is unchanged, but nothing in the web UI uses it.
