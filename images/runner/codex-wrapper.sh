#!/bin/sh
set -eu

# The variables of the repositories this session started with.
# shellcheck source=SCRIPTDIR/repo-env/repo-env.sh
. /usr/local/lib/omnigent/repo-env.sh

# The instructions of the repositories this session started with, after the
# runner's own, once their setup scripts are done or have had their time.
# Omnigent links each session's CODEX_HOME/AGENTS.md to this file.
# shellcheck source=SCRIPTDIR/repo-env/agent-instructions.sh
. /usr/local/lib/omnigent/agent-instructions.sh
omnigent_wait_for_repo_setup
omnigent_agent_instructions /opt/codex-home/AGENTS.md \
    || echo "codex-wrapper: could not add the repository instructions to /opt/codex-home/AGENTS.md" >&2

real_codex=/usr/local/bin/codex-real
# The image's own Node, not whatever a project's mise config pins: Codex starts
# in the project directory, where the mise shims come first on PATH.
node=/usr/local/bin/node
shared_auth=/mnt/codex-home/auth.json
# A remote TUI belongs to the already-running app-server. Keep its login link;
# changing that link could make the old server refresh into another account's file.
remote=0
for argument in "$@"; do
    case "$argument" in --remote|--remote=*) remote=1 ;; esac
done
subscription_auth=""
if [ "$remote" = "0" ] || [ ! -f "${CODEX_HOME:-/opt/codex-home}/auth.json" ]; then
    subscription_auth="$(/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py codex)"
fi
if [ -n "${CODEX_HOME:-}" ]; then
    mkdir -p "$CODEX_HOME"
    # Each Codex process holds a shared lock on its home until it exits: fd 9
    # survives the exec below. Codex writes refreshed tokens through the
    # auth.json link, so the link only moves to another subscription when no
    # process here still holds the old one. A read-only home, like
    # credential-status's, can't be relinked and needs no lock.
    lock="$CODEX_HOME/.omnigent-auth.lock"
    if (: >>"$lock") 2>/dev/null; then
        exec 9>>"$lock"
        if [ -n "$subscription_auth" ] && [ "$(readlink "$CODEX_HOME/auth.json" || true)" != "$subscription_auth" ]; then
            if flock -n -x 9; then
                ln -sfn "$subscription_auth" "$CODEX_HOME/auth.json"
            else
                echo "codex-wrapper: another Codex process in $CODEX_HOME still uses the previous subscription; this one shares it until they all exit" >&2
            fi
        fi
        flock -s 9
    fi
fi

if [ -n "${CODEX_HOME:-}" ]; then
    case "$CODEX_HOME" in
        /home/omnigent/.omnigent/codex-native/*/codex-home|*/omnigent-codex-home-*)
            mkdir -p "$CODEX_HOME"
            if [ -z "$subscription_auth" ] && [ -f "$shared_auth" ] && [ ! -e "$CODEX_HOME/auth.json" ]; then
                ln -s "$shared_auth" "$CODEX_HOME/auth.json"
            fi
            if [ "${OMNIGENT_CODEX_BYPASS_APPROVALS:-0}" = "1" ]; then
                exec "$node" "$real_codex" --dangerously-bypass-approvals-and-sandbox "$@"
            fi
            ;;
    esac
fi

exec "$node" "$real_codex" "$@"

