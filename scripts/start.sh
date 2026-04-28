#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Defaults
VAULT_PATH="${1:-./vault}"
PORT="${2:-8184}"
HOST="${3:-0.0.0.0}"

usage() {
    echo "Usage: $0 [vault_path] [port] [host]"
    echo ""
    echo "  vault_path  Path to the vault (default: ./vault)"
    echo "  port        Port to bind (default: 8184)"
    echo "  host        Host to bind (default: 0.0.0.0)"
    echo ""
    echo "Examples:"
    echo "  $0 ~/SecondBrain"
    echo "  $0 ~/SecondBrain 9000"
    echo "  $0 ~/SecondBrain 9000 127.0.0.1"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

# Resolve vault path
VAULT_PATH="$(cd "$PROJECT_DIR" && realpath -m "$VAULT_PATH" 2>/dev/null || echo "$VAULT_PATH")"

# Find python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python not found. Run ./scripts/install.sh first."
    exit 1
fi

# Check if secondbrain is installed
if ! $PYTHON -c "import secondbrain" &>/dev/null; then
    echo "VaultForge not installed. Running install first..."
    "$SCRIPT_DIR/install.sh"
fi

# Initialize vault if it doesn't exist
if [ ! -d "$VAULT_PATH/system" ]; then
    echo "Initializing new vault at $VAULT_PATH..."
    $PYTHON -m secondbrain.cli init "$VAULT_PATH"
    echo ""
fi

# Kill any existing server on the same port
if command -v lsof &>/dev/null; then
    existing=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$existing" ]; then
        echo "Stopping existing server on port $PORT (PID: $existing)..."
        kill $existing 2>/dev/null || true
        sleep 1
    fi
fi

echo "=== VaultForge ==="
echo "  Vault:  $VAULT_PATH"
echo "  URL:    http://$HOST:$PORT"
echo "  Stop:   Ctrl+C"
echo ""

# Run the server
exec $PYTHON -m secondbrain.cli serve --vault "$VAULT_PATH" --host "$HOST" --port "$PORT"
