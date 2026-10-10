# No pin or archive buttons in the sidebar

On a computer, hovering a session in the sidebar shows only its **⋯**
button. Upstream Omnigent also shows a pin button and an archive button
next to it, which are easy to click by accident.

## What you'll notice

- **Pin and Unpin are in the session's menu.** Open it with **⋯** or a
  right-click. Upstream only shows them there on a phone.
- **Archive and Unarchive are in the same menu,** as upstream already has
  them.
- **Dragging a session onto Pinned still pins it.**
- **Phones and tablets don't change.** They never had these buttons.

## Turning it off

You can't.

## How it works

The web UI patch
`images/server/web-patches/0013-remove-sidebar-quick-actions.patch`:

- removes the two buttons from each session row
- shows the menu's Pin item at every window size
- reserves only the **⋯** button's width for the title when you hover a row,
  so long titles keep more of their text
