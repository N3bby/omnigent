ARG OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:f3dee09609ccecbebd6841c30b33f0160eaf3d2758d535b2e3993c1a3df5664a
FROM ${OMNIGENT_HOST_IMAGE}

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        podman podman-docker crun aardvark-dns catatonit docker-compose curl \
    && rm -rf /var/lib/apt/lists/* \
    && touch /etc/containers/nodocker \
    && podman --version \
    && docker-compose version
RUN install -d -m 1777 /etc/claude-code
COPY containers/containers.conf /etc/containers/containers.conf.d/50-omnigent.conf
COPY containers/storage.conf /etc/containers/storage.conf
COPY --chmod=0755 containers/podman-service.sh /usr/local/bin/omnigent-podman-service
COPY --chmod=0755 containers/docker-compose.sh /usr/local/bin/docker-compose
COPY containers/podman-service-profile.sh /etc/profile.d/omnigent-podman-service.sh

WORKDIR /opt/omnigent-agent-clis
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/claude /usr/local/bin/claude-real \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/codex /usr/local/bin/codex-real \
    && claude-real --version \
    && codex-real --version

COPY --chmod=0755 codex-wrapper.sh /usr/local/bin/codex
COPY --chmod=0755 claude-wrapper.sh /usr/local/bin/claude

# Each runner gets its own Codex home and shares only the login. Codex keeps
# SQLite databases in its home, and on the shared volume Pods in different
# user namespaces left files that the others couldn't open.
COPY --chmod=0644 codex-config.toml /opt/codex-home/config.toml
RUN ln -s /mnt/codex-home/auth.json /opt/codex-home/auth.json

# --fuzz=0 fails the build when upstream code under a patch has moved.
COPY patches/ /tmp/omnigent-patches/
RUN set -eu; \
    site="$(python -c 'import omnigent, pathlib; print(pathlib.Path(omnigent.__file__).parents[1])')"; \
    apt-get update; \
    apt-get install -y --no-install-recommends patch; \
    for p in /tmp/omnigent-patches/*.patch; do \
        patch -d "$site" -p1 --forward --fuzz=0 --no-backup-if-mismatch < "$p"; \
    done; \
    apt-get purge -y --auto-remove patch; \
    rm -rf /var/lib/apt/lists/* /tmp/omnigent-patches; \
    python -m compileall -q -f "$site/omnigent/harnesses"; \
    python -c 'import omnigent.harnesses.claude_native.forwarder, omnigent.harnesses.codex_native.forwarder'
