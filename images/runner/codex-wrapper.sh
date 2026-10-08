#!/bin/sh
set -eu

# The variables of the repositories this session started with.
# shellcheck source=SCRIPTDIR/repo-env/repo-env.sh
. /usr/local/lib/omnigent/repo-env.sh

real_codex=/usr/local/bin/codex-real
# The image's own Node, not whatever a project's mise config pins: Codex starts
# in the project directory, where the mise shims come first on PATH.
node=/usr/local/bin/node
shared_auth=/mnt/codex-home/auth.json

if [ -n "${CODEX_HOME:-}" ]; then
    case "$CODEX_HOME" in
        /home/omnigent/.omnigent/codex-native/*/codex-home|*/omnigent-codex-home-*)
            mkdir -p "$CODEX_HOME"
            if [ -f "$shared_auth" ] && [ ! -e "$CODEX_HOME/auth.json" ]; then
                ln -s "$shared_auth" "$CODEX_HOME/auth.json"
            fi
            if [ "${OMNIGENT_CODEX_BYPASS_APPROVALS:-0}" = "1" ]; then
                exec "$node" "$real_codex" --dangerously-bypass-approvals-and-sandbox "$@"
            fi
            ;;
    esac
fi

exec "$node" "$real_codex" "$@"

