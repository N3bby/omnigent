# Greenfield Omnigent deployment and automation plan

## Implementation status

The greenfield repository described here is implemented in this directory. The
checked-in production profile deliberately keeps the optional GitHub repository
picker disabled, so Vault is not installed and the upstream server image can be
used unchanged by digest. Representative load/capacity testing remains an
after-deployment task because it requires the actual netcup VM and authenticated
sessions.

## Executive assessment

The current setup is useful only as a record of past choices. It will not be
migrated, modified, or treated as a compatibility target. The replacement is
built greenfield in a sibling `omnigent-deployment` directory as its own Git
repository.

The target should support both:

1. Provisioning a fresh Ubuntu server with minimal manual involvement.
2. Repeatedly applying repository changes to an existing server through one
   idempotent, reviewable deployment command.

The audit below is based on the tracked repository and ignored generated files
in this checkout. It exists to retain useful lessons and avoid repeating the
same design problems; no live-cluster inventory or state migration is required.

## Highest-priority findings

1. **Tracked and generated desired state disagree.** The tracked overlay enables
   Vault and the local Vault-capable server image, while the currently generated
   overlay does neither. A fresh preparation would produce a different
   deployment than the one presently prepared.
2. **Image updates are not reliably rolled out.** `03-deploy.sh` rebuilds fixed
   local image tags and imports them into containerd, but an unchanged
   Deployment spec does not trigger a rollout. ConfigMap and Secret changes also
   lack checksum-triggered restarts.
3. **Important state exists only in the cluster.** GitHub configuration, Vault
   tokens, Claude credentials, and `GIT_TOKEN` are introduced through imperative
   `kubectl patch` or `kubectl set image` operations.
4. **Fresh-server setup is incomplete.** Docker is required by deployment but is
   not installed by the bootstrap script. Server creation, DNS, cloud firewall,
   and SSH setup are also manual.
5. **Durability beyond the current node is intentionally not required.** The
   existing backup script is incomplete and creates false confidence, so it
   should be removed rather than expanded. Loss of the server or its disk means
   losing PostgreSQL data, artifacts, OAuth state, and Vault state, followed by
   a clean redeployment and reauthentication.
6. **Runner credentials have a broad blast radius.** Runners share Claude and
   Git credentials and mount the same Codex OAuth PVC read-write.
7. **Codex bypasses its approval and sandbox checks.** The custom wrapper passes
   `--dangerously-bypass-approvals-and-sandbox`. Kubernetes reduces the host
   risk, but runners use the default container runtime. Unrestricted runner
   ingress and egress is an intentional requirement and must be preserved, so
   isolation needs to come from runtime, identity, and credential boundaries
   rather than network destination filtering.

## Current setup decision register (reference only)

This register lists every material deployment choice found in the old
repository, including inherited defaults and important omissions. It is not a
set of migration requirements for the new deployment.

### Platform and topology

- One Ubuntu server with `apt`, systemd, Bash, GNU utilities, and `sudo`.
- One single-node k3s cluster with no high availability.
- k3s provides Traefik, local-path storage, containerd, and its other defaults.
- Omnigent, PostgreSQL, Vault, and all runners share one physical node.
- The `omnigent` namespace contains the server, database, and Vault.
- The `omnigent-sandboxes` namespace contains runner Jobs.
- There is one production environment and no staging/test overlay.
- Resource names and namespaces are fixed, making multiple instances difficult.
- Persistent data uses node-local storage and is tied to the server disk.
- VPS creation, DNS, provider firewalling, and SSH setup are out of band.

### Installation

- k3s is installed through unpinned `curl | sh` from `get.k3s.io`.
- No k3s version or installer checksum is recorded.
- cert-manager is pinned to `v1.21.1`, but its remote manifest has no checksum.
- Apt packages use whatever versions the Ubuntu repositories currently serve.
- Docker is required later but is not installed or configured by the setup.
- The k3s kubeconfig is copied into the invoking user's home.
- The user's `.profile` is modified to set `KUBECONFIG`.
- Host firewall automation only drops TCP/6443 on the interface found through
  the route to `1.1.1.1`.
