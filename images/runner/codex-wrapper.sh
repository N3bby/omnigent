#!/bin/sh
set -eu

real_codex=/usr/local/bin/codex-real
shared_auth=/mnt/codex-home/auth.json

if [ -n "${CODEX_HOME:-}" ]; then
    case "$CODEX_HOME" in
        /home/omnigent/.omnigent/codex-native/*/codex-home|*/omnigent-codex-home-*)
            mkdir -p "$CODEX_HOME"
            if [ -f "$shared_auth" ] && [ ! -e "$CODEX_HOME/auth.json" ]; then
                ln -s "$shared_auth" "$CODEX_HOME/auth.json"
            fi
            if [ "${OMNIGENT_CODEX_BYPASS_APPROVALS:-0}" = "1" ]; then
                exec "$real_codex" --dangerously-bypass-approvals-and-sandbox "$@"
            fi
            ;;
    esac
fi

exec "$real_codex" "$@"

