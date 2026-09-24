ARG OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:f3dee09609ccecbebd6841c30b33f0160eaf3d2758d535b2e3993c1a3df5664a
FROM ${OMNIGENT_HOST_IMAGE}

USER root
WORKDIR /opt/omnigent-agent-clis
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /opt/omnigent-agent-clis/node_modules/.bin/codex /usr/local/bin/codex-real \
    && claude --version \
    && codex-real --version

COPY --chmod=0755 codex-wrapper.sh /usr/local/bin/codex

