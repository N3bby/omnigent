# The Even G2 glasses app

Your sessions on Even Realities G2 glasses, from the Even Hub app in
[N3bby/omnigent-even-g2](https://github.com/N3bby/omnigent-even-g2): see
them grouped by project, get an alert when one finishes or asks something,
read replies, diagrams and screenshots, answer questions, and send quick
replies with the R1 ring, with the phone in your pocket.

The app is a web page in the Even app's WebView on the phone, so every call
it makes to this server comes from another origin. Browsers and WebViews
refuse to read such a response unless the server sends CORS headers, and
upstream Omnigent sends none. This deployment adds them.

## What you'll notice

Nothing in the web UI. With the setting on, the server answers CORS
preflights and adds `Access-Control-Allow-Origin` to API responses, for the
origins you list.

## The setting

In `environments/<env>.toml`:

```toml
cors_allowed_origins = "*"
```

- **`"*"`** allows any origin. Production uses it, because the Even app
  doesn't document the Origin its WebView sends.
- **A comma-separated list**, such as `"https://app.example.com,http://localhost:5173"`,
  allows only those, each as `scheme://host` with an optional port.
- **Empty or missing** turns CORS off, as upstream. The glasses app then
  can't sign in.

It becomes `OMNIGENT_CORS_ALLOWED_ORIGINS` in the server's config, so a
change needs a deploy.

## Why `"*"` is safe here

The glasses app signs in with your username and password, and then sends a
Bearer token. CORS here never allows credentials, so a page on another
site still can't make a request with your browser's Omnigent cookie: it
would need a token, which it can't get without your password. See the
[threat model](../threat-model.md#trade-offs-worth-knowing).

## How it's built

`images/server/patches/0023-cors-origins.patch` adds Starlette's
`CORSMiddleware` as the outermost middleware when
`OMNIGENT_CORS_ALLOWED_ORIGINS` is set, allowing the `Authorization`,
`Content-Type`, `Accept` and `Last-Event-ID` headers and no credentials.
`scripts/config.py` checks the setting and `scripts/render` passes it on.
