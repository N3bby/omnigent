ARG OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:f3dee09609ccecbebd6841c30b33f0160eaf3d2758d535b2e3993c1a3df5664a
FROM ${OMNIGENT_HOST_IMAGE}

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        podman podman-docker crun \
    && rm -rf /var/lib/apt/lists/* \
    && touch /etc/containers/nodocker \
    && podman --version
RUN install -d -m 1777 /etc/claude-code
COPY containers/containers.conf /etc/containers/containers.conf.d/50-omnigent.conf
COPY containers/storage.conf /etc/containers/storage.conf

WORKDIR /opt/omnigent-agent-clis
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/claude /usr/local/bin/claude-real \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/codex /usr/local/bin/codex-real \
    && claude-real --version \
    && codex-real --version

COPY --chmod=0755 codex-wrapper.sh /usr/local/bin/codex
COPY --chmod=0755 claude-wrapper.sh /usr/local/bin/claude