- Other ports and cloud-provider firewall rules are intentionally untouched.
- IPv4/IPv6 exposure is not comprehensively validated.
- Existing k3s and cert-manager installations are detected, but their versions
  are not reconciled.

### Source and configuration generation

- Omnigent is pinned to tag `v0.15.0`.
- Its repository is shallow-cloned into ignored `work/omnigent`.
- Preparation forcibly discards modifications in that upstream checkout.
- The overlay relies on upstream files at known relative paths.
- Production manifests are generated inside the ignored upstream checkout.
- Hostname, ACME email, and administrator email are collected interactively.
- PostgreSQL and account-cookie secrets are generated during preparation.
- Plaintext secrets are written to an ignored YAML file on disk.
- Deployment metadata is written to an ignored shell-like file containing an
  absolute path.
- Templates use textual replacement rather than schema-aware YAML generation.
- Versions are duplicated across documentation, scripts, manifests, and images.
- Feature selection is implicit rather than captured in one environment file.

### Build and deployment

- Two custom images are built on the production server.
- The runner image is based on `omnigent-host:v0.15.0`.
- The server image is based on `omnigent-server-kubernetes:v0.15.0`.
- Claude Code is pinned to `2.1.281`.
- Codex CLI is pinned to `0.156.1`.
- `hvac` is pinned to `2.4.0`.
- Base images are referenced by mutable tags instead of immutable digests.
- npm and pip packages are installed without lockfiles or hash verification.
- Local images use fixed mutable tags.
- Images are imported directly into k3s containerd; no registry is used.
- Image building and deployment are coupled into the same operation.
- Deployment uses client-side `kubectl apply` without pruning removed objects.
- Configuration changes do not automatically restart affected workloads.
- Deployed resources do not record the repository revision or manifest bundle.
- There is no plan/diff step or automatic rollback after failed health checks.
- There is no repository CI for manifests, scripts, images, or upgrade paths.
- This checkout currently has no configured Git remote.
- Historic migration scripts are mixed with steady-state operations.

### Web access and authentication

- Public traffic enters through the k3s-provided Traefik ingress controller.
- HTTPS uses a cluster-wide `letsencrypt-prod` ClusterIssuer with HTTP-01.
- HTTP is permanently redirected to HTTPS.
- There is no Let's Encrypt staging issuer for safe testing.
- HSTS, explicit security headers, WAF, rate limiting, and IP restrictions are
  not configured.
- Traefik Basic Auth was removed.
- Omnigent's built-in `accounts` provider is enabled.
- Automatic account opening is disabled.
- The configured administrator email is also listed in sandbox configuration.
- The first browser visit claims the initial administrator account.
- Additional users join through invites.
- Account sessions depend on a generated cookie secret.
- The Omnigent server runs as one replica.

### Server, database, and storage

- The server listens on `0.0.0.0:8000` behind a ClusterIP service on port 80.
- Liveness and readiness probes use `/health`.
- Server requests are 250m CPU and 512Mi memory.
- Server limits are 1 CPU and 1Gi memory.
- The artifact PVC is 10Gi, ReadWriteOnce.
- The inherited Omnigent release feature list is empty.
- PostgreSQL uses `postgres:16-alpine` by tag.
- PostgreSQL has one StatefulSet replica.
- The database and database user are both named `omnigent`.
- PostgreSQL password traffic is unencrypted inside the cluster.
- PostgreSQL requests are 100m CPU and 256Mi memory.
- PostgreSQL limits are 500m CPU and 512Mi memory.
- PostgreSQL uses a 10Gi ReadWriteOnce PVC.
- No explicit storage class, tuning, metrics, or maintenance policy is defined.

### Runner architecture

