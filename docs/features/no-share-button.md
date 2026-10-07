# No Share button

The web UI has no **Share** button. It's gone from:

- the chat header
- the header's menu on mobile
- each session's menu in the sidebar

## Good to know

- Admins still see **Settings → Sharing**.
- The server's sharing API still works.

## Turning it off

You can't.

## How it works

The web UI patch `images/server/web-patches/0004-remove-share-button.patch`
hides Share in the header and removes the sidebar menu item.
