# shellcheck shell=sh
# Gives the agent the instructions of the repositories this session started
# with, appended to the runner's own, and tells it how their setup scripts
# went (see docs/features/repo-instructions.md and repo-setup-script.md).
# The agent wrappers source this, then call omnigent_wait_for_repo_setup and
# omnigent_agent_instructions with the file their agent reads. Neither fails
# the wrapper: without anything to add, the agent gets the runner's
# instructions as they are.

omnigent_repo_env_dir=${OMNIGENT_REPO_ENV_DIR:-/run/omnigent/repo-env}
omnigent_repo_setup_run=${OMNIGENT_REPO_SETUP_RUN:-/run/omnigent-repo-setup}
omnigent_repo_setup_state=${OMNIGENT_REPO_SETUP_STATE:-/home/omnigent/.local/state/omnigent-repo-setup}

# Whether a repository of this session has a setup script that hasn't
# finished in this Pod yet.
omnigent_repo_setup_pending() {
    [ -f "$omnigent_repo_setup_run/checkouts" ] && [ ! -e "$omnigent_repo_setup_run/done" ] || return 1
    while IFS=$(printf '\t') read -r secret _ _; do
        [ -n "$secret" ] && [ -f "$omnigent_repo_env_dir/$secret/setup.sh" ] && return 0
    done <"$omnigent_repo_setup_run/checkouts"
    return 1
}

# Waits for the setup scripts, for at most OMNIGENT_REPO_SETUP_WAIT seconds:
# Omnigent gives Codex a minute to start. A longer script keeps running, and
# the instructions say so.
omnigent_wait_for_repo_setup() {
    omnigent_repo_setup_pending || return 0
    limit=${OMNIGENT_REPO_SETUP_WAIT:-45}
    echo "Waiting up to ${limit}s for the repository's setup script..." >&2
    waited=0
    while [ ! -e "$omnigent_repo_setup_run/done" ] && [ "$waited" -lt "$limit" ]; do
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

# Prints the section for one repository: its instructions and how its setup
# script went, or nothing when it has neither.
omnigent_repo_section() {
    secret=$1
    repository=$2
    checkout=$3
    dir=$omnigent_repo_env_dir/$secret
    instructions=$dir/agent-instructions.md
    script=$dir/setup.sh
    [ -s "$instructions" ] || [ -f "$script" ] || return 0
    printf '\n## %s\n\nChecked out at `%s`.\n' "$repository" "$checkout"
    if [ -s "$instructions" ]; then
        printf '\n### Instructions\n\n'
        printf '%s\n' "These are the user's own instructions for this repository. Where they disagree with the runner's instructions above or with the repository's own, follow these."
        printf '\n'
        cat "$instructions"
        # The file may not end with a newline.
        [ -z "$(tail -c 1 "$instructions")" ] || printf '\n'
    fi
    if [ -f "$script" ]; then
        name=${checkout##*/}
        log=$omnigent_repo_setup_state/$name.log
        status=$(cat "$omnigent_repo_setup_state/$name.exit" 2>/dev/null) || status=
        printf '\n### Setup script\n\n'
        printf 'Omnigent runs the setup script `%s` in the checkout once, before the agent starts. ' "$script"
        case $status in
            0) printf 'It finished successfully. Its output is in `%s`.\n' "$log" ;;
            '') printf 'It is still running, so what it sets up may not be ready yet. Its output goes to `%s`, and `%s` gets its exit status when it finishes. Check it before relying on what it sets up.\n' \
                "$log" "$omnigent_repo_setup_state/$name.exit" ;;
            *) printf 'It failed with exit status %s, so what it sets up may be missing. Its output is in `%s`. Read it, and tell the user it failed when that matters for what they ask.\n' \
                "$status" "$log" ;;
        esac
    fi
}

# Writes the runner's instructions to $1, plus a section for each repository
# with instructions or a setup script. Rewrites $1 only when it changes,
# atomically, because several agents may start at once.
omnigent_agent_instructions() {
    target=$1
    base=${OMNIGENT_AGENT_INSTRUCTIONS:-/usr/local/share/omnigent/agent-instructions.md}
    [ -f "$base" ] || return 0
    sections=
    if [ -f "$omnigent_repo_setup_run/checkouts" ]; then
        sections=$(
            while IFS=$(printf '\t') read -r secret repository checkout; do
                [ -n "$secret" ] && omnigent_repo_section "$secret" "$repository" "$checkout"
            done <"$omnigent_repo_setup_run/checkouts"
            printf x
        )
        sections=${sections%x}
    fi
    [ -w "${target%/*}" ] || return 1
    tmp=$target.omnigent.$$
    {
        cat "$base"
        if [ -n "$sections" ]; then
            printf '\n# Repository settings from Omnigent\n\n'
            printf '%s\n' "Omnigent appended this to the runner's instructions, from what the user saved for this session's repositories in Omnigent's Settings, under Repository variables. It isn't in the repositories, so don't copy it into a repository or commit it."
            printf '%s' "$sections"
        fi
    } >"$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
    if cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
    else
        mv -f "$tmp" "$target" 2>/dev/null || { rm -f "$tmp"; return 1; }
    fi
}
