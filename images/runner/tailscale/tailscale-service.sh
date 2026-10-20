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

# k3s's Pod and Service ranges. Connections to them never go through the
# tailnet, not even to an exit node or a subnet route that covers them, so
# the Pod keeps reaching the Omnigent server and the cluster's DNS.
cluster_cidrs="10.42.0.0/16 10.43.0.0/16"
# Where the Pod's DNS forwarder listens: a link-local address on an interface
# of its own, which Podman's containers reach too.
dns_ip=169.254.20.10

ts() { tailscale --socket="$socket" "$@"; }
dns_ready() {
    [ -n "$(ss -Hlun "src $dns_ip" 2>/dev/null)" ] && [ -n "$(ss -Hlun "src 127.0.0.54" 2>/dev/null)" ]
}
# Shows how joining goes in the session while the sandbox starts
# (omnigent-startup-report); never fails this service.
report() {
    command -v omnigent-startup-report >/dev/null 2>&1 || return 0
    omnigent-startup-report step host Tailscale "$@" </dev/null >/dev/null 2>&1 || true
}
report running "Joining your tailnet as $name"

# With /dev/net/tun, from the TUN device plugin
# (kubernetes/platform/tun-device-plugin.yaml), tailscaled routes the Pod's
# own connections: every tool reaches tailnet names and addresses, and an exit
# node carries the Pod's internet traffic. Without it, the Pod uses userspace
# networking: tailnet connections out go through tailscaled's proxy and
# `tailscale nc` (ssh-tailnet.conf).
if [ -c /dev/net/tun ]; then
    mode=tun
    for cidr in $cluster_cidrs; do
        ip rule del to "$cidr" lookup main priority 5200 2>/dev/null
        ip rule add to "$cidr" lookup main priority 5200
    done
else
    mode=userspace
fi