- Managed sessions run as Kubernetes Jobs.
- An init container prepares the workspace and clones repositories.
- The host container runs the selected agent.
- Repository checkouts and the runner home are ephemeral.
- The inherited runner home limit is 8Gi.
- Default runner resources are 500m-2 CPU and 1-4Gi memory.
- Ephemeral-storage requests and limits are not configured.
- Runner placement defaults to `amd64`.
- Pod readiness timeout is overridden to 300 seconds.
- Jobs can run for up to seven days.
- Finished Jobs remain for 24 hours.
- Runners use a non-root UID/GID, RuntimeDefault seccomp, dropped capabilities,
  and no privilege escalation.
- Runner service-account tokens are not mounted.
- The runner ServiceAccount has no Kubernetes API rights.
- The server has namespaced rights to manage runner Jobs, Pods, token Secrets,
  logs, and events.
- The two-namespace RBAC separation is a strong choice that should be retained.
- Container root filesystems remain writable.
- Pod Security Admission namespace policy is not configured.
- ResourceQuota and LimitRange objects are not configured.
- NetworkPolicies are absent. Runners have unrestricted ingress and egress,
  including arbitrary internet and cluster destinations; this is an explicit
  functional requirement intended to avoid impeding agent work.
- No gVisor, Kata, or other sandboxed RuntimeClass is used.
- Upgrades delete all runner Jobs and interrupt active work.

### Agent and repository credentials

- Claude and Codex subscription providers are both enabled and marked default.
- OpenAI API-key authentication is intentionally not used.
- Codex uses ChatGPT device OAuth.
- A shared 1Gi `local-path` PVC stores Codex authentication.
- That PVC is mounted read-write into every runner.
- Private Codex homes receive a symlink to the shared `auth.json`.
- The Codex wrapper bypasses all approvals and Codex sandboxing.
- Claude setup is manual and stores a long-lived token in `omnigent-creds`.
- The optional Git HTTPS token is global and stored in the same Secret.
- The complete runner Secret is injected into clone and host containers.
- Helper Pods use fixed names, preventing concurrent helper operations.
- Codex login has cleanup trapping; Claude login does not clean up on failure.

### GitHub picker and Vault

- Private GitHub picker access uses per-user GitHub App connections.
- GitHub expiring user tokens are enabled and webhooks are disabled.
- Permissions requested are metadata read, contents read/write, and pull
  requests read/write.
- The necessity of all write permissions is not documented.
- GitHub App creation and installation remain manual.
- The picker is documented as optional, but the tracked overlay now always
  includes Vault and the Vault-capable server image.
- Vault is a single StatefulSet with a 1Gi PVC and file storage.
- Vault cluster traffic uses unencrypted HTTP.
- Vault initialization uses one unseal share and threshold one.
- The unseal key and root token remain in a Kubernetes Secret in the same
  namespace.
- The unseal key is injected into Vault for automatic unsealing.
- Omnigent receives an orphan service token with a ten-year TTL.
- Re-running setup creates another token without revoking the previous token.
- The application policy is limited to encrypt/decrypt on one Transit key.
- Vault has no resource limits, audit device, token rotation, or NetworkPolicy.
- The server requires a custom image solely to add the `hvac` dependency.

### Operations and accepted data-loss model

- Status reporting consists of raw Kubernetes resource listings.
- There is no end-to-end HTTP, authentication, or runner smoke test.
- Metrics, alerting, log retention, certificate expiry, disk usage, and Vault
  seal monitoring are absent.
- Backups currently exist as a manual, local script, but backups are not a
  requirement for the target system and this script should be removed.
- PostgreSQL receives a logical dump.
- Artifacts and Codex state receive tar archives.
- Vault data and bootstrap material are included only when Vault is detected.
- File-based service backups are taken while those services remain live.
- The current archives are not encrypted, uploaded, checksummed, retained, or
  restore-tested; they must not be treated as a supported recovery mechanism.
- No restore workflow will be added. Node or disk loss is an accepted loss of
  application data and authentication state.
