ARG OMNIGENT_SERVER_IMAGE=ghcr.io/omnigent-ai/omnigent-server-kubernetes@sha256:ed3ae35d30572dcd929ba4e1edefdcec53ef178ab2f8147d11a8f2f33dded123
FROM ${OMNIGENT_SERVER_IMAGE}

ARG HVAC_VERSION=2.4.0

RUN python -m pip install --no-cache-dir "hvac==${HVAC_VERSION}" \
    && python -c 'import hvac; print("Vault client ready")'
