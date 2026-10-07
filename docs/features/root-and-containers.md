# Root and containers

Agents are root in their session, so they can install whatever a task needs
with `apt-get install`. They can also build and run containers with Podman.

The usual Docker tools work too:

- `docker` commands
- `docker compose` with a `compose.yaml`
- test libraries such as Testcontainers

## Is root safe?

That root only applies inside the Pod. A Linux user namespace maps it to an
ordinary, unprivileged user on the VM.

![Root inside the pod maps to an unprivileged user on the host](../images/runner-root.svg)

See the [threat model](../threat-model.md#what-keeps-a-runner-contained) for
what this does and doesn't protect.

## Using it

- **Published ports** (`-p 8080:80`, or `ports:` in compose) are reachable on
  `localhost` inside the session.
- **Compose services** can reach each other by name.
- **Container images are kept** in the session's home directory, so they're
  still there after an [idle session](idle-sessions.md) wakes up.

## What doesn't work

- `docker buildx` and BuildKit. `docker build` and compose `build:` services
  still work, using Podman's builder.
- Per-container resource limits. The Pod's own limits still apply.

## Turning it off

You can't.

## How it works

- **Admission policies** (`kubernetes/platform/runner-userns.yaml`):
  - One makes every runner Pod use its own user namespace
    (`hostUsers: false`) and run as UID 0.
  - The other rejects Pods in `omnigent-sandboxes` that are privileged,
    mount `hostPath`, share host namespaces or use host ports.
- **Runner image:** installs Podman, with `docker` as an alias, and Docker
  Compose. They're configured by `images/runner/containers/`. Containers run
  without their own cgroups, because containerd doesn't give cgroups to
  user-namespaced Pods.
- **Docker API:** `omnigent-podman-service` starts with the Pod and serves the
  Docker API at `/var/run/docker.sock`, where Docker tools look by default.
  It also runs container healthchecks, which Podman normally leaves to
  systemd, so `depends_on: service_healthy` works.
- **After upgrading k3s**, run `scripts/check-userns` on the VM to check that
  all of this still works.
