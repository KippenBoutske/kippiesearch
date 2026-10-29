#!/bin/bash
set -euo pipefail

export PYTHONUNBUFFERED=1
export KIPPIESEARCH_OPTIONS="${KIPPIESEARCH_OPTIONS:-/data/options.json}"
export KIPPIESEARCH_DATA_DIR="${KIPPIESEARCH_DATA_DIR:-/data}"
export PYTHONPATH="${PYTHONPATH:-/opt/kippiesearch}"

# HA schrijft opties naar /data/options.json. Zonder dat bestand: defaults.
if [ ! -f "$KIPPIESEARCH_OPTIONS" ]; then
  mkdir -p "$(dirname "$KIPPIESEARCH_OPTIONS")"
  echo '{}' > "$KIPPIESEARCH_OPTIONS"
fi

mkdir -p /data/models /data/itslearning /data/lancedb

exec python -m kippiesearch
