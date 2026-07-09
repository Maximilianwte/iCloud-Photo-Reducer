#!/usr/bin/env bash
# Wrapper so the user never has to think about the venv.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d ".venv" ]; then
    echo "Setting up environment (first run only)..."
    uv sync
fi

uv run photo-reducer "$@"
