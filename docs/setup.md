# Set up your own deployment

This guide takes you from an empty VM to a working Omnigent server.

## What you need

**A VM**

- x86_64 Ubuntu 22.04 or newer, which you can reach over SSH as a user with
  sudo.
- 2 vCPU, 4 GiB RAM and 40 GB disk is enough for one or two sessions at a
  time. Each extra session running at the same time needs about 0.5 vCPU and
  1 GiB RAM more.
- Start from a clean VM. Setup refuses to take over an existing `omnigent`
  namespace.

**A DNS name for the VM**

- By default the VM must be reachable from the internet on port 80, so
  Let's Encrypt can issue its certificate.
- If you only want to reach the VM over Tailscale, see
  [private deployment](private-deployment.md).

**A private way to reach the VM's Kubernetes API**

- The firewall blocks the Kubernetes API (port 6443) on the public network.
- The easiest option is [Tailscale](https://tailscale.com) on the VM and on
  your computer. GitHub Actions connects the same way.

**On your computer**

- [mise](https://mise.jdx.dev). It installs the pinned Python, Ansible and
  kubectl.
- Docker with Buildx, `jq` and OpenSSH.

## Steps

### 1. Fork this repository

Keep your fork **public**. The VM then pulls the images from GHCR without
needing registry credentials.

### 2. Edit the configuration

| File | What to set |
| --- | --- |
| `ansible/inventory/production/hosts.yml` | Your VM's SSH host and user. |
| `ansible/inventory/production/group_vars/all.yml` | Your VM's size (setup refuses smaller VMs), and any private address range that may reach the Kubernetes API. |
| `environments/production.toml` | Everything else; see below. |

In `environments/production.toml`, set:

- `hostname`: your DNS name.
- `acme_email` and `admin_email`.
- `acme_challenge`: keep `http-01` unless the VM is
  [private](private-deployment.md).
- `image_registry`: `ghcr.io/<your-github-user>`.
- `kubernetes_api_host`: the VM's private name, such as its Tailscale
  MagicDNS name.
- `deploy_github_repository_id`: your fork's numeric ID, from
  `gh api repos/OWNER/REPO --jq .id`. Remove it if you won't
  [deploy from GitHub Actions](github-actions.md).
- Resource limits and how many sessions can run at once, if the defaults
  don't suit you.

### 3. Install the tools

```bash
mise install
```

### 4. Publish the images

1. In your fork's **Actions** tab, run the **Publish immutable images**
   workflow.
2. In GHCR, make both new packages **public**.
3. Check out the commit the workflow built, and pin the image digests:

   ```bash
   mise run lock-images
   git commit -am "Lock image digests"
   ```

### 5. Bootstrap the VM

This is the only step that needs SSH and sudo. It asks for your sudo
password.

```bash
mise run bootstrap
git add environments/production.kubernetes-ca.crt
git commit -m "Record the cluster CA"
```

Bootstrap:

- installs k3s, cert-manager and the agent-sandbox controller
- sets up the platform: namespaces, security policies for sessions, quotas
  and access rules
- saves an admin kubeconfig to `~/.kube/omnigent-production.yaml`
- records the cluster's CA certificate, which GitHub Actions needs

### 6. Deploy the application

This step only needs the Kubernetes API, not SSH.

```bash
mise run diff      # see what will change
mise run deploy
```

You can run the deploy as often as you like. It creates internal secrets,
applies the manifests, waits for the rollout and checks the HTTPS endpoint.

### 7. Sign in

Open your HTTPS URL. Claim the admin account with the `admin_email` you
configured.

### 8. Connect at least one agent

```bash
mise run setup-codex     # Codex: log in with a device code
mise run setup-claude    # Claude: store a subscription token
```

### 9. Give sessions access to your repositories

Pick one:

| Option | Command | Good for |
| --- | --- | --- |
| **GitHub App** (recommended) | `mise run setup-github-app` | Each user gets their own access and a repository picker. See [GitHub repository picker](features/github-repo-picker.md). |
| **One Git token** | `mise run setup-git-token` | Quick to set up, but every session shares the token, and there's no picker. |

## After setup

- Sessions that already exist keep their old credentials. Start a new session
  after you change one.
- For day-to-day work, see [making changes](operations.md).
- Optional extras:
  - [Deploy from GitHub Actions](github-actions.md)
  - [Reach sessions over Tailscale](features/tailscale.md)
  - [Open sessions in JetBrains Gateway](features/jetbrains-gateway.md)
