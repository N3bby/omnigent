# Runtimes from mise

Sessions have [mise](https://mise.jdx.dev). Agents can install the Node,
Python, Go, Java or other versions a project pins in `.mise.toml`,
`mise.toml` or `.tool-versions`.

```bash
cd ~/workspace/my-project
mise install        # install what the project pins
node --version      # the pinned version, in this directory
mise run test       # run the project's mise tasks
```

## What agents do

Agents are [told](runner-instructions.md) to:

- run `mise install` when they find one of those files
- use `mise use -g node@22`, rather than `apt-get`, for tools a project
  doesn't pin. This doesn't touch the project's files.

## Good to know

- **Changing a version is enough.** Once a tool is installed, change its
  version in the file and the next `node` installs the new one.
- **Outside a project**, `node` and `python` are the image's own.
- **Runtimes are kept** in the home directory, so they're still there after
  an [idle session](idle-sessions.md) wakes up. They do use the home
  directory's disk.
- **Shims only switch tool versions.** For a project's `[env]` variables, run
  commands through `mise exec --` or `mise run`.
- **mise never asks whether to trust a project.** It trusts every config file
  in the home directory. See the
  [threat model](../threat-model.md#trade-offs-worth-knowing).
- **Some old Python patch releases fail** with "No GitHub artifact
  attestations found". mise refuses those builds because they weren't
  signed. Pin a newer patch release.

## Turning it off

You can't.

## How it works

- **Runner image:** installs the pinned `mise` binary in `/usr/local/bin`.
  The version is `mise` and `mise_sha256` in `versions.yaml`.
- **PATH:** `images/runner/mise/mise-profile.sh` is installed as
  `/etc/profile.d/zz-omnigent-mise.sh`. It puts mise's shims first on `PATH`
  for every login shell, including the one the runner starts from and
  Tailscale SSH.
  - It sorts after the base image's `omnigent-venv.sh`, so a project's Python
    comes before Omnigent's venv.
  - Omnigent itself calls its venv's Python by full path, and the `codex`
    wrapper runs Codex with the image's Node. A project's pins don't affect
    them.
  - So does git's GitHub credential helper, which Omnigent writes to
    `~/.gitconfig` when you've connected GitHub. Upstream writes it as bare
    `python3`, so in a project that pins Python, git pushes and fetches failed.
    `images/runner/patches/0002-git-helper-python.patch` writes the venv's
    Python by full path instead.
- **Home directory:** the script sets `MISE_DATA_DIR`, `MISE_CONFIG_DIR` and
  `MISE_STATE_DIR` under `/home/omnigent`. Agents have
  `HOME=/home/omnigent`, but root's home in `/etc/passwd`, which Tailscale
  SSH uses, is `/root`, which isn't kept.
- **No prompts:** `MISE_YES=1` and
  `MISE_TRUSTED_CONFIG_PATHS=/home/omnigent`.
- **Agent instructions:** the mise advice is part of
  `images/runner/agent-instructions.md`. See
  [agents know where they're running](runner-instructions.md).