- The full Omnigent Secret, runner credentials, GitHub client secret, Vault
  service token, and k3s state are not comprehensively backed up.
- Upgrade logic implements one historic version transition rather than a
  general workflow.
- There is no operating-system or k3s upgrade policy.

### Repository hygiene

- `work/*` and `generated/*` are ignored wholesale.
- No obvious credential signatures were found in tracked history by a basic
  pattern scan.
- Ignored generated directories do contain plaintext credential-bearing data.
- There are no repository-level tests, lint configuration, task runner, CI
  workflow, release process, or dependency update automation.
- The README mixes installation, migrations, authentication, troubleshooting,
  and upgrades into one long procedure.

## Recommended target architecture

For one server, use a push-based deployment rather than introducing Flux or
Argo CD immediately:

```text
Git repository
   |-- CI: validate, build immutable images, push to registry
   |-- Ansible: bootstrap and reconcile Ubuntu/k3s
   `-- tracked Kustomize manifests: complete cluster desired state
                            |
                            v
                     one-command deploy
```

An appropriate repository layout would be:

```text
versions.yaml
Makefile
ansible/
  inventory/production/
  roles/base/
  roles/k3s/
  roles/omnigent/
kubernetes/
  base/
  overlays/production/
images/
  runner/
scripts/
  preflight
  smoke-test
```

This keeps the operational model simple while supporting repeatable remote
updates. Pull-based GitOps can be introduced later if automatic reconciliation
after merge becomes desirable.

### Durability boundary

The deployment will not provide backups or disaster recovery. Git is sufficient
to recreate the non-secret software stack; generated secrets are recreated and
external credentials are entered again. PostgreSQL records, artifacts,
Codex/Claude login state, GitHub connections, and Vault data are not preserved.
After node or disk loss, provision a clean server and repeat the required
login/connection flows. Monitoring should still report disk and PVC health so
avoidable failures can be addressed, but recovery of lost application data is
explicitly out of scope.

### Initial resource-sizing baseline

The current Omnigent server limit of 1Gi memory is conservative and could cause
avoidable `OOMKilled` restarts. Use the following as the initial production
baseline, then adjust it from observed usage:

```yaml
resources:
  requests:
    cpu: 500m
    memory: 1Gi
  limits:
    cpu: "2"
    memory: 2Gi
