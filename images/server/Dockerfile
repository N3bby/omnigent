ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes@sha256:ed3ae35d30572dcd929ba4e1edefdcec53ef178ab2f8147d11a8f2f33dded123
FROM ${OMNIGENT_SERVER_IMAGE}

ARG HVAC_VERSION=2.4.0

RUN python -m pip install --no-cache-dir "hvac==${HVAC_VERSION}" \
    && python -c 'import hvac; print("Vault client ready")'

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
    python -m compileall -q -f "$site/omnigent/server"; \
    python -c 'import omnigent.server.sandbox_catalog_fallback, omnigent.server.routes.sessions'
