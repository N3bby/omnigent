# Runner threat model

Runner Jobs execute third-party repositories and agent-generated commands. They
must therefore be treated as potentially compromised.

The accepted functional boundary is unusual but explicit: runner Pods have
unrestricted ingress and egress, including arbitrary internet and cluster
destinations. There is no runner `NetworkPolicy`, destination allowlist, proxy
filter, or protocol filter. Network reachability is not a security boundary.

The boundaries that do exist are:

- runners use a dedicated namespace with Kubernetes `restricted` Pod Security;
- the runner ServiceAccount has no RBAC and its token is not mounted;
- generated Pods run non-root, drop all capabilities, use RuntimeDefault
  seccomp, and cannot escalate privileges;
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

`codex_bypass_approvals` is a visible production setting. When true, the image
wrapper passes Codex's dangerous bypass flag for native Omnigent Codex sessions.
Turning it off removes that flag on newly created runners.

