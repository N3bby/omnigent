# shellcheck shell=sh
# Exports the variables of the repositories this session started with. The
# server mounts each repository's Secret at /run/omnigent/repo-env/<name>/,
# one file per variable (see docs/features/repo-env.md). The agent wrappers
# source this before starting the agent, and /etc/profile.d does for
# Tailscale SSH logins. A Pod without any has nothing to export.
for omnigent_repo_env_file in "${OMNIGENT_REPO_ENV_DIR:-/run/omnigent/repo-env}"/*/*; do
    # Kubernetes' ..data links start with a dot, so the glob skips them.
    [ -f "$omnigent_repo_env_file" ] || continue
    omnigent_repo_env_name=${omnigent_repo_env_file##*/}
    case $omnigent_repo_env_name in
        '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;;
    esac
    # The x keeps trailing newlines, which $(...) would otherwise strip.
    omnigent_repo_env_value=$(cat "$omnigent_repo_env_file" && printf x) || continue
    export "$omnigent_repo_env_name=${omnigent_repo_env_value%x}"
done
unset omnigent_repo_env_file omnigent_repo_env_name omnigent_repo_env_value
