# shellcheck shell=sh
# Runner Pods start through a root login shell, so this starts the Podman
# API service at Pod start. Later login shells exit on its lock.
if [ "$(id -u)" = 0 ] && command -v omnigent-podman-service >/dev/null 2>&1; then
    setsid omnigent-podman-service </dev/null >>/run/omnigent-podman-service.log 2>&1 &
fi
