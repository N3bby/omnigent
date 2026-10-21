ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes@sha256:eba3422ee55ef946bff7c895f458258fbaf4a5e4244ef38aa66caec8fefe9198
ARG WEB_BUILDER_IMAGE=docker.io/library/node:22.23.3-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

# The upstream image ships the web UI prebuilt, so a web patch needs a rebuild
# from the pinned upstream source. The commit id pins the checkout's content.
FROM ${WEB_BUILDER_IMAGE} AS web
ARG OMNIGENT_COMMIT=a8676d23e88df7918f4f8ef1c7c4e3186e710354
ARG PNPM_VERSION=11.15.1
ARG JETBRAINS_IDEA_PATH=/opt/jetbrains/intellij
ENV VITE_OMNIGENT_GATEWAY_IDEA_PATH=${JETBRAINS_IDEA_PATH}
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git patch \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init -q . \
    && git fetch -q --depth 1 https://github.com/omnigent-ai/omnigent.git "$OMNIGENT_COMMIT" \
    && git -c advice.detachedHead=false checkout -q FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$OMNIGENT_COMMIT"
# --fuzz=0 fails the build when upstream code under a patch has moved.
COPY web-patches/ /tmp/web-patches/
RUN set -eu; \
    for p in /tmp/web-patches/*.patch; do \
        patch -p1 --forward --fuzz=0 --no-backup-if-mismatch < "$p"; \
    done
RUN npm install -g "pnpm@${PNPM_VERSION}" \
    && pnpm install --frozen-lockfile --filter web \
    && pnpm --filter web run build \
    && test -f omnigent/server/static/web-ui/index.html

FROM ${OMNIGENT_SERVER_IMAGE}

ARG HVAC_VERSION=2.4.0

RUN python -m pip install --no-cache-dir "hvac==${HVAC_VERSION}" \
    && python -c 'import hvac; print("Vault client ready")'

# --fuzz=0 fails the build when upstream code under a patch has moved.
# The installed package has no tests/, so each patch's tests/ diffs are left
# out here; check-patches and check-patched-tests apply them to a checkout.
# omnigent_repo_env and omnigent_repo_env_api are ours, not upstream's:
# 0004-repo-env.patch, 0006-repo-env-routes.patch, 0010-repo-git-token.patch
# and 0012-repo-checkouts.patch import them. So are omnigent_startup and
# omnigent_startup_api, for 0016-startup-progress.patch, and
# omnigent_mcp_servers and omnigent_mcp_servers_api, for 0019-mcp-servers.patch,
# and omnigent_progress and omnigent_progress_api, for 0022-agent-progress.patch.
COPY patches/ /tmp/omnigent-patches/
COPY omnigent_repo_env.py omnigent_repo_env_api.py /tmp/omnigent-modules/
COPY omnigent_subscriptions.py omnigent_subscriptions_api.py omnigent_subscription_login.py /tmp/omnigent-modules/
COPY omnigent_startup.py omnigent_startup_api.py /tmp/omnigent-modules/
COPY omnigent_mcp_servers.py omnigent_mcp_servers_api.py /tmp/omnigent-modules/
COPY omnigent_progress.py omnigent_progress_api.py /tmp/omnigent-modules/
RUN set -eu; \
    site="$(python -c 'import omnigent, pathlib; print(pathlib.Path(omnigent.__file__).parents[1])')"; \
    purelib="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    install -m 0644 /tmp/omnigent-modules/*.py "$purelib/"; \
    rm -r /tmp/omnigent-modules; \
    apt-get update; \
    apt-get install -y --no-install-recommends patch; \
    for p in /tmp/omnigent-patches/*.patch; do \
        python -c 'import re, sys; print("".join(c for c in re.split(r"(?m)^(?=diff --git )", sys.stdin.read()) if not c.startswith("diff --git a/tests/")), end="")' < "$p" \
            | patch -d "$site" -p1 --forward --fuzz=0 --no-backup-if-mismatch; \
    done; \
    apt-get purge -y --auto-remove patch; \
    rm -rf /var/lib/apt/lists/* /tmp/omnigent-patches; \
    python -m compileall -q -f "$site/omnigent/server" "$site/omnigent/onboarding/sandboxes" "$purelib/omnigent_repo_env.py" "$purelib/omnigent_repo_env_api.py" "$purelib/omnigent_startup.py" "$purelib/omnigent_startup_api.py" "$purelib/omnigent_mcp_servers.py" "$purelib/omnigent_mcp_servers_api.py" "$purelib/omnigent_progress.py" "$purelib/omnigent_progress_api.py"; \
    python -c 'import omnigent.server.sandbox_catalog_fallback, omnigent.server.routes.sessions, omnigent.onboarding.sandboxes.agent_sandbox, omnigent.server.app'

# The server serves the SPA from OMNIGENT_WEB_UI_DIST instead of its package data.
COPY --from=web /src/omnigent/server/static/web-ui /opt/omnigent-web-ui
ENV OMNIGENT_WEB_UI_DIST=/opt/omnigent-web-ui