```

Use a 4Gi memory limit when the host has sufficient capacity or the deployment
serves several concurrent users. The limit applies only to the Omnigent server;
PostgreSQL, Vault, Kubernetes system services, and every runner need additional
capacity. Because each runner can currently consume up to 4Gi, size the node
from the intended concurrency rather than from the server Pod alone. Treat
16Gi host RAM as a practical starting point for modest multi-user operation,
then validate it with live metrics and workload tests.

## Implementation plan

### Phase 0: Create the greenfield repository

- Create `../omnigent-deployment` as a sibling of the current setup.
- Treat the current repository and live installation as read-only reference;
  do not import manifests, generated files, Secrets, PVC data, or cluster state.
- Add the planned top-level structure, `README.md`, `.gitignore`, `plan.md`, and
  a single `versions.yaml`.
- Initialize it as an independent Git repository when its initial scaffold is
  ready to commit.
- Generate entirely new internal secrets and repeat external authentication for
  the eventual new installation.

**Acceptance criteria:** the replacement is self-contained in its own directory
and has no runtime or state dependency on the current installation.

### Phase 1: Establish one source of truth

- Build and track the complete non-secret desired state in the new repository.
- Vendor the small required upstream manifest set, or reference an immutable
  upstream commit. Vendoring is preferred so rendering requires no network.
- Use one validated environment values file rather than copying the old textual
  templates or `deployment-info.txt` approach.
- Put all versions in `versions.yaml`.
- Set the initial Omnigent server request to 500m CPU/1Gi memory and its limit to
  2 CPU/2Gi memory, with an environment override for larger installations.
- Make GitHub/Vault enablement an explicit feature setting.
- Do not copy historic migration or repair scripts into the new repository.
- Add offline `render`, `validate`, and `diff` operations.
- Use an object inventory or prune mechanism for removed resources.
- Add configuration and Secret checksums to Pod templates so relevant changes
  trigger rollouts.

**Acceptance criteria:** a clean clone renders exactly the intended non-secret
manifests, and two consecutive deployments produce no changes.

### Phase 2: Make images immutable

- Build the custom runner image in CI rather than on the production server. Use
  the unchanged upstream server image by digest while the Vault-backed GitHub
  picker remains disabled.
- Push public images to GHCR. The images contain no credentials, so public
  visibility avoids pull credentials on the server. GitHub currently documents
  Container Registry storage and bandwidth as free and promises at least one
  month's notice before changing that policy. Re-evaluate only if that policy
  changes materially.
- Use version/commit tags and deploy immutable image digests.
- Add lockfiles or hash verification for npm and pip dependencies.
- Generate an SBOM and run container vulnerability scanning.
- Remove Docker from the production server after migration.

**Acceptance criteria:** changing runner-image inputs creates a new digest and a
guaranteed rollout, while the previous digest remains available for rollback.

### Phase 3: Automate host bootstrap and ongoing updates

Create an idempotent Ansible playbook that:

- Starts from an existing custom Ubuntu VM at netcup that is reachable over SSH;
  VM purchasing and creation are not automated initially.
- Validates Ubuntu version, architecture, CPU, memory, disk, DNS, and ports.
- Configures host firewalling for SSH, HTTP, HTTPS, and private k3s API access.
- Accepts the public hostname as configuration and verifies DNS. DNS record
  creation remains a small manual netcup step unless API credentials are added
  later.
- Installs and reconciles a pinned k3s version and explicit configuration.
- Installs a pinned cert-manager release.
- Configures Pod Security labels and resource quotas. Any NetworkPolicies for
  control-plane workloads must leave runner ingress and egress unrestricted.
- Pulls public GHCR images without registry credentials.
- Creates generated Kubernetes Secrets only when absent and prompts for external
  credentials only when their corresponding optional feature is enabled.
- Applies desired state, waits for rollouts, and runs smoke tests.

Expose a small operator interface:

```bash
make check ENV=production
make diff ENV=production
make deploy ENV=production
make status ENV=production
```

`make deploy` should connect remotely. Editing or regenerating files directly
on the production server should not be part of the workflow.

**Acceptance criteria:** a blank supported Ubuntu server reaches the unavoidable
interactive authentication stage with one command, and later repository changes
are applied through the same deployment command.

### Phase 4: Normalize secrets and identity setup

- Do not require SOPS, age, or an external secret manager.
- Generate database and cookie secrets directly into Kubernetes only when
  absent. Never render them into tracked files or command output.
- Track non-secret GitHub App configuration declaratively. Prompt once for the
  GitHub client secret when enabling the feature and store it only in the
  Kubernetes Secret.
- Prompt for optional Claude and Git credentials through explicit setup
  commands and store them only in Kubernetes.
- Keep OAuth device flows as explicit, idempotent post-bootstrap commands.
- Add credential presence and expiry checks that never print values.
- Retain one shared Codex and Claude identity. The only user is currently the
  operator, so per-user credential isolation is unnecessary for now. Revisit
  before inviting untrusted users.
- Keep the GitHub repository picker disabled in the initial deployment. Enabling
  it is a future feature change that must add a supported KMS/Vault encryption
  backend; do not silently introduce a weak local cipher or a long-lived root
  token.

**Acceptance criteria:** no additional secret-management service or key is
required. A new server generates its internal secrets and asks only for the
external credentials and unavoidable OAuth confirmations for enabled features.

### Phase 5: Harden runners

- Preserve unrestricted runner ingress and egress. Do not introduce destination
  allowlists, protocol filters, or default-deny runner NetworkPolicies.
- Document unrestricted networking as part of the runner threat model and do
  not present network isolation as a security boundary.
- Add explicit ephemeral-storage requests and limits.
- Add namespace ResourceQuota and LimitRange resources.
- Load-test representative concurrent sessions and tune server, PostgreSQL,
  Vault, runner, and node capacity from measured CPU, memory, and disk usage.
- Monitor and alert on memory pressure, throttling, eviction, and `OOMKilled`
  restarts rather than relying only on static resource estimates.
- Use the ordinary k3s container runtime; gVisor, Kata, and dedicated runner
  nodes are not required.
- Make dangerous Codex bypass behavior an explicit policy setting.
- Mount only the credentials needed by each agent.
- Make shared OAuth storage read-only where possible and serialize refresh
  writes if shared storage remains necessary.
- Keep PostgreSQL and Vault protected by strong authentication, least-privilege
  credentials, and non-public Services without relying on runner network
  filtering.
- Add HSTS and suitable reverse-proxy security headers.

**Acceptance criteria:** agents retain unrestricted network functionality, while
runner compromise does not grant Kubernetes API access, host access, unrelated
agent credentials, or unbounded node resources. The resulting network and
execution risks are explicitly documented and accepted.

### Phase 6: Add operational monitoring

- Do not add the old backup script or promise a restore workflow.
- Monitor server health, TLS expiry, PVC/disk usage, failed runner Jobs, memory
  pressure, and Vault sealing.
- Make alerts actionable through the same deploy/status workflow.
- Document that a lost node is replaced with a clean deployment and fresh
  authentication rather than restored.

**Acceptance criteria:** operators receive useful warning before avoidable
resource failures, while the absence of backup and restore guarantees is clear.

### Phase 7: Establish a general upgrade workflow

- Make a version bump change only `versions.yaml`.
- Have CI render manifests, lint shell/YAML, validate Kubernetes schemas, build
  images, scan them, and run smoke tests.
- Record the deployed Git revision and image digests.
- Roll back by selecting the prior immutable digest.
- Treat database-incompatible upgrades as irreversible: validate their
  migration path in a disposable environment and require explicit approval.
- Add dependency update automation with manual production approval initially.

**Acceptance criteria:** configuration changes and application upgrades use the
same deployment workflow instead of requiring new numbered scripts.

## Settled implementation decisions

- Target an existing custom Ubuntu VM at netcup and configure it over SSH with
  Ansible. Automate the host firewall; keep netcup VM creation and DNS as manual
  prerequisites initially.
- Production deployment is manually triggered with `make deploy`. CI may build
  and validate artifacts but must not deploy production automatically.
- Use a public GHCR image for the custom runner and upstream public images by
  digest for the server and PostgreSQL. As of 2026-09-24, GitHub documents Container Registry
  storage and bandwidth as currently free:
  <https://docs.github.com/en/billing/concepts/product-billing/github-packages>.
- Do not introduce SOPS, age, or an external secret manager. Generate internal
  secrets in Kubernetes and prompt for external credentials when needed.
- Retain shared Codex and Claude identities; the operator is currently the only
  user.
- Use normal container isolation. gVisor and Kata are not required.
- Keep runner ingress and egress unrestricted.
- Do not provide backups or restore workflows.
- Keep the optional GitHub repository picker disabled initially, avoiding Vault
  and its separate credential lifecycle until the feature is actually needed.

## Remaining sizing input

- Decide how many concurrent runners the netcup VM should support and record its
  available RAM, CPU, and disk before finalizing resource limits.

## Recommended first milestone

Implement Phases 0 through 3 first:

1. Scaffold the independent greenfield repository.
2. Establish its complete desired state without copying generated legacy state.
3. Publish immutable images.
4. Deliver an idempotent, one-command Ansible deployment for fresh servers and
   ongoing updates.

This addresses the immediate reproducibility and maintenance problems before
adding optional hardening and operational enhancements.
