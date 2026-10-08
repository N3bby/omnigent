# Repository variables

Give a repository its own environment variables, such as the token for a
private npm registry. Sessions started on that repository get them in their
agent's environment. Sessions on other repositories don't.

## Set it up

In Omnigent, go to **Settings → Repository variables** (admins only) and
choose **Add repository**:

1. Pick one of your GitHub repositories, or paste any clone URL. The page
   shows the repository it will match, such as
   `github.com/kunlabora/roadpass`.
2. Add the variables one by one, or **Paste .env** to add `NAME=value`
   lines at once.
3. **Save.**

Values are write-only. The page shows the names, never the values. To change
a value, **Edit** the repository and type a new one; a value left blank
keeps what it was. **Remove** deletes all of the repository's variables.

### From the command line

The same variables can be set from your machine. The page and the command
store them in the same place, so you can use either.

```bash
mise run setup-repo-env kunlabora/roadpass ../roadpass-env/roadpass.env
```

Or leave out the file and paste `NAME=value` lines. What you paste isn't
shown, and Ctrl-D ends it.

```bash
mise run setup-repo-env kunlabora/roadpass
```

- **The repository** can be `owner/repo` on github.com, or any clone URL.
  `git@github.com:Kunlabora/RoadPass.git` and
  `https://github.com/kunlabora/roadpass` are the same repository.
- **The file** has one `NAME=value` per line, like a `.env` file. Comments,
  blank lines, `export` and quotes around a value are fine. Nothing is
  expanded: `$HOME` stays `$HOME`.
- **Running it again replaces** all the repository's variables with the new
  ones, including any added on the page.
- **To remove them:** `mise run setup-repo-env --delete kunlabora/roadpass`.
- **To see which repositories have variables:** `mise run credential-status`.
  It lists names, never values.

## Using them

Start a session on the repository with the repository picker. Once picked,
the repository shows **2 variables** (or however many it has); click it to see
their names. The agent has the variables in every command it runs, and so does anything those commands
start, like `npm install`, `mise run dev` or `mise exec`. Tailscale SSH
logins into the session get them too.

For an npm registry, keep the token out of `.npmrc` and refer to the
variable instead:

```ini
@govflanders:registry=https://artifactory.example/api/npm/npm/
//artifactory.example/api/npm/npm/:_authToken=${GOVFLANDERS_NPM_TOKEN}
```

A project's `mise.toml` that loads a local file with
`_.file = "../roadpass-env/roadpass.env"` needs no change. In a session the
file doesn't exist, and mise skips it, so the session's variables stay.

## Good to know

- **Only the repositories a session starts with count.** A repository the
  agent clones later gets nothing.
- **Changes reach new sessions.** A running session gets them after it has
  been [idle](idle-sessions.md) and wakes up. A process already running, such
  as a dev server, keeps the old value until it restarts.
- **Containers don't inherit them.** A `docker compose` service or
  `docker build` only gets the variables its compose file or build arguments
  pass in.
- **A few names are reserved**, because the runner relies on them: `PATH`,
  `HOME` and the like, and names starting with `OMNIGENT_`, `CLAUDE_`,
  `CODEX_`, `MISE_` or `TAILSCALE_`.
- **Anyone who starts a session on the repository gets the variables.**
  They belong to the repository, not to a user. Anyone signed in can see
  their names in the new-session form. See the
  [threat model](../threat-model.md).
- **Only admins manage them** on the page. Admins are the users with admin
  rights in Omnigent, the same who see Members and Policies.

## Turning it off

There's nothing to turn off. A repository without variables starts as before.

## How it works

- **Secrets:** each repository's variables are one Secret in the
  `omnigent-sandboxes` namespace, one key per variable, labelled
  `omnigent.dev/repo-env`. Its name comes from the repository, such as
  `omnigent-repo-env-kunlabora-roadpass-66ad182d`. The annotation
  `omnigent.dev/repository` holds the repository, such as
  `github.com/kunlabora/roadpass`.
- **The Settings page** (`images/server/web-patches/0005-repo-variables-page.patch`)
  calls `/v1/repo-env`, which `images/server/omnigent_repo_env_api.py`
  serves (mounted by `images/server/patches/0006-repo-env-routes.patch`).
  Listing returns names; saving sends each name with a new value, or `null`
  to keep the current one, and replaces the Secret only if nobody changed it
  since it was read. Every route but the names lookup is admin-only.
- **Permissions:** for the page, the server may get, list and update Secrets
  in `omnigent-sandboxes` (`kubernetes/platform/runner-rbac.yaml`). That's a
  platform change, so it needs `mise run bootstrap` once.
- **The name:** `images/server/omnigent_repo_env.py` turns any way of
  writing the repository into `host/owner/repo` in lower case. The name is a
  readable part plus a hash of that, so `my_repo` and `my.repo` don't share
  one. `scripts/setup-repo-env`, the Pod and the page use this same file,
  and the page repeats its rules in TypeScript to preview the match.
- **The Pod** (`images/server/patches/0004-repo-env.patch`): when the
  server creates a runner Pod, it mounts the Secret for each of the session's
  repositories at `/run/omnigent/repo-env/<Secret name>/`, on the host
  container only. The volume is optional, so a repository without a Secret
  mounts nothing. A wake re-creates the Pod with the same repositories.
- **The agent** (`images/runner/repo-env/repo-env.sh`): the `claude` and
  `codex` wrappers source it before starting the real CLI, and it exports each
  mounted file as a variable. It's also `/etc/profile.d/omnigent-repo-env.sh`,
  for login shells.
- **Why not one shared Secret:** Omnigent passes only a fixed list of
  variables from the runner Pod's environment to the agent. Variables loaded
  by the agent wrappers don't go through that list.
