#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Install the isolated Python environment, check system dependencies, create
restrictive XDG directories, and install a desktop launcher. Model downloads
remain explicit and are never triggered implicitly.

Options:
  --check-only            Run checks only; do not sync dependencies or install files.
  --model NAME            Explicitly download a faster-whisper model (for example small
                          or large-v3) into the app data directory.
  --report-model NAME     Explicitly download an Ollama model (for example qwen3.5:4b).
  --with-diarization      Install the optional pyannote.audio dependencies. The gated
                          model still needs explicit terms acceptance and --setup-diarization.
  --configure-ollama      Install a systemd drop-in that binds Ollama to loopback and
                          disables cloud features. This may ask for sudo.
  --no-desktop            Do not install the .desktop launcher.
  --help                  Show this help.

Environment:
  ALLOW_UNSUPPORTED_UBUNTU=1  Permit installation on a non-24.04 Ubuntu release.
EOF
}

CHECK_ONLY=0
INSTALL_DESKTOP=1
WITH_DIARIZATION=0
CONFIGURE_OLLAMA=0
WHISPER_MODEL=""
REPORT_MODEL=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --check-only) CHECK_ONLY=1 ;;
        --model)
            shift
            [ "$#" -gt 0 ] || { echo "--model requires a value." >&2; exit 2; }
            WHISPER_MODEL="$1"
            ;;
        --report-model)
            shift
            [ "$#" -gt 0 ] || { echo "--report-model requires a value." >&2; exit 2; }
            REPORT_MODEL="$1"
            ;;
        --with-diarization) WITH_DIARIZATION=1 ;;
        --configure-ollama) CONFIGURE_OLLAMA=1 ;;
        --no-desktop) INSTALL_DESKTOP=0 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
        if [ "${ALLOW_UNSUPPORTED_UBUNTU:-0}" != "1" ]; then
            echo "This release targets Ubuntu 24.04; detected ${PRETTY_NAME:-unknown}." >&2
            echo "Set ALLOW_UNSUPPORTED_UBUNTU=1 only if you accept unsupported behaviour." >&2
            exit 1
        fi
        echo "Warning: unsupported Ubuntu release: ${PRETTY_NAME:-unknown}" >&2
    fi
else
    echo "Warning: /etc/os-release is unavailable; cannot verify Ubuntu 24.04." >&2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ and rerun ./install.sh" >&2
    exit 1
fi

missing=()
command -v ffmpeg >/dev/null 2>&1 || missing+=("ffmpeg")
command -v ffprobe >/dev/null 2>&1 || missing+=("ffprobe")

if command -v ldconfig >/dev/null 2>&1; then
    for library in libGL.so.1 libEGL.so.1 libxkbcommon-x11.so.0 libxcb-cursor.so.0 libportaudio.so.2; do
        if ! ldconfig -p 2>/dev/null | grep -F "$library" >/dev/null; then
            missing+=("$library")
        fi
    done
fi

if [ "${#missing[@]}" -gt 0 ]; then
    echo "Missing system dependencies: ${missing[*]}" >&2
    echo "Install them with:" >&2
    echo "  sudo apt-get update && sudo apt-get install -y ffmpeg libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libportaudio2" >&2
    exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU detected (optional; CPU fallback remains available):"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
else
    echo "No NVIDIA driver detected. CPU mode is fully supported."
fi

REQUIRED_KB=2097152
if [ -n "$WHISPER_MODEL" ]; then
    REQUIRED_KB=5242880
fi
if [ -n "$REPORT_MODEL" ]; then
    REQUIRED_KB=$((REQUIRED_KB + 5242880))
fi
AVAILABLE_KB="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
if [ -n "$AVAILABLE_KB" ] && [ "$AVAILABLE_KB" -lt "$REQUIRED_KB" ]; then
    echo "Not enough free disk space: $((AVAILABLE_KB / 1024)) MiB available, $((REQUIRED_KB / 1024)) MiB required." >&2
    exit 1
fi

echo "Audio / report model licenses:"
echo "  faster-whisper weights: upstream model card license; weights are not bundled."
echo "  Ollama Qwen models: Qwen model license; verify the exact model card."
echo "  Optional pyannote diarization: CC-BY-4.0 with gated terms."
echo "  Approximate sizes: small ASR ~0.5 GB, large-v3 ~3.1 GB, qwen3.5:4b ~3.3 GB."

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "System checks passed. No dependencies were installed and no models were downloaded."
    exit 0
fi

SYNC_ARGS=(--frozen --no-dev --python 3.12)
if [ "$WITH_DIARIZATION" -eq 1 ]; then
    SYNC_ARGS+=(--extra diarization)
fi
uv sync "${SYNC_ARGS[@]}"
PYTHON="$ROOT/.venv/bin/python"

"$PYTHON" -m audio_report_ai --self-check
"$PYTHON" -m audio_report_ai --check-audio

if [ -n "$WHISPER_MODEL" ]; then
    "$PYTHON" -m audio_report_ai --setup-models --model "$WHISPER_MODEL"
    "$PYTHON" -m audio_report_ai --check-models --model "$WHISPER_MODEL"
else
    echo "Speech model setup is explicit and was not downloaded. Example:"
    echo "  uv run python -m audio_report_ai --setup-models --model small"
fi

if [ -n "$REPORT_MODEL" ]; then
    "$PYTHON" -m audio_report_ai --setup-report-model --report-model "$REPORT_MODEL"
    "$PYTHON" -m audio_report_ai --check-ollama --report-model "$REPORT_MODEL"
elif command -v ollama >/dev/null 2>&1; then
    echo "Ollama found. Download the report model only when ready:"
    echo "  uv run python -m audio_report_ai --setup-report-model --report-model qwen3.5:4b"
else
    echo "Ollama is not installed. Report generation stays optional."
    echo "Install Ollama separately from https://ollama.com/download, then rerun with --report-model."
fi

if [ "$CONFIGURE_OLLAMA" -eq 1 ]; then
    "$ROOT/scripts/configure_ollama_local_only.sh"
else
    echo "For local-only Ollama, use OLLAMA_HOST=127.0.0.1:11434 and OLLAMA_NO_CLOUD=1,"
    echo "or rerun ./install.sh --configure-ollama to install the user/systemd drop-in helper."
fi

if [ "$WITH_DIARIZATION" -eq 1 ]; then
    echo "Diarization dependencies installed. The model is still gated and explicit:"
    echo "  accept the terms, then HF_TOKEN=<token> uv run python -m audio_report_ai --setup-diarization"
fi

if [ "$INSTALL_DESKTOP" -eq 1 ]; then
    "$PYTHON" -m audio_report_ai --install-desktop --project-root "$ROOT"
fi

"$PYTHON" -m audio_report_ai --offline-smoke-test

echo
"$PYTHON" -m audio_report_ai --runtime-check || true

echo
if [ "$INSTALL_DESKTOP" -eq 1 ]; then
    echo "Installation complete. Run ./run.sh or use the desktop launcher."
else
    echo "Installation complete. Run ./run.sh"
fi
echo "Runtime is offline: no model downloads or remote API calls are performed."
echo "Data:   ${XDG_DATA_HOME:-$HOME/.local/share}/audio-report-ai"
echo "Config: ${XDG_CONFIG_HOME:-$HOME/.config}/audio-report-ai"
echo "Remove the launcher with ./uninstall.sh; add --purge to delete app data."
