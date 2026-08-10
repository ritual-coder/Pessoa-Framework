#!/usr/bin/env bash
# Pessoa Startup Script
set -euo pipefail

# Work from the repo root regardless of where this script is invoked from.
cd "$(dirname "${BASH_SOURCE[0]}")"

# 0. Locate a suitable interpreter.
# The mcp package requires Python 3.10+. Override with:  PYTHON_BIN=... ./START_PESSOA.sh
find_python() {
    if [ -n "${PYTHON_BIN:-}" ]; then
        echo "$PYTHON_BIN"; return
    fi
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            command -v "$candidate"; return
        fi
    done
}

# 1. Setup Environment
if [ ! -d "venv" ]; then
    PY="$(find_python)"
    if [ -z "$PY" ]; then
        echo "Error: no Python 3.10+ interpreter found." >&2
        echo "Install one, or set PYTHON_BIN=/path/to/python3 and re-run." >&2
        exit 1
    fi
    echo "Creating virtual environment with $PY ($("$PY" -V 2>&1))..."
    "$PY" -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

# 2. Start MCP Bridge
echo "Starting Pessoa Framework Bridge..."
exec ./venv/bin/python3 core/base_server.py
