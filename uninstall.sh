#!/usr/bin/env bash
set -euo pipefail

if [ -z "${HOME:-}" ]; then
    echo "HOME is not set; refusing to resolve user data paths." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [--purge] [--yes]

Without --purge, remove only the desktop launcher and icon. The application
data, configuration, database, audio, transcripts, reports, exports, and logs
are kept.

--purge  Also delete all app-managed XDG data for this user. This is
         irreversible and includes recordings and reports.
--yes    Do not ask for confirmation with --purge.
EOF
}

PURGE=0
ASSUME_YES=0
for argument in "$@"; do
    case "$argument" in
        --purge) PURGE=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *)
            echo "Unknown option: $argument" >&2
            usage >&2
            exit 2
            ;;
    esac
done

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"

DATA_DIR="$DATA_HOME/audio-report-ai"
CONFIG_DIR="$CONFIG_HOME/audio-report-ai"
CACHE_DIR="$CACHE_HOME/audio-report-ai"
STATE_DIR="$STATE_HOME/audio-report-ai"
DESKTOP_FILE="$DATA_HOME/applications/audio-report-ai.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/audio-report-ai.svg"

for file in "$DESKTOP_FILE" "$ICON_FILE"; do
    if [ -e "$file" ] || [ -L "$file" ]; then
        rm -f -- "$file"
        echo "Removed: $file"
    fi
done

if [ "$PURGE" -eq 0 ]; then
    echo "Application data was kept:"
    echo "  $DATA_DIR"
    echo "  $CONFIG_DIR"
    echo "  $CACHE_DIR"
    echo "  $STATE_DIR"
    echo "Run ./uninstall.sh --purge to delete it permanently."
    exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    echo "This will permanently delete all app-managed data for this user:"
    echo "  $DATA_DIR"
    echo "  $CONFIG_DIR"
    echo "  $CACHE_DIR"
    echo "  $STATE_DIR"
    read -r -p "Type yes to continue: " answer
    if [ "$answer" != "yes" ]; then
        echo "Purge cancelled."
        exit 1
    fi
fi

for directory in "$DATA_DIR" "$CONFIG_DIR" "$CACHE_DIR" "$STATE_DIR"; do
    case "$directory" in
        ""|"/"|"$HOME"|"$HOME/"|"$DATA_HOME"|"$CONFIG_HOME"|"$CACHE_HOME"|"$STATE_HOME")
            echo "Refusing to delete unsafe path: $directory" >&2
            exit 1
            ;;
    esac
    case "$directory" in
        */audio-report-ai) ;;
        *)
            echo "Refusing to delete unexpected path: $directory" >&2
            exit 1
            ;;
    esac
    if [ -e "$directory" ] || [ -L "$directory" ]; then
        rm -rf -- "$directory"
        echo "Deleted: $directory"
    fi
done

echo "Purge complete. The source checkout and .venv were not modified."
echo "Remove the checkout and .venv manually if you no longer need them: $ROOT"
