# Runner threat model

Runner Jobs execute third-party repositories and agent-generated commands. They
must therefore be treated as potentially compromised.

The accepted functional boundary is unusual but explicit: runner Pods have
unrestricted ingress and egress, including arbitrary internet and cluster
destinations. There is no runner `NetworkPolicy`, destination allowlist, proxy
filter, or protocol filter. Network reachability is not a security boundary.

The boundaries that do exist are:

- runners use a dedicated namespace; every Pod there must run in its own user
  namespace (`hostUsers: false`) and must not use host networking, PID, IPC,
  ports, hostPath volumes, or privileged mode (`runner-userns.yaml`);
- the runner ServiceAccount has no RBAC and its token is not mounted;
- runner agents are root with all capabilities, unconfined seccomp and
  AppArmor, and an unmasked `/proc`, so they can `apt-get install` and run
  Podman. That root maps to an unprivileged host UID and the capabilities only
  apply inside the Pod's user namespace; escaping still requires a kernel bug,
  but the reachable kernel surface is larger than under `restricted` Pod
  Security, which the namespace no longer enforces;
- Podman containers run without cgroups (the Pod's cgroup is not delegated)
  and share the Pod's network, so they are bounded only by the Pod's limits;
- CPU, memory, ephemeral storage, home size, Pod count, and Job count are
  bounded;
- the Omnigent server can manage Jobs, Pods, launch Secrets, logs, and events
  only in the runner namespace;
- PostgreSQL and the server's Secrets remain in a different namespace;
- only runner credentials are projected into runner Pods;
- normal containerd isolation is accepted; gVisor and Kata are out of scope.

The shared Codex PVC and shared Claude token mean one compromised runner can use
the operator's agent identities. That is accepted while the operator is the
only user. Revisit per-user identities and dedicated runner nodes before
inviting anyone who is not equally trusted.

GitHub OAuth refresh tokens are encrypted through Vault Transit before database
storage. Vault and its unseal material live in the same cluster so restarts are
unattended. This protects against disclosure of the database alone; root access
to the VM or administrative access to the cluster can recover both the
ciphertext and the material needed to decrypt it.

`codex_bypass_approvals` is a visible production setting. When true, the image
wrapper passes Codex's dangerous bypass flag for native Omnigent Codex sessions.
Turning it off removes that flag on newly created runners.

`claude_bypass_permissions` is a visible production setting. When true, the
image wrapper writes `/etc/claude-code/managed-settings.json` to set Claude
Code's default mode to `bypassPermissions`. Runner agents are root; the image
also makes that directory writable by non-root helper Pods. Turning the setting off
affects newly created runners; any running runner keeps the file until it is
recreated.
