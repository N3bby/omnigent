# Omnigent deployment

This repository provisions a fresh single-node Omnigent installation on an
existing Ubuntu VM and reconciles later changes through the same command. It is
greenfield: it does not read or migrate the old installation.

The production topology is k3s with Traefik, cert-manager, one Omnigent server,
PostgreSQL, a local Vault credential-encryption service, and ephemeral
Kubernetes runner Jobs. The runners deliberately have
unrestricted ingress and egress. Images are built in GitHub Actions, published
to public GHCR and deployed by digest. The server image only adds the pinned
Vault client library to the pinned upstream image. Production deployment is
manual.

## One-time prerequisites

1. Create an Ubuntu 22.04+ netcup VM. The checked-in baseline expects at least
   4 vCPU, 16 GiB RAM, and 60 GB disk for the checked-in two-runner baseline.
2. Add the operator's SSH key for `n3bby`, and ensure `n3bby` has sudo access.
3. Point the intended DNS name at the VM.
4. Create a public Git repository and push this directory. Public repositories
   can expose their linked GHCR packages without server-side registry secrets.
5. Install mise, Docker with Buildx, `jq`, and OpenSSH on the operator machine.
   `kubectl` or `kustomize` is optional; rendering falls back to the pinned
   kubectl container. Mise installs the pinned Python and Ansible versions.

Edit these tracked files:

- `ansible/inventory/production/hosts.yml`: SSH host/user.
- `ansible/inventory/production/group_vars/all.yml`: actual VM sizing and any
  private CIDR that may reach the Kubernetes API.
- `environments/production.toml`: hostname, emails, GHCR namespace, resource
  limits, runner concurrency, and explicit policy settings.

The deployment firewall only protects the Kubernetes API: public TCP/6443 is
blocked unless its source is in `k3s_api_private_cidrs`. It does not filter
other service ports or outbound traffic. Provider-level netcup firewall rules
and the DNS record remain manual prerequisites.

Install the pinned operator tools with:

```bash
mise install
```

## Publish and lock the images

Run the manually triggered `Publish immutable images` workflow once. It builds
the amd64 runner and server images, attaches provenance/SBOM attestations, and
publishes the release tag from `versions.yaml`. Make both packages public, then run:

```bash
mise run lock-images
git add environments/production.toml
git commit -m "Lock production image digests"
```

## First deployment

```bash
mise run check
mise run diff
mise run deploy
```

The deploy is idempotent. It validates the VM and DNS, installs the pinned k3s
and cert-manager releases with checked installer/manifest hashes, protects the
public k3s API port, creates internal random secrets only when absent, applies and
prunes the tracked desired state, waits for rollouts, and tests the public HTTPS
health endpoint. It never copies secrets back to the operator machine.
It also refuses to adopt an existing unmanaged `omnigent` namespace; use a
clean VM/cluster so the old installation remains untouched.

Open the configured HTTPS URL and claim the initial administrator account with
the configured admin email. Then authenticate the shared agent identities:

```bash
mise run setup-codex
mise run setup-claude       # optional
mise run setup-git-token    # optional private HTTPS repositories
mise run setup-github-app   # GitHub repository picker
```

Codex uses a device flow and stores its shared auth on the `codex-home` PVC.
Claude and the optional Git token are entered with hidden prompts and stored in
the runner namespace Secret. Existing runner Jobs retain their original
environment; create a new runner after rotating a credential.

`setup-github-app` first prints the exact GitHub App settings, then prompts for
the Client ID, Client secret, and App slug. It initializes the in-cluster Vault
and stores the encryption material only in Kubernetes Secrets. It does not need
a GitHub App private key or an external key store. After it finishes, connect
GitHub once from Omnigent under Settings -> Sandbox Integrations.

## Applying changes

Every change uses the same path:

```bash
mise run check
mise run diff
mise run deploy
mise run status
mise run credential-status
```

Do not edit the server or live Kubernetes objects. The renderer hashes
ConfigMaps, Secret content produces an opaque Pod-template checksum, and images
are digest-pinned, so relevant changes always trigger a rollout. Apply uses a
dedicated label-scoped object inventory and prunes removed resources.

Version changes belong only in `versions.yaml`. Image-input changes require a
new `image_release`, a workflow run, and `mise run lock-images`. Production is never
deployed automatically by CI.

## Deliberate boundaries

- No backups or restore workflow are provided. Loss of the VM/disk means a
  clean deployment and fresh authentication; application data is lost.
- GitHub App connections use the in-cluster Vault Transit engine. The Vault
  unseal material is stored in the same k3s cluster for unattended restarts;
  this protects database-only disclosure, not a root or cluster compromise.
- Runners have unrestricted networking. Security relies on namespace/RBAC,
  Pod Security, credentials, and resource boundaries—not network filtering.
- Codex's dangerous approval/sandbox bypass is explicit as
  `codex_bypass_approvals`; it is currently enabled per the accepted policy.
- Ordinary containerd isolation is used; gVisor/Kata are not installed.
- One server replica is used because Omnigent's runner registry is in memory.

See [the threat model](docs/THREAT_MODEL.md),
[operations contract](docs/OPERATIONS.md), and [implementation plan](plan.md).
The complete deployed topology is available as a
[D2 architecture diagram](docs/architecture.svg); regenerate it with
`mise run diagram` after architectural changes.
