#!/bin/sh
# Runs a command, workspace-prep's clone of a repository, with its Git
# connections going through the tailnet exit node the repository names:
#
#     omnigent-via-exit-node /run/omnigent/repo-env/<Secret>/tailscale-exit-node git clone ...
#
# clone_command in omnigent_repo_env.py calls it only when that file has a
# name. The init container has no TUN device or capabilities, and the Pod's
# own node joins only once the host container starts, so this joins a
# temporary ephemeral node with userspace networking, uses its exit node, and
# points Git at its SOCKS5 proxy: HTTPS URLs through http.proxy, SSH URLs
# through `tailscale nc`. The node logs out when the command ends.
set -u

file=$1
shift
exit_node=$(tr -d '[:space:]' <"$file")
fail() {
    echo "omnigent-via-exit-node: $*" >&2
    exit 1
}
[ -n "$exit_node" ] || fail "$file names no exit node"
key=${TAILSCALE_AUTHKEY:-}
[ -n "$key" ] || fail "the repository uses exit node $exit_node, but no Tailscale key is stored (mise run setup-tailscale)"
case $key in
    tskey-client-*) key="$key?ephemeral=true&preauthorized=true" ;;
esac

dir=$(mktemp -d)
socket=$dir/tailscaled.sock
ts() { tailscale --socket="$socket" "$@"; }
# A free port for the proxy; clones of several repositories run at once.
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
tailscaled --tun=userspace-networking --state=mem: --statedir="$dir/state" --socket="$socket" \
    --socks5-server="127.0.0.1:$port" >"$dir/tailscaled.log" 2>&1 &
daemon=$!
# shellcheck disable=SC2317 # called by the EXIT trap
cleanup() {
    timeout 10 tailscale --socket="$socket" logout >/dev/null 2>&1
    kill "$daemon" 2>/dev/null
    wait "$daemon" 2>/dev/null
    rm -rf "$dir"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

until [ -S "$socket" ]; do
    kill -0 "$daemon" 2>/dev/null || fail "tailscaled stopped: $(tail -n 5 "$dir/tailscaled.log")"
    sleep 0.2
done
ts up --auth-key="$key" --hostname=omnigent-clone --advertise-tags="${OMNIGENT_TAILSCALE_TAGS:-tag:omnigent-runner}" \
    --timeout=60s >/dev/null || fail "could not join the tailnet to use exit node $exit_node"
ts set --exit-node="$exit_node" ||
    fail "could not use exit node $exit_node. Check that it offers to be an exit node, and that your tailnet policy lets tag:omnigent-runner use it."
tries=0
until [ "$(ts status --json | python3 -c 'import json, sys; print((json.load(sys.stdin).get("ExitNodeStatus") or {}).get("Online", False))')" = True ]; do
    tries=$((tries + 1))
    [ "$tries" -lt 60 ] || fail "exit node $exit_node is offline"
    sleep 0.5
done
echo "omnigent-via-exit-node: cloning through exit node $exit_node"

# ssh is called by name, also from a repository key's core.sshCommand
# (ssh_command in omnigent_repo_env.py); a ProxyCommand given first wins.
mkdir "$dir/bin"
cat >"$dir/bin/ssh" <<EOF
#!/bin/sh
exec /usr/bin/ssh -o "ProxyCommand=tailscale --socket=$socket nc %h %p" "\$@"
EOF
chmod 0755 "$dir/bin/ssh"
# Only Git gets the proxy: the GitHub App's credential helper talks to the
# Omnigent server, which isn't on the tailnet.
count=${GIT_CONFIG_COUNT:-0}
env PATH="$dir/bin:$PATH" GIT_CONFIG_COUNT=$((count + 1)) \
    "GIT_CONFIG_KEY_$count=http.proxy" "GIT_CONFIG_VALUE_$count=socks5h://127.0.0.1:$port" \
    "$@"
status=$?
exit "$status"
