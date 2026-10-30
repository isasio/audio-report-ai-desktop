#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "No virtual environment found. Run ./install.sh first." >&2
    exit 1
fi

export PYTHONNOUSERSITE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

# The GUI launch path checks local readiness and tries to start a user-level
# Ollama unit if one exists. It never installs anything or downloads models.
if [ "$#" -eq 0 ]; then
    if command -v systemctl >/dev/null 2>&1 && \
       systemctl --user list-unit-files ollama.service >/dev/null 2>&1; then
        systemctl --user start ollama >/dev/null 2>&1 || true
    fi
    "$ROOT/.venv/bin/python" -m audio_report_ai --runtime-check || true
fi

exec "$ROOT/.venv/bin/python" -m audio_report_ai "$@"
