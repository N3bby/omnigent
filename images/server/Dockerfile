ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes@sha256:ed3ae35d30572dcd929ba4e1edefdcec53ef178ab2f8147d11a8f2f33dded123
ARG WEB_BUILDER_IMAGE=docker.io/library/node:22.23.3-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

# The upstream image ships the web UI prebuilt, so a web patch needs a rebuild
# from the pinned upstream source. The commit id pins the checkout's content.
FROM ${WEB_BUILDER_IMAGE} AS web
ARG OMNIGENT_COMMIT=c8b9b85f822f2c9203ff995c10f3cc49d064bbe5
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
# omnigent_repo_env and omnigent_repo_env_api are ours, not upstream's:
# 0004-repo-env.patch and 0006-repo-env-routes.patch import them, and
# scripts/setup-repo-env uses the same omnigent_repo_env to name the Secrets.
COPY patches/ /tmp/omnigent-patches/
COPY omnigent_repo_env.py omnigent_repo_env_api.py /tmp/omnigent-modules/
RUN set -eu; \
    site="$(python -c 'import omnigent, pathlib; print(pathlib.Path(omnigent.__file__).parents[1])')"; \
    purelib="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    install -m 0644 /tmp/omnigent-modules/*.py "$purelib/"; \
    rm -r /tmp/omnigent-modules; \
    apt-get update; \
    apt-get install -y --no-install-recommends patch; \
    for p in /tmp/omnigent-patches/*.patch; do \
        patch -d "$site" -p1 --forward --fuzz=0 --no-backup-if-mismatch < "$p"; \
    done; \
    apt-get purge -y --auto-remove patch; \
    rm -rf /var/lib/apt/lists/* /tmp/omnigent-patches; \
    python -m compileall -q -f "$site/omnigent/server" "$site/omnigent/onboarding/sandboxes" "$purelib/omnigent_repo_env.py" "$purelib/omnigent_repo_env_api.py"; \
    python -c 'import omnigent.server.sandbox_catalog_fallback, omnigent.server.routes.sessions, omnigent.onboarding.sandboxes.agent_sandbox, omnigent.server.app'

# The server serves the SPA from OMNIGENT_WEB_UI_DIST instead of its package data.
COPY --from=web /src/omnigent/server/static/web-ui /opt/omnigent-web-ui
ENV OMNIGENT_WEB_UI_DIST=/opt/omnigent-web-ui
