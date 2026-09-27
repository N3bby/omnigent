# Omnigent deployment

Deploy your own [Omnigent](https://github.com/omnigent-ai/omnigent) server on a
single Ubuntu VM. One command provisions a fresh VM and later reconciles any
change you commit.

What you get:

- k3s with Traefik and cert-manager (automatic HTTPS)
- one Omnigent server, PostgreSQL, and a small in-cluster Vault
- agent sessions in short-lived Kubernetes runner Pods
- a few features on top of upstream Omnigent, such as showing your Claude and
  Codex usage limits in the Omnigent UI, and Podman inside runners. See
  [custom features](docs/CUSTOM_FEATURES.md).

Images are built by GitHub Actions, published to your GHCR namespace, and
deployed by digest.

![How the deployment fits together](docs/architecture.svg)

## Prerequisites

- An x86_64 Ubuntu 22.04+ VM that you can reach over SSH as a user with sudo.
  2 vCPU, 4 GiB RAM and 40 GB disk is enough for a session or two at a time.
  Each extra concurrent session needs about 0.5 vCPU and 1 GiB more.
- A DNS name pointing at the VM.
- On your machine: [mise](https://mise.jdx.dev), Docker with Buildx, `jq`, and
  OpenSSH. Mise installs the pinned Python and Ansible versions.

## Set up your own deployment

1. **Fork this repository** and keep it public, so the VM can pull its GHCR
   images without registry credentials.

2. **Edit the configuration** for your setup:

   - `ansible/inventory/production/hosts.yml`: SSH host and user of your VM.
   - `ansible/inventory/production/group_vars/all.yml`: your VM's size (the
     deploy refuses smaller hosts), and any private CIDR allowed to reach the
     Kubernetes API.
   - `environments/production.toml`: hostname, ACME and admin email,
     `image_registry` (`ghcr.io/<your-github-user>`), resource limits and
     runner concurrency.

3. **Install the tools:**

   ```bash
   mise install
   ```

4. **Publish the images.** Run the `Publish immutable images` workflow from the
   Actions tab of your fork. Make both new GHCR packages public, then pin their
   digests:

   ```bash
   mise run lock-images
   git commit -am "Lock image digests"
   ```

5. **Deploy:**

   ```bash
   mise run check
   mise run diff
   mise run deploy
   ```

   The deploy is idempotent. It installs k3s and cert-manager, creates
   internal secrets, applies the manifests and checks the public HTTPS
   endpoint. Use a clean VM: it refuses to take over an existing `omnigent`
   namespace.

6. **Sign in.** Open your HTTPS URL and claim the admin account with the admin
   email you configured.

7. **Connect at least one agent:**

   ```bash
   mise run setup-codex        # Codex device login
   mise run setup-claude       # Claude subscription token
   ```

8. **Give runners access to your repositories.** Pick one of these:

   ```bash
   mise run setup-github-app   # GitHub App (recommended)
   mise run setup-git-token    # a single HTTPS Git token
   ```

   The GitHub App adds a repository picker and gives each user their own
   access. It reaches whichever repositories the App is installed on,
   including private ones. `setup-github-app` prints the App settings to use,
   then asks for its Client ID, secret and slug. Afterwards, connect GitHub
   under Settings -> Sandbox Integrations.

   A Git token is simpler to set up, but every session shares the same token
   and there's no repository picker.

Runners that already exist keep their old credentials, so start a new session
after rotating one.

## Making changes

Change files in Git, never on the server or in the cluster, then run:

```bash
mise run check
mise run diff
mise run deploy
mise run status              # health, TLS, storage, failed runners
mise run credential-status   # which credentials are present
```

- **Upgrade versions** in `versions.yaml`.
- **Change images** (`images/`): bump `image_release`, run the publish
  workflow again, then `mise run lock-images`, commit and deploy.
- **Roll back** by reverting the commit and deploying again.

Production is never deployed automatically. You can also run these steps
from GitHub Actions instead of your machine; see below.

## Deploying from GitHub Actions

> **Not active yet.** The workflow is staged at `ci/deploy.yml`. Enable it with
> `git mv ci/deploy.yml .github/workflows/deploy.yml`, then commit and push
> from an account that is allowed to change workflows.

The `Deploy` workflow runs `mise run check` followed by `diff`, `deploy`,
`status` or `smoke` against production. It only runs when started by hand from
the Actions tab, only from `main`, and only in the `production` Environment.

1. **Create an SSH key for CI** and add its public half to the SSH user's
   `~/.ssh/authorized_keys` on the VM.

2. **Create the `production` Environment** under Settings -> Environments.
   Add yourself as a required reviewer, and limit deployment branches to
   `main`.

3. **Add these Environment secrets:**

   | Secret | Value |
   | --- | --- |
   | `DEPLOY_SSH_HOST` | The VM's real hostname or IP. `ansible_host` in `hosts.yml` may be an alias from your own SSH config. |
   | `DEPLOY_SSH_PRIVATE_KEY` | The CI private key. |
   | `DEPLOY_SSH_KNOWN_HOSTS` | Output of `ssh-keyscan <DEPLOY_SSH_HOST>`. Check it against the VM's host key. |
   | `DEPLOY_BECOME_PASSWORD` | The SSH user's sudo password. Leave it out if that user has passwordless sudo. |

4. **Make SSH reachable.** GitHub-hosted runners connect from public IPs that
   change. If SSH on your VM is only reachable over [Tailscale](https://tailscale.com),
   also add `TS_OAUTH_CLIENT_ID` and `TS_OAUTH_SECRET`, from a Tailscale OAuth
   client that can create `tag:ci` auth keys. The workflow then joins your
   tailnet before connecting.

Anyone who can get a run approved, or who compromises an action used in the
workflow, has root on the VM. Only give write access to people you would
give sudo, and keep third-party actions pinned by commit SHA.

The credential setup tasks (`setup-codex`, `setup-claude`, `setup-git-token`,
`setup-github-app`) are interactive, so they still run from your machine.

## Things to know

- **No backups.** If the VM or its disk is lost, so is the data. Recovery
  means a fresh VM, a new deploy and signing in again.
- **Runners are trusted.** They have unrestricted network access and share
  the operator's agent logins. Agents are root inside their own Pod so they can
  install packages, but a user namespace maps that root to an unprivileged
  user on the host. That suits a single operator or a trusted team. Read the
  [threat model](docs/THREAT_MODEL.md) before inviting others.
- **Agents skip permission prompts** by default. Turn off
  `claude_bypass_permissions` and `codex_bypass_approvals` in
  `environments/production.toml` to change that.
