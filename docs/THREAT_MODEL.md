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

Short of a Linux kernel bug, it **can't**:

- take over the VM
- read the database or the server's secrets
- touch other namespaces or the Kubernetes API
- use more CPU, memory or disk than its limits allow

## The network is open

Runner pods have unrestricted network access in both directions. There is no
network policy, allowlist or filtering proxy. Treat anything a runner can
reach as reachable by an attacker.

## What keeps a runner contained

| Boundary | What it does |
| --- | --- |
| User namespace | The agent is root inside its pod, but that maps to an unprivileged user on the host. See [the diagram](CUSTOM_FEATURES.md#install-packages-and-run-containers). |
| Admission policy | Rejects runner pods that are privileged, mount host paths, share host namespaces or use host ports (`kubernetes/base/runner-userns.yaml`). |
| No Kubernetes access | The runner's service account has no permissions, and its token isn't mounted. |
| Resource limits | CPU, memory, disk, home size, and the number of pods and jobs are all capped. |
| Separate namespace | PostgreSQL and the server's secrets live in a different namespace. Runners only get the runner credentials. |

The server itself can only manage jobs, pods, logs and launch secrets in the
runner namespace, which limits what a compromised server could do there.

## Trade-offs worth knowing

- **Root has more kernel surface.** To make `apt-get` and Podman work, runner
  agents have all capabilities, no seccomp or AppArmor profile, and an
  unmasked `/proc`. Those powers only count inside the pod, but they expose
  more of the kernel than Kubernetes' default `restricted` profile. Isolation
  is ordinary containerd; stronger sandboxes like gVisor or Kata aren't used.
- **Containers share the pod's limits.** Podman containers run without their
  own cgroups, so only the pod's overall limits constrain them.
- **Agent logins are shared.** Every runner uses the same Claude token and
  Codex login, so one compromised runner can use them.
- **Agents skip permission prompts** by default (`claude_bypass_permissions`
  and `codex_bypass_approvals`). Turning these off makes the agents ask
  again, but since the agent is root in its pod it could change that setting
  itself. Treat it as a guard against mistakes, not against an attacker.
  Changes only apply to new runners.

## GitHub tokens and Vault

Each user's GitHub tokens are encrypted by Vault before they're stored in the
database. Vault and the key that unlocks it live in the same cluster, so
Vault restarts without anyone present.

That protects against a leak of the database on its own. It doesn't protect
against someone with root on the VM or admin access to the cluster, because
they can reach both the encrypted tokens and the key.

## Before inviting people you don't fully trust

Consider:

- giving each user their own agent logins instead of shared ones
- running runners on a separate node or VM from the server and database
- restricting runner network access
