ARG OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:f3dee09609ccecbebd6841c30b33f0160eaf3d2758d535b2e3993c1a3df5664a
FROM ${OMNIGENT_HOST_IMAGE}

ARG JETBRAINS_IDEA_BUILD=263.6259.32
ARG JETBRAINS_IDEA_DOWNLOAD_URL=https://download.jetbrains.com/idea/idea-263.6259.32.tar.gz
ARG JETBRAINS_IDEA_SHA256=bdabdb667dc6a1dd029368276ded56bf54c254bb65b65ae8699824731ef25bea
ARG JETBRAINS_IDEA_PATH=/opt/jetbrains/intellij

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        podman podman-docker crun aardvark-dns catatonit docker-compose curl \
        libfontconfig1 libfreetype6 libxrender1 libxtst6 libxi6 \
    && rm -rf /var/lib/apt/lists/* \
    && touch /etc/containers/nodocker \
    && podman --version \
    && docker-compose version
# Host-based Gateway links can connect without a client-local SSH config only
# when the IDE is already installed. Its writable state stays in the user's home.
RUN curl -fsSLo /tmp/idea.tar.gz "${JETBRAINS_IDEA_DOWNLOAD_URL}" \
    && echo "${JETBRAINS_IDEA_SHA256}  /tmp/idea.tar.gz" | sha256sum -c - \
    && mkdir -p "${JETBRAINS_IDEA_PATH}" \
    && tar -xzf /tmp/idea.tar.gz -C "${JETBRAINS_IDEA_PATH}" --strip-components=1 \
    && rm /tmp/idea.tar.gz \
    && test -x "${JETBRAINS_IDEA_PATH}/bin/remote-dev-server.sh" \
    && python -c 'import json, pathlib, sys; p = pathlib.Path(sys.argv[1]); info = json.loads((p / "product-info.json").read_text()); assert info["buildNumber"] == sys.argv[2], info; assert info["productCode"] == "IU", info' "${JETBRAINS_IDEA_PATH}" "${JETBRAINS_IDEA_BUILD}"
RUN install -d -m 1777 /etc/claude-code
ARG TAILSCALE_VERSION=1.102.5
ARG TAILSCALE_SHA256=65e6d7f19ad7e1c87d20c2a21e92f38a96795cb897af54b04536590e1c148d12
RUN curl -fsSLo /tmp/tailscale.tgz "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" \
    && echo "${TAILSCALE_SHA256}  /tmp/tailscale.tgz" | sha256sum -c - \
    && tar -xzf /tmp/tailscale.tgz -C /usr/local/bin --strip-components=1 \
        "tailscale_${TAILSCALE_VERSION}_amd64/tailscale" "tailscale_${TAILSCALE_VERSION}_amd64/tailscaled" \
    && rm /tmp/tailscale.tgz \
    && tailscale version
ARG MISE_VERSION=2026.10.3
ARG MISE_SHA256=8d48bc510b7d844fad0bc7156c0855c2a1e7230c51e498a7f6b63b021f8b57c5
RUN curl -fsSLo /usr/local/bin/mise "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64" \
    && echo "${MISE_SHA256}  /usr/local/bin/mise" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/mise \
    && mise --version
COPY containers/containers.conf /etc/containers/containers.conf.d/50-omnigent.conf
COPY containers/storage.conf /etc/containers/storage.conf
COPY --chmod=0755 containers/podman-service.sh /usr/local/bin/omnigent-podman-service
COPY --chmod=0755 containers/docker-compose.sh /usr/local/bin/docker-compose
COPY containers/podman-service-profile.sh /etc/profile.d/omnigent-podman-service.sh
COPY --chmod=0755 tailscale/tailscale-service.sh /usr/local/bin/omnigent-tailscale
COPY --chmod=0755 tailscale/tailscale-logout.sh /usr/local/bin/omnigent-tailscale-logout
COPY tailscale/tailscale-service-profile.sh /etc/profile.d/omnigent-tailscale.sh
COPY mise/mise-profile.sh /etc/profile.d/zz-omnigent-mise.sh
# Claude Code's managed instructions; Codex gets the same file as AGENTS.md.
COPY --chmod=0644 agent-instructions.md /etc/claude-code/CLAUDE.md

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
COPY --chmod=0644 agent-instructions.md /opt/codex-home/AGENTS.md
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
    python -m compileall -q -f "$site/omnigent/harnesses" "$site/omnigent/git_credential_github.py"; \
    python -c 'import omnigent.harnesses.claude_native.forwarder, omnigent.harnesses.codex_native.forwarder, omnigent.git_credential_github'

# Shell setup for Tailscale SSH logins. It's personal taste: change
# images/runner/shell/ to make it your own (see docs/features/tailscale.md).
COPY shell/ /tmp/omnigent-shell/
RUN /tmp/omnigent-shell/install.sh && rm -rf /tmp/omnigent-shell
