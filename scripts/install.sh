#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== VaultForge Install ==="
echo "Project: $PROJECT_DIR"
echo ""

# Check Python version
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$cmd"
            echo "Using $cmd ($version)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.11+ is required but not found."
    echo "Install it from https://www.python.org/downloads/"
    exit 1
fi

# Install in editable mode with dev dependencies
echo ""
echo "Installing VaultForge and dependencies..."
cd "$PROJECT_DIR"
$PYTHON -m pip install -e ".[dev]" --quiet

echo ""
echo "Verifying installation..."
$PYTHON -c "import secondbrain; print('  secondbrain package: OK')"
$PYTHON -c "import fastapi; print('  fastapi: OK')"
$PYTHON -c "import typer; print('  typer: OK')"

# Verify CLI is available
if command -v secondbrain &>/dev/null; then
    echo "  secondbrain CLI: OK"
else
    echo "  secondbrain CLI: installed (may need to restart shell or add pip bin to PATH)"
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Next steps:"
echo "  1. Initialize a vault:  secondbrain init ~/SecondBrain"
echo "  2. Configure your LLM:  Start the UI and go to Settings"
echo "  3. Start the UI:        secondbrain serve --vault ~/SecondBrain --port 8184"
echo "  4. Or use the script:   ./scripts/start.sh ~/SecondBrain"
echo ""
echo "  Run tests:              python3 -m pytest tests/ -v"
