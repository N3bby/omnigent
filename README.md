# Omnigent deployment

Deploy your own [Omnigent](https://github.com/omnigent-ai/omnigent) server on a
single Ubuntu VM. One command provisions a fresh VM and later reconciles any
change you commit.

What you get:

- k3s with Traefik and cert-manager (automatic HTTPS)
- one Omnigent server, PostgreSQL, and a small in-cluster Vault
- agent sessions in short-lived Kubernetes runner Pods
- a few features on top of upstream Omnigent, such as usage limits in the
  composer and Podman inside runners. See
  [custom features](docs/CUSTOM_FEATURES.md).

Images are built by GitHub Actions, published to your GHCR namespace, and
deployed by digest.

![Architecture overview](docs/architecture-simple.svg)

## Prerequisites

- An Ubuntu 22.04+ VM with at least 4 vCPU, 16 GiB RAM and 60 GB disk,
  reachable over SSH by a user with sudo.
- A DNS name pointing at the VM.
- On your machine: [mise](https://mise.jdx.dev), Docker with Buildx, `jq`, and
  OpenSSH. Mise installs the pinned Python and Ansible versions.

## Set up your own deployment

1. **Fork this repository** and keep it public, so the VM can pull its GHCR
   images without registry credentials.

2. **Edit the configuration** for your setup:

   - `ansible/inventory/production/hosts.yml`: SSH host and user of your VM.
   - `ansible/inventory/production/group_vars/all.yml`: VM size, and any
     private CIDR allowed to reach the Kubernetes API.
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

7. **Connect agent accounts** (all optional except at least one harness):

   ```bash
   mise run setup-codex        # Codex device login
   mise run setup-claude       # Claude subscription token
   mise run setup-git-token    # HTTPS token for private repositories
   mise run setup-github-app   # GitHub repository picker
   ```

   `setup-github-app` prints the GitHub App settings to use, then asks for its
   Client ID, secret and slug. Afterwards, connect GitHub under Settings ->
   Sandbox Integrations. Runners that already exist keep their old
   credentials, so start a new session after rotating one.

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

Production is never deployed automatically by CI.

## Things to know

- **No backups.** If the VM or its disk is lost, so is the data. Recovery
  means a fresh VM, a new deploy and signing in again.
- **Runners are trusted.** They have unrestricted network access, run as root
  inside a per-Pod user namespace, and share the operator's agent logins. That
  suits a single operator or a trusted team. Read the
  [threat model](docs/THREAT_MODEL.md) before inviting others.
- **Agents skip permission prompts** by default. Turn off
  `claude_bypass_permissions` and `codex_bypass_approvals` in
  `environments/production.toml` to change that.
- **One server replica**, because Omnigent keeps its runner registry in memory.
- **Outside monitoring.** The VM cannot report its own outage, so use an
  external HTTPS monitor or your provider's console.

The [detailed diagram](docs/architecture.svg) shows the complete topology.
Regenerate both diagrams with `mise run diagram` after architectural changes.
