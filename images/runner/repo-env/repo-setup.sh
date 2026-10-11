#!/bin/sh
# Runs the setup script of each repository this session started with, in its
# checkout, once per checkout (see docs/features/repo-setup-script.md). The
# script is the repository's setup.sh, mounted with its variables at
# /run/omnigent/repo-env/<Secret name>/. Its output and exit status go to
# ~/.local/state/omnigent-repo-setup/<checkout>.log and .exit, which survive
# the Pod stopping, so a woken Pod doesn't run it again.
#
# Started from every root login shell (repo-setup-profile.sh), which also
# writes the list of checkouts; the lock keeps it to one copy. When it's done,
# it touches $run/done, which the agent wrappers wait for
# (agent-instructions.sh).
set -u

env_dir=${OMNIGENT_REPO_ENV_DIR:-/run/omnigent/repo-env}
run=${OMNIGENT_REPO_SETUP_RUN:-/run/omnigent-repo-setup}
state=${OMNIGENT_REPO_SETUP_STATE:-/home/omnigent/.local/state/omnigent-repo-setup}
tab=$(printf '\t')

mkdir -p "$run"
exec 9>"$run/lock"
flock -n 9 || exit 0
[ -e "$run/done" ] && exit 0

# Runs one script in its checkout, with the environment an agent's login
# shell gets: the repository's variables and mise's shims, from
# /etc/profile.d. It doesn't get the Pod's own environment, which holds the
# host's launch token. A script with a #! line runs with that interpreter,
# any other with bash.
run_script() {
    script=$1
    checkout=$2
    copy=$(mktemp "${TMPDIR:-/tmp}/omnigent-repo-setup.XXXXXX") || return 1
    cp "$script" "$copy" && chmod 700 "$copy" || { rm -f "$copy"; return 1; }
    if [ "$(head -c 2 "$copy")" = '#!' ]; then
        set -- "$copy"
    else
        set -- bash "$copy"
    fi
    (
        cd "$checkout" || exit 1
        env -i HOME=/home/omnigent USER="$(id -un)" LOGNAME="$(id -un)" SHELL=/bin/bash \
            LANG=C.UTF-8 TERM=dumb PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
            OMNIGENT_REPO_ENV_DIR="$env_dir" \
            bash -lc 'exec "$@"' bash "$@" </dev/null
    )
    status=$?
    rm -f "$copy"
    return "$status"
}

if [ -f "$run/checkouts" ]; then
    # The Podman service starts from the same login shell; give it a moment,
    # so a script can use docker straight away.
    waited=0
    while [ ! -S /run/podman/podman.sock ] && [ "$waited" -lt 15 ] \
        && command -v omnigent-podman-service >/dev/null 2>&1; do
        sleep 1
        waited=$((waited + 1))
    done
    while IFS=$tab read -r secret repository checkout; do
        script=$env_dir/$secret/setup.sh
        [ -n "$secret" ] && [ -f "$script" ] || continue
        name=${checkout##*/}
        if [ -e "$state/$name.exit" ]; then
            echo "omnigent-repo-setup: $repository already ran its setup script in $checkout"
            continue
        fi
        if [ ! -d "$checkout" ]; then
            echo "omnigent-repo-setup: $checkout isn't there; skipping the setup script of $repository" >&2
            continue
        fi
        mkdir -p "$state"
        echo "omnigent-repo-setup: running the setup script of $repository in $checkout"
        {
            echo "# omnigent-repo-setup: the setup script of $repository, in $checkout, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            run_script "$script" "$checkout"
            status=$?
            echo "# omnigent-repo-setup: exit status $status, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        } >"$state/$name.log" 2>&1
        printf '%s\n' "$status" >"$state/$name.exit"
        echo "omnigent-repo-setup: $repository finished with exit status $status; see $state/$name.log"
    done <"$run/checkouts"
fi
touch "$run/done"
