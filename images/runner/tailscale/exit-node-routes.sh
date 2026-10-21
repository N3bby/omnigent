#!/bin/sh
# Sends only the Pod's traffic to some hosts, a repository's Git server,
# through the tailnet exit node, and the rest of its internet traffic out
# directly:
#
#     omnigent-exit-node-routes          # sets the rules for the hosts in the file
#     omnigent-exit-node-routes --keep   # and again every 5 minutes
#
# The hosts are in /run/omnigent-tailscale.exit-node-hosts, one per line.
# tailscale-service.sh writes it, and runs this before it sets the exit node,
# for repositories with tailscale-exit-node-git-only
# (omnigent_repo_env.EXIT_NODE_GIT_ONLY_KEY) when the Pod has the TUN device.
#
# tailscaled keeps its routes in table 52, which its rule at priority 5270
# looks up for everything; with an exit node, that's a default route through
# tailscale0. These rules come first, for IPv4 and IPv6:
#
#     5260  table 52 without its default route: tailnet machines, subnet routes
#     5261  one per address of the hosts: table 52, so the exit node
#     5262  the main table, so everything else goes out directly
#     5263  unreachable: the Pod has no IPv6 route to the internet of its own
#
# Without an exit node, such as after the agent switched it off, table 52 has
# no default route, so the hosts' addresses go out directly as well.
# Addresses can change, so --keep resolves the hosts again every 5 minutes, or
# every 30 seconds while one doesn't resolve. A host that doesn't resolve keeps
# the addresses it had.
set -u

hosts_file=${OMNIGENT_EXIT_NODE_HOSTS_FILE:-/run/omnigent-tailscale.exit-node-hosts}
log() {
    echo "omnigent-exit-node-routes: $*" >&2
}

# `ip rule add` adds a copy each time, so the fixed rules are replaced.
base_rules() {
    family=$1
    for priority in 5260 5262 5263; do
        while ip "-$family" rule del priority "$priority" 2>/dev/null; do :; done
    done
    ip "-$family" rule add priority 5260 lookup 52 suppress_prefixlength 0 &&
        ip "-$family" rule add priority 5262 lookup main &&
        ip "-$family" rule add priority 5263 unreachable
}

# The hosts' addresses, one per line; fails when a host doesn't resolve.
resolve() {
    status=0
    while read -r host; do
        case $host in "" | "#"*) continue ;; esac
        found=$(getent ahosts "$host" | awk '{ print $1 }' | sort -u)
        if [ -n "$found" ]; then
            printf '%s\n' "$found"
        else
            log "could not resolve $host; it keeps the addresses it had"
            status=1
        fi
    done <"$hosts_file"
    return "$status"
}

# The 5261 rules' addresses of one family, one per line.
routed() {
    ip "-$1" rule show priority 5261 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "to") print $(i + 1) }'
}

# Gives the addresses of one family a 5261 rule each, and removes the other
# rules unless a host didn't resolve.
sync_family() {
    family=$1
    wanted=$2
    prune=$3
    current=$(routed "$family")
    for address in $wanted; do
        printf '%s\n' "$current" | grep -qxF "$address" && continue
        ip "-$family" rule add priority 5261 to "$address" lookup 52 && log "$address goes through the exit node"
    done
    [ "$prune" = yes ] || return 0
    for address in $current; do
        printf '%s\n' "$wanted" | grep -qxF "$address" && continue
        ip "-$family" rule del priority 5261 to "$address" lookup 52 && log "$address no longer goes through the exit node"
    done
}

sync() {
    [ -r "$hosts_file" ] || {
        log "$hosts_file is missing"
        return 1
    }
    if addresses=$(resolve); then prune=yes; else prune=no; fi
    sync_family 4 "$(printf '%s\n' "$addresses" | grep -v ':')" "$prune"
    sync_family 6 "$(printf '%s\n' "$addresses" | grep ':')" "$prune"
    [ "$prune" = yes ]
}

if [ "${1:-}" = --keep ]; then
    while :; do
        if sync; then sleep 300; else sleep 30; fi
    done
fi

base_rules 4 || {
    log "could not add the IPv4 rules"
    exit 1
}
# A Pod without IPv6 has nothing to send through it anyway.
base_rules 6 2>/dev/null || log "could not add the IPv6 rules"
sync
