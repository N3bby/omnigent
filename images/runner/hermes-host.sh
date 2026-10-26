#!/bin/sh
# Runs the always-on Omnigent host for Hermes sessions, in the
# omnigent-hermes-host Deployment (docs/features/hermes-host.md). Started
# through a root login shell, like runner Pods, so the Pod joins the tailnet
# that leads to the Hermes machine.
#
# The host signs in as you with the refresh token `mise run setup-hermes-host`
# stores. Without it, it waits instead of connecting.
set -eu

server=${OMNIGENT_SERVER_URL:?OMNIGENT_SERVER_URL is not set}

if [ -z "${OMNIGENT_REFRESH_TOKEN:-}" ]; then
    echo "omnigent-hermes-host: not signed in; run \`mise run setup-hermes-host\`." >&2
    exec sleep infinity
fi

# Where `omnigent login` would keep the sign-in. Expired on purpose: the host
# trades the refresh token for an access token when it connects, and again
# whenever that runs out.
python3 - "$server" <<'PY'
import os
import sys

from omnigent.cli_auth import store_token

store_token(
    sys.argv[1],
    token="",
    user_id=os.environ.get("OMNIGENT_HERMES_HOST_USER", ""),
    expires_at=0,
    refresh_token=os.environ["OMNIGENT_REFRESH_TOKEN"],
)
PY
unset OMNIGENT_REFRESH_TOKEN

exec omnigent host --server "$server" --non-interactive