# Either way, tailscaled serves a SOCKS5 and an HTTP proxy, which share one
# port and resolve MagicDNS names.
(
    while :; do
        if [ "$mode" = tun ]; then
            # Tailnet connections to the Pod's own ports reach servers that
            # only listen on localhost, as with userspace networking, through
            # the DNAT rules below, also from the Pod itself to its own
            # tailnet name. The kernel only routes them to 127.0.0.1 with
            # route_localnet on the interface tailscaled creates.
            (
                until [ -d /proc/sys/net/ipv4/conf/tailscale0 ]; do sleep 0.2; done
                echo 1 >/proc/sys/net/ipv4/conf/tailscale0/route_localnet
            ) &
            tailscaled --tun=tailscale0 --statedir="$state" --socket="$socket" \
                --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055
        else
            tailscaled --tun=userspace-networking --statedir="$state" --socket="$socket" \
                --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055
        fi
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
# --reset forgets settings from an earlier start, such as another exit node.
# The Pod's DNS forwarder below resolves MagicDNS names, so tailscaled leaves
# /etc/resolv.conf alone, and Podman keeps the firewall.
set -- --reset --auth-key="$key" --hostname="$name" --advertise-tags="$tags" --ssh --accept-routes
if [ "$mode" = tun ]; then
    set -- "$@" --accept-dns=false --netfilter-mode=off
fi
delay=15
until ts up "$@" --timeout=60s; do
    echo "omnigent-tailscale: could not join the tailnet; retrying in ${delay}s" >&2
    report running "Couldn't join the tailnet; retrying in ${delay}s"
    sleep "$delay"
    [ "$delay" -ge 300 ] || delay=$((delay * 2))
done

status=$(ts status --json)
field() { printf '%s' "$status" | python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }
dns=$(field '["Self"]["DNSName"].rstrip(".")')
suffix=$(field '.get("MagicDNSSuffix") or ""')

if [ "$mode" = tun ]; then
    nft -f - <<'EOF' || echo "omnigent-tailscale: could not forward tailnet connections to localhost" >&2
table ip omnigent-tailscale
delete table ip omnigent-tailscale
table ip omnigent-tailscale {
	chain prerouting {
		type nat hook prerouting priority dstnat + 10; policy accept;
		iifname "tailscale0" ip daddr 100.64.0.0/10 meta l4proto { tcp, udp } dnat to 127.0.0.1
	}
	chain output {
		type nat hook output priority dstnat + 10; policy accept;
		oifname "lo" ip daddr 100.64.0.0/10 meta l4proto { tcp, udp } dnat to 127.0.0.1
	}
}
EOF

    # MagicDNS: a forwarder sends the tailnet's names to tailscaled's
    # resolver, and everything else to the cluster's DNS, as before. Unlike
    # tailscaled's own DNS setup, it keeps cluster names working when an exit
    # node is in use. Without that setup, tailscaled's resolver answers a
    # name that's no machine of the tailnet with SERVFAIL, which stops musl's
    # search-list lookups (Alpine containers), so a second forwarder answers
    # those with NXDOMAIN. The Pod's /etc/resolv.conf is a mount, so it's
    # rewritten in place; the original is kept for the forwarder.
    original=/run/omnigent-tailscale.resolv.conf
    grep -q '^# omnigent-tailscale' /etc/resolv.conf || cp /etc/resolv.conf "$original"
    nameservers=$(awk '$1 == "nameserver" { print $2 }' "$original")
    if [ -n "$suffix" ] && [ -n "$nameservers" ] && command -v dnsmasq >/dev/null; then
        ip link add omnigent-dns type dummy 2>/dev/null
        ip addr replace "$dns_ip/32" dev omnigent-dns
        ip link set omnigent-dns up
        (
            while :; do
                dnsmasq --keep-in-foreground --pid-file= --no-resolv --no-hosts --user=root \
                    --listen-address=127.0.0.54 --bind-interfaces --log-facility=/dev/null --address=/#/
                sleep 1
            done
        ) &
        # --strict-order tries tailscaled's resolver first, and the NXDOMAIN
        # forwarder after a SERVFAIL.
        set -- --keep-in-foreground --pid-file= --no-resolv --no-hosts --no-negcache --strict-order \
            --user=root --listen-address="$dns_ip" --bind-interfaces --log-facility=- \
            --server="/$suffix/100.100.100.100" --server="/$suffix/127.0.0.54"
        for nameserver in $nameservers; do
            set -- "$@" --server="$nameserver"
        done
        (
            while :; do
                dnsmasq "$@" 2>&1 | grep -v '^dnsmasq\[[0-9]*\]: \(using\|started\|compile\|cleared\)'
                sleep 1
            done
        ) &
        # Only once both answer: a Pod whose forwarder doesn't start keeps
        # the cluster's DNS.
        tries=0
        until dns_ready || [ "$tries" -ge 50 ]; do
            sleep 0.1
            tries=$((tries + 1))
        done
    fi
    if dns_ready; then
        # Kubernetes puts the Pod's own three search domains first; the
        # tailnet's comes next, so a short name finds a tailnet machine
        # before a machine on the VM's network.
        {
            echo "# omnigent-tailscale: MagicDNS through the forwarder on $dns_ip; the original is $original"
            awk -v suffix="$suffix" '
                $1 == "search" {
                    n = 0
                    for (i = 2; i <= NF; i++) if ($i != suffix) domain[++n] = $i
                    line = "search"
                    for (i = 1; i <= n; i++) {
                        line = line " " domain[i]
                        if (i == 3) line = line " " suffix
                    }
                    if (n < 3) line = line " " suffix
                    print line
                    found = 1
                }
                $1 == "options" { print }
                END { if (!found) print "search " suffix }
            ' "$original"
            echo "nameserver $dns_ip"
        } >/run/omnigent-tailscale.resolv.conf.new
        cat /run/omnigent-tailscale.resolv.conf.new >/etc/resolv.conf
        rm -f /run/omnigent-tailscale.resolv.conf.new
    fi
fi

# A repository can name an exit node (omnigent_repo_env.EXIT_NODE_KEY), which
# then carries the session's internet traffic. What became of it goes to a
# file for the environment menu (runner patch 0012-environment-exit-node.patch):
# `note_exit_node names A B`, `note_exit_node one A`, or
# `note_exit_node one A "<Tailscale's error>"`.
exit_node_file=/run/omnigent-tailscale.exit-node.json
rm -f "$exit_node_file"
note_exit_node() {
    python3 -c 'import json, sys
kind, *args = sys.argv[1:]
print(json.dumps({"names": args} if kind == "names" else dict(zip(("name", "error"), args))))' "$@" \
        >"$exit_node_file.new" && mv -f "$exit_node_file.new" "$exit_node_file"
}
exit_nodes=$(
    for file in /run/omnigent/repo-env/*/tailscale-exit-node; do
        [ -s "$file" ] && tr -d '[:space:]' <"$file" && echo
    done | sed '/^$/d' | sort -u
)
via=""
case $exit_nodes in
    "") ;;
    *"
"*)
        list=$(printf '%s' "$exit_nodes" | paste -sd, - | sed 's/,/, /g')
        echo "omnigent-tailscale: the repositories name different exit nodes ($list); using none" >&2
        report failed "Joined as $dns, without an exit node: the repositories name different ones ($list)"
        # shellcheck disable=SC2086 # one name per line, and names have no spaces
        note_exit_node names $exit_nodes
        exit_nodes=""
        failed=1
        ;;
    *)
        if error=$(ts set --exit-node="$exit_nodes" --exit-node-allow-lan-access 2>&1); then
            via=", through exit node $exit_nodes"
            note_exit_node one "$exit_nodes"
            echo "omnigent-tailscale: using exit node $exit_nodes"
        else
            error=$(printf '%s' "$error" | tr '\n' ' ')
            echo "omnigent-tailscale: could not use exit node $exit_nodes: $error" >&2
            report failed "Joined as $dns, but couldn't use exit node $exit_nodes: $error"
            note_exit_node one "$exit_nodes" "$error"
            failed=1
        fi
        ;;
esac

# Tailscale renames a node whose name another device already holds, and then
# the name in Omnigent doesn't reach this Pod.
case $dns in
    "$name".*)
        echo "omnigent-tailscale: joined as $dns ($mode networking)"
        if [ -z "${failed:-}" ]; then
            report "done" "$dns$via"
        fi
        ;;
    *)
        echo "omnigent-tailscale: joined as $dns because another device is named $name; Omnigent shows $name" >&2
        if [ -z "${failed:-}" ]; then
            report "done" "Joined as $dns, because another device is named $name$via"
        fi
        ;;
esac
wait
