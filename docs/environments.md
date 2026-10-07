# Running more than one deployment

One fork can manage several deployments, each on its own VM. This repository
has one, `production`; the steps below use `production-alternative` as an
example of adding another.

## How environments work

Each deployment has:

| What | Where |
| --- | --- |
| Settings | `environments/<name>.toml` |
| VM inventory | `ansible/inventory/<name>/` |
| Cluster CA (written by bootstrap) | `environments/<name>.kubernetes-ca.crt` |
| Admin kubeconfig (on your computer) | `~/.kube/omnigent-<name>.yaml` |

Every `mise` command picks the deployment from `ENV`. Without `ENV`, it uses
`production`:

```bash
mise run status                                # production
ENV=production-alternative mise run status     # production-alternative
```

## Add a deployment

These steps add one called `production-alternative`. Replace the name with
your own.

> **Is the new VM private?** If it's only reachable over Tailscale, first
> read [private deployment](private-deployment.md#add-a-new-private-deployment).
> It adds a DNS record and a token to these steps.

### 1. Create the environment file

Copy `environments/production.toml` to
`environments/production-alternative.toml`, and change:

- `hostname`: a DNS name that points at the new VM. Bootstrap checks this.
- `kubernetes_api_host`: the new VM's MagicDNS name
- `deploy_github_environment = "production-alternative"`, or remove it and
  `deploy_github_repository_id` if GitHub Actions shouldn't deploy it

The copied image digests are already locked. Later,
`ENV=production-alternative mise run lock-images` updates them.

### 2. Create the inventory

Copy `ansible/inventory/production` to
`ansible/inventory/production-alternative`. Then:

- in `hosts.yml`, set the VM's SSH host and user
- in `group_vars/all.yml`, set
  `omnigent_environment: production-alternative` and the VM's size

### 3. Bootstrap and commit

```bash
ENV=production-alternative mise run bootstrap
git add environments/production-alternative.toml \
  environments/production-alternative.kubernetes-ca.crt \
  ansible/inventory/production-alternative
git commit -m "Add the production-alternative deployment"
```

### 4. Deploy

```bash
ENV=production-alternative mise run deploy
```

### 5. Finish the setup

Follow [setup](setup.md) from
[step 7, sign in](setup.md#7-sign-in), adding `ENV=production-alternative`
to every `mise run` command.

To deploy it from GitHub Actions too, see
[deploying another environment](github-actions.md#deploying-another-environment).
