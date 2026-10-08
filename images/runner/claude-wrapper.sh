#!/bin/sh
set -eu

# Managed settings outrank user and project settings, so the deployment policy
# holds however Omnigent or the agent configures Claude Code. Runner agents are
# root; the image also makes this directory writable for non-root helper Pods.
# skipDangerousModePermissionPrompt suppresses the interactive bypass-mode
# consent dialog, which Omnigent cannot answer and which blocks session start.
managed=/etc/claude-code/managed-settings.json

if [ "${OMNIGENT_CLAUDE_BYPASS_PERMISSIONS:-0}" = "1" ] && [ ! -e "$managed" ]; then
    if ! { mkdir -p "${managed%/*}" && printf '%s\n' '{"permissions":{"defaultMode":"bypassPermissions"},"skipDangerousModePermissionPrompt":true}' > "$managed"; } 2>/dev/null; then
        echo "claude-wrapper: could not write $managed; permission bypass not applied" >&2
    fi
fi

# The variables of the repositories this session started with.
# shellcheck source=SCRIPTDIR/repo-env/repo-env.sh
. /usr/local/lib/omnigent/repo-env.sh

exec /usr/local/bin/claude-real "$@"
