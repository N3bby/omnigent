# No microphone in the composer

The composer has no microphone button. Upstream Omnigent's voice dictation
didn't work reliably, and a phone's keyboard has its own voice input. It's
gone from:

- a session's composer
- the **New session** screen
- the side chat

## Good to know

- **⌘⌥V does nothing,** and **Keyboard shortcuts** no longer lists
  **Toggle voice dictation**.
- **Your phone's voice input still works,** as it does in any text field.

## Turning it off

You can't.

## How it works

The web UI patch `images/server/web-patches/0035-remove-voice-dictation.patch`
removes the mic button from the three composers, with its ⌘⌥V shortcut. The
server's dictation endpoint is unchanged, but nothing in the web UI uses it.
