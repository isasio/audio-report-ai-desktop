#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DROP_IN="$ROOT/packaging/ollama-local-only.conf"

if [ ! -f "$DROP_IN" ]; then
    echo "Missing Ollama drop-in: $DROP_IN" >&2
    exit 1
fi

USER_UNIT=0
SYSTEM_UNIT=0
if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user list-unit-files ollama.service >/dev/null 2>&1; then
        USER_UNIT=1
    fi
    if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
        SYSTEM_UNIT=1
    fi
fi

if [ "$USER_UNIT" -eq 1 ]; then
    TARGET="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ollama.service.d"
    install -d -m 700 "$TARGET"
    install -m 600 "$DROP_IN" "$TARGET/99-audio-report-ai.conf"
    systemctl --user daemon-reload
    systemctl --user restart ollama
    echo "Installed user Ollama drop-in: $TARGET/99-audio-report-ai.conf"
    echo "Ollama is configured for 127.0.0.1:11434 with cloud disabled."
    exit 0
fi

if [ "$SYSTEM_UNIT" -eq 1 ]; then
    TARGET="/etc/systemd/system/ollama.service.d"
    echo "Installing a system-wide Ollama drop-in. sudo may ask for a password."
    sudo install -d -m 755 "$TARGET"
    sudo install -m 644 "$DROP_IN" "$TARGET/99-audio-report-ai.conf"
    sudo systemctl daemon-reload
    sudo systemctl restart ollama
    echo "Installed system Ollama drop-in: $TARGET/99-audio-report-ai.conf"
    echo "Ollama is configured for 127.0.0.1:11434 with cloud disabled."
    exit 0
fi

cat <<'EOF'
No systemd ollama.service was found, so nothing was changed.
If you start Ollama manually, use:

  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve

The application itself always connects only to http://127.0.0.1:11434.
EOF
