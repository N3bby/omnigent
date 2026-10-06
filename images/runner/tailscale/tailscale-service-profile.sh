# shellcheck shell=sh
# Runner Pods start through a root login shell, so this joins the tailnet at
# Pod start when a Tailscale key is configured. Later login shells exit on
# its lock.
if [ "$(id -u)" = 0 ] && command -v omnigent-tailscale >/dev/null 2>&1; then
    setsid omnigent-tailscale </dev/null >>/run/omnigent-tailscale.log 2>&1 &
fi
