# Omnigent runner

You're running in a Kubernetes Pod that belongs to one Omnigent session. The
user works with you through Omnigent in a browser or the Omnigent apps, not
on this machine, so `localhost` here means nothing to them.

## Environment

- You're root inside the Pod's own user namespace.
- Podman is installed, with `docker` as an alias. `docker build`,
  `docker compose` and Testcontainers work. `docker buildx` and BuildKit
  don't, and per-container resource limits don't apply.
- Only `/home/omnigent` survives the Pod stopping, which happens after the
  session has been idle for a few hours. When the Pod wakes, packages
  installed with `apt-get` and running processes, such as dev servers, are
  gone.

## Installing tools

Use [mise](https://mise.jdx.dev) where you can, and `apt-get` only for what
mise doesn't have. mise installs into the home directory, so its tools
survive the Pod stopping.

- When a project has a `.mise.toml`, `mise.toml` or `.tool-versions`, run
  `mise install` in it before building or testing, so `node`, `python` and
  the other tools it pins resolve to those versions. Use
  `mise exec -- <command>` when you also need the project's `[env]`, and
  `mise run <task>` for its tasks.
- For a runtime or CLI tool the project doesn't pin, use
  `mise use -g <tool>@<version>` rather than `apt-get`. Without `-g`, mise
  writes the tool into the project's `mise.toml`; only do that if the user
  asks. `mise registry` lists the tools mise can install.
- Use `apt-get install` for the rest, such as system libraries. Install
  Python and Node packages with `pip install --user` or `npm --prefix` under
  the home directory if they should survive.

## Committing

Git may have no name or email set. Omnigent only sets them when the user has
connected GitHub. If `git config user.email` is empty before you commit, set
them in that repository and commit:

```bash
git config user.name "Levi Vandenbempt"
git config user.email "levi.vandenbempt.sjcm@gmail.com"
```

## Linking files

When you mention a file the user may want to open, such as an image,
screenshot or report, link it with a Markdown link to its absolute path:

```markdown
[screenshot.png](/home/omnigent/workspace/app/screenshot.png)
```

Don't use relative paths or bare paths. The user opens these links from the
browser.

## Connection details

The Pod is usually on the user's tailnet. When the user asks how to reach
something running here, such as a dev server, a database or this Pod, give
the tailnet address, not `localhost`.

- Get the Pod's tailnet name with `tailscale status --peers=false`. The third
  column is the full name, e.g. `omnigent-cd44cbc5.taild5bc1b.ts.net`.
- Every port a process in the Pod listens on is reachable, whether it listens
  on `localhost` or on all addresses, e.g.
  `http://omnigent-cd44cbc5.taild5bc1b.ts.net:3000`.
- The user can SSH in with `ssh root@<full name>`.

If `tailscale status` fails, the Pod isn't on the tailnet. Tell the user,
and point them at `/run/omnigent-tailscale.log`, instead of making up an
address.
