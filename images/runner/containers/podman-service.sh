#!/bin/sh
# Serves the Docker API from Podman at Docker's default socket, so
# Testcontainers, docker compose and Docker SDKs work without DOCKER_HOST
# (Omnigent does not pass it to agents). Also runs container healthchecks,
# which Podman otherwise schedules with systemd timers that a Pod lacks.
# Started from every root login shell; the lock keeps it to one copy.
set -u

exec 9>/run/omnigent-podman-service.lock
flock -n 9 || exit 0

socket=/run/podman/podman.sock
mkdir -p /run/podman
ln -sfn "$socket" /run/docker.sock

api() { curl -sf --max-time 60 --unix-socket "$socket" "http://d/v5.0.0/libpod$1"; }

# Checks run every 2s, ignoring each container's own interval, so
# depends_on: service_healthy resolves promptly.
healthchecks() {
    filter='%7B%22health%22%3A%5B%22starting%22%2C%22healthy%22%2C%22unhealthy%22%5D%7D'
    while :; do
        for id in $(api "/containers/json?filters=$filter" | grep -o '"Id":"[0-9a-f]*"' | cut -d'"' -f4); do
            api "/containers/$id/healthcheck" >/dev/null &
        done
        wait
        sleep 2
    done
}
healthchecks &

while :; do
    podman system service --time=0 "unix://$socket"
    sleep 1
done
