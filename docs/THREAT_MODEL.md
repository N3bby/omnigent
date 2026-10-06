# Threat model

Runners run code you didn't write: the repository's code, its dependencies,
and whatever commands the agent decides to run. Any of that could be
malicious, so this setup assumes a runner might be compromised. This page
covers what a compromised runner could do, what stops it, and who this setup
is suitable for.

## The short version

This setup suits **one operator, or a small team who all trust each other**.

A compromised runner **can**:

- use the shared Claude and Codex logins, and the shared Git token if you set
  one up
- use the GitHub access of the person who started the session, for as long
  as its short-lived token is valid
- reach anything on the network: the internet, and other services in the
  cluster (which still need their own credentials)
- use all of the VM's spare CPU and memory, which slows down other sessions
- reach what your tailnet policy lets runners reach, if you
  [put runners on your tailnet](#runners-on-your-tailnet)

Short of a Linux kernel bug, it **can't**:

- take over the VM
- read the database or the server's secrets
- use the Kubernetes API, or touch other namespaces

## The network is open

Runner pods have unrestricted network access in both directions. There is no
network policy, allowlist or filtering proxy. Treat anything a runner can
reach as reachable by an attacker.

## What keeps a runner contained

| Boundary | What it does |
| --- | --- |
| User namespace | The agent is root inside its pod, but that maps to an unprivileged user on the host. See [the diagram](CUSTOM_FEATURES.md#install-packages-and-run-containers). |
| Admission policy | Rejects runner pods that are privileged, mount host paths, share host namespaces or use host ports (`kubernetes/platform/runner-userns.yaml`). |
| No Kubernetes access | The runner's service account has no permissions, and its token isn't mounted. |
| Resource limits | Scratch disk and the number of pods and jobs are capped. CPU and memory limits are above the VM's size, so runners share whatever is free, and the server, database and Vault have a higher priority than runners. The home volume is not capped; see below. |
| Separate namespace | PostgreSQL and the server's secrets live in a different namespace. Runners only get the runner credentials. |

The server itself can only manage sandboxes, jobs, pods, logs and launch
secrets in the runner namespace, which limits what a compromised server could
do there.

## Trade-offs worth knowing

- **Root has more kernel surface.** To make `apt-get` and Podman work, runner
  agents have all capabilities, no seccomp or AppArmor profile, and an
  unmasked `/proc`. Those powers only count inside the pod, but they expose
  more of the kernel than Kubernetes' default `restricted` profile. Isolation
  is ordinary containerd; stronger sandboxes like gVisor or Kata aren't used.
- **Containers share the pod's limits.** Podman containers run without their
  own cgroups, so only the pod's overall limits constrain them.
- **The home volume isn't capped.** Each session's home directory is a
  `local-path` volume on the VM's disk, and `local-path` doesn't enforce its
  requested size (`runner_home_limit`). A runner that fills its home fills the
  VM's disk.
- **Agent logins are shared.** Every runner uses the same Claude token and
  Codex login, so one compromised runner can use them.
- **Agents skip permission prompts** by default (`claude_bypass_permissions`
  and `codex_bypass_approvals`). Turning these off makes the agents ask
  again, but since the agent is root in its pod it could change that setting
  itself. Treat it as a guard against mistakes, not against an attacker.
  Changes only apply to new runners.

## Runners on your tailnet

[Tailscale for runners](CUSTOM_FEATURES.md#reach-a-session-over-tailscale) is
off until you store a key. Once it's on, every runner Pod is a node on your
tailnet, and the agent is root in it, so treat the node as untrusted:

- **What it can reach is up to your tailnet policy.** Grant your devices
  access to `tag:omnigent-runner`, and grant `tag:omnigent-runner` nothing.
  With Tailscale's default allow-all policy, a compromised runner could
  connect to every device on your tailnet, including your own machine and
  the VM's Kubernetes API (port 6443), which the firewall only allows from
  private addresses.
- **It can read the Tailscale key.** The key is in every runner's
  environment. With an OAuth client, a runner can create more ephemeral
  nodes, but only with the tags you gave the client. Give the client a tag
  of its own, and rotate it with `mise run setup-tailscale` if you suspect a
  leak.
- **Devices your policy allows can get a root shell in the Pod** through
  Tailscale SSH, and reach any port the agent opens. That's the same access
  the agent already has.

## GitHub tokens and Vault

Each user's GitHub tokens are encrypted by Vault before they're stored in the
database. Vault and the key that unlocks it live in the same cluster, so
Vault restarts without anyone present.

That protects against a leak of the database on its own. It doesn't protect
against someone with root on the VM or admin access to the cluster, because
they can reach both the encrypted tokens and the key.

## The Cloudflare DNS token

With `acme_challenge = "cloudflare-dns-01"`, cert-manager holds a Cloudflare
API token in the `cert-manager` namespace, so it can prove control of the
hostname through DNS. Runners and the GitHub Actions deploy identity can't
read that namespace. Root on the VM or cluster admin can.

Whoever gets the token can change every DNS record in the zones it covers.
They could point your hostnames, or any other name in the domain, at their own
servers, and get valid certificates for them. Limit the token to the one zone
and to the DNS edit and zone read permissions, and rotate it with
`mise run setup-cloudflare-token` if it might have leaked.

## Deploying from GitHub Actions

Deploys come in two levels of access:

- **`mise run bootstrap`** needs SSH and sudo on the VM, and runs from your
  machine. It owns everything that could weaken the boundaries above: the
  firewall, k3s, cert-manager, the agent-sandbox controller, the namespaces
  and their Pod Security labels, the admission policies, RBAC and quotas.
- **`mise run deploy`** needs only the Kubernetes API. From GitHub Actions it
  authenticates with a short-lived GitHub OIDC token. The API server accepts
  the token only from this repository's `production` Environment on `main`,
  and the token can only manage the application in the two Omnigent
  namespaces (`kubernetes/platform/deployer-rbac.yaml`).
- **The `Deploy` workflow** runs that deploy after building the images. Its
  job can also push images to GHCR and commit to `main`, because it records
  the new digests there.

So a compromised deploy run, or a compromised action inside it, can't change
the firewall, k3s, admission policies or namespace labels, and can't start
Pods outside those two namespaces. It can still run Pods in them, so it gets
every Secret there: the database, Vault and its key, the GitHub App secret,
and the agent logins. It can also publish its own images, point production at
them, and push commits to `main`, including changes to later deploys. Treat
approving a deploy run as handing out all of that.

The kubeconfig `mise run bootstrap` saves on your machine is cluster-admin,
which is equivalent to root on the VM.

## Before inviting people you don't fully trust

Consider:

- giving each user their own agent logins instead of shared ones
- running runners on a separate node or VM from the server and database
- restricting runner network access
