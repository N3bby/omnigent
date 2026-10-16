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

# Claude Code opens its first-run theme picker, which Omnigent cannot answer,
# when its global config lacks hasCompletedOnboarding. Omnigent adds the key
# just before it starts a session's terminal, but in a new home another Claude,
# such as the host's `claude auth status` or the title generator's `claude -p`,
# may have created the file without it, and writes back the config it read.
# So the first Claude in a new home creates the file with the key. It's linked
# into place whole, and a file that exists by then is left alone.
config="${CLAUDE_CONFIG_DIR:-${HOME:-/home/omnigent}}/.claude.json"
if [ ! -e "$config" ]; then
    seed="$(mktemp "$config.XXXXXX" 2>/dev/null)" || seed=""
    if [ -z "$seed" ] || ! { printf '%s\n' '{"hasCompletedOnboarding":true}' > "$seed" && { ln "$seed" "$config" || [ -e "$config" ]; }; } 2>/dev/null; then
        echo "claude-wrapper: could not create $config; Claude Code may show its first-run setup" >&2
    fi
    if [ -n "$seed" ]; then rm -f "$seed"; fi
    unset seed
fi
unset config

# Claude Code's auto-updater runs `npm install -g` with the first npm on PATH.
# That replaces this wrapper with an unpinned Claude, or, under a project's
# mise Node, adds a `claude` shim to the home volume that comes before it on
# PATH, so later sessions skip the wrapper. Versions come from versions.yaml.
export DISABLE_AUTOUPDATER=1

# An Omnigent session's Claude gets its settings from its bridge directory.
# The account it starts with is recorded there, so the composer can show it.
bridge_dir=""
previous=""
for argument in "$@"; do
    case "$previous=$argument" in
        --settings=*/claude-settings.json) bridge_dir="${argument%/*}" ;;
        *=--settings=*/claude-settings.json) bridge_dir="${argument#--settings=}"; bridge_dir="${bridge_dir%/*}" ;;
    esac
    previous="$argument"
done
if [ -n "$bridge_dir" ] && [ -d "$bridge_dir" ]; then
    subscription_token="$(/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py claude --record "$bridge_dir/omnigent-subscription")"
else
    subscription_token="$(/opt/venv/bin/python /usr/local/lib/omnigent/subscriptions.py claude)"
fi
if [ -n "$subscription_token" ]; then
    export CLAUDE_CODE_OAUTH_TOKEN="$subscription_token"
fi
unset subscription_token bridge_dir previous

# The variables of the repositories this session started with.
# shellcheck source=SCRIPTDIR/repo-env/repo-env.sh
. /usr/local/lib/omnigent/repo-env.sh

# The instructions of the repositories this session started with, after the
# runner's own, once their setup scripts are done or have had their time.
# shellcheck source=SCRIPTDIR/repo-env/agent-instructions.sh
. /usr/local/lib/omnigent/agent-instructions.sh
omnigent_wait_for_repo_setup
omnigent_agent_instructions /etc/claude-code/CLAUDE.md \
    || echo "claude-wrapper: could not add the repository instructions to /etc/claude-code/CLAUDE.md" >&2

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
