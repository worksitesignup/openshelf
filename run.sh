#!/usr/bin/env bash
# Start Open Shelf from source, setting up its private Python environment on first run.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ ! -x .venv/bin/python ]; then
    echo "First run: installing Open Shelf's dependencies (about 700 MB)..."
    python3 -m venv .venv
    .venv/bin/pip install --disable-pip-version-check -q "PySide6>=6.9"
fi

exec .venv/bin/python -m openshelf "$@"
