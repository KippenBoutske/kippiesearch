# KippieSearch add-on — Debian slim, niet Alpine: onnxruntime heeft glibc-wheels voor aarch64.
# Primair doel: Raspberry Pi 5 (aarch64). Ook amd64 voor lokale tests.
FROM python:3.12-slim-bookworm

ARG BUILD_ARCH=aarch64
ARG BUILD_VERSION=0.1.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/models/hf \
    HUGGINGFACE_HUB_CACHE=/data/models/hf \
    KIPPIESEARCH_DATA_DIR=/data \
    KIPPIESEARCH_OPTIONS=/data/options.json \
    PYTHONPATH=/opt/kippiesearch

LABEL \
  io.hass.name="KippieSearch" \
  io.hass.description="Lokale privacy-first semantic search + MCP-server" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="aarch64|amd64" \
  org.opencontainers.image.source="https://github.com/KippenBoutske/kippiesearch"

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    tini \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/kippiesearch
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
  && pip install -r /tmp/requirements.txt

COPY src/kippiesearch /opt/kippiesearch/kippiesearch
COPY run.sh /run.sh
RUN chmod a+x /run.sh \
  && mkdir -p /data

EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/run.sh"]
