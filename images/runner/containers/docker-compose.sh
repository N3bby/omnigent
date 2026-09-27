#!/bin/sh
# Compose's BuildKit builder needs its own cgroup, which Podman cannot create
# here, so `build:` services use the classic builder that Podman implements.
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-0}"
exec /usr/bin/docker-compose "$@"
