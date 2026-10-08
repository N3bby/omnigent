#!/bin/sh
# Joins this runner Pod to the tailnet as an ephemeral node named
# omnigent-<first 8 hex of the host id>, the name the Omnigent composer shows
# (server patch 0003-tailscale-host.patch derives the same name).
# Started from every root login shell; the lock keeps it to one copy.
set -u

# Only the host container has a host id. The init container also starts
# through a login shell, and must not join.
host_id=${OMNIGENT_HOST_ID:-}
key=${TAILSCALE_AUTHKEY:-}
[ -n "$host_id" ] && [ -n "$key" ] || exit 0

exec 9>/run/omnigent-tailscale.lock
flock -n 9 || exit 0

name="omnigent-$(printf '%.8s' "$host_id" | tr '[:upper:]' '[:lower:]')"
tags=${OMNIGENT_TAILSCALE_TAGS:-tag:omnigent-runner}
# The node's state lives in the session's home volume, so a woken Pod keeps
# its SSH host keys. It rejoins as the same node only if it stopped without
# running its logout hook (tailscale-logout.sh) and the node is still listed.
state=${HOME:-/home/omnigent}/.local/state/omnigent-tailscale
socket=/run/tailscale/tailscaled.sock
mkdir -p "$state" /run/tailscale
chmod 700 "$state"

ts() { tailscale --socket="$socket" "$@"; }

# The Pod has no /dev/net/tun. Userspace networking still delivers tailnet
# connections to the Pod's own ports, and serves Tailscale SSH.
(
    while :; do
        tailscaled --tun=userspace-networking --statedir="$state" --socket="$socket"
        sleep 1
    done
) &

while [ ! -S "$socket" ]; do
    sleep 0.2
done

# An OAuth client secret mints one key per node; these make that key
# ephemeral and skip device approval. A plain auth key carries both itself.
case $key in
    tskey-client-*) key="$key?ephemeral=true&preauthorized=true" ;;
esac
delay=15
until ts up --auth-key="$key" --hostname="$name" --advertise-tags="$tags" --ssh --timeout=60s; do
    echo "omnigent-tailscale: could not join the tailnet; retrying in ${delay}s" >&2
    sleep "$delay"
    [ "$delay" -ge 300 ] || delay=$((delay * 2))
done

# Tailscale renames a node whose name another device already holds, and then
# the name in Omnigent doesn't reach this Pod.
dns=$(ts status --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
case $dns in
    "$name".*) echo "omnigent-tailscale: joined as $dns" ;;
    *) echo "omnigent-tailscale: joined as $dns because another device is named $name; Omnigent shows $name" >&2 ;;
esac
wait
