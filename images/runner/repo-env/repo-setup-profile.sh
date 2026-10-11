# shellcheck shell=sh
# Runner Pods start through a root login shell, so this runs the setup
# scripts of the session's repositories at Pod start (omnigent-repo-setup).
# Only the host container has OMNIGENT_REPO_CHECKOUTS, from the server; it
# lists where each repository is checked out. Writing the list here, before
# the Omnigent host starts, means the agent wrappers always find it. Later
# login shells exit on the service's lock.
if [ "$(id -u)" = 0 ] && [ -n "${OMNIGENT_REPO_CHECKOUTS:-}" ] && [ -n "${OMNIGENT_HOST_ID:-}" ] \
    && command -v omnigent-repo-setup >/dev/null 2>&1; then
    if [ ! -e /run/omnigent-repo-setup/checkouts ] && mkdir -p /run/omnigent-repo-setup; then
        printf '%s\n' "$OMNIGENT_REPO_CHECKOUTS" >/run/omnigent-repo-setup/checkouts.$$ \
            && mv -f /run/omnigent-repo-setup/checkouts.$$ /run/omnigent-repo-setup/checkouts
    fi
    setsid omnigent-repo-setup </dev/null >>/run/omnigent-repo-setup.log 2>&1 &
fi
