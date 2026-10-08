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

# Claude Code's auto-updater runs `npm install -g` with the first npm on PATH.
# That replaces this wrapper with an unpinned Claude, or, under a project's
# mise Node, adds a `claude` shim to the home volume that comes before it on
# PATH, so later sessions skip the wrapper. Versions come from versions.yaml.
export DISABLE_AUTOUPDATER=1

# The variables of the repositories this session started with.
# shellcheck source=SCRIPTDIR/repo-env/repo-env.sh
. /usr/local/lib/omnigent/repo-env.sh

# Keep bypass in Claude's shift+tab cycle whatever mode the session launches
# in, so the web picker can always switch back to it, e.g. after approving a
# plan moved the session to auto. Subcommands such as `claude mcp` accept it.
# Keyed on the managed settings, not the variable: Omnigent drops
# OMNIGENT_CLAUDE_BYPASS_PERMISSIONS from the env of the Claude terminals it
# launches, so the file is how a terminal sees the deployment's setting.
if grep -q '"defaultMode":"bypassPermissions"' "$managed" 2>/dev/null; then
    exec /usr/local/bin/claude-real --allow-dangerously-skip-permissions "$@"
fi

exec /usr/local/bin/claude-real "$@"
