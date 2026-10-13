# The sandbox picker under the composer

On the **New session** screen, where the session runs is picked in the bar
under the composer, before the repository. It shows its name: **Agent
Sandbox**, or a connected machine's name with its status dot.

## What you'll notice

- **It's a named chip next to the repository**, instead of an icon in the
  composer left of the permission picker.
- **Its menu is the same:** the sandbox, your connected machines and
  **Connect new host**.
- **When the bar runs out of room**, it shows only its icon, as the
  repository chip does.
- **In the Codex theme** the bar is under the composer. In the other themes
  it's the tray above it, so the picker is there.
- **Inside a session nothing moves.** The
  [environment menu](environment-menu.md) stays in the composer.

## Turning it off

You can't.

## How it works

The web UI patch
`images/server/web-patches/0023-sandbox-picker-under-composer.patch` moves the
host menu from the composer's controls to the start of the workspace bar.
Its trigger becomes the same chip as the repository's and the working
directory's, so it gets their size and collapsing. It keeps the old chip's
accessible name, such as `Host: Agent Sandbox, Offline`.
