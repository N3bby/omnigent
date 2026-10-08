#!/bin/sh
# The runner Pod's preStop hook (kubernetes/platform/runner-userns.yaml) runs
# this before the Pod stops. Logging out removes an ephemeral node from the
# tailnet straight away. Otherwise Tailscale keeps the stopped Pod's node for
# another 30 to 60 minutes, and counts them against the tailnet's monthly
# ephemeral minutes.
# Never fails, so a Pod that never joined still stops as usual.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
socket=/run/tailscale/tailscaled.sock
[ -S "$socket" ] || exit 0

{
    if timeout 10 tailscale --socket="$socket" logout; then
        echo "omnigent-tailscale: logged out as the Pod stops"
    else
        echo "omnigent-tailscale: could not log out as the Pod stops"
    fi
} >>/run/omnigent-tailscale.log 2>&1
exit 0
