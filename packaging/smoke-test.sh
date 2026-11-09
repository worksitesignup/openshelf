#!/usr/bin/env bash
# Start a packaged build with no display and a throwaway profile, and check it stays running.
# Usage: packaging/smoke-test.sh path/to/executable
set -euo pipefail
exe="$1"
home="$(mktemp -d)"
log="$home/output.log"

OPENSHELF_HOME="$home" QT_QPA_PLATFORM=offscreen "$exe" >"$log" 2>&1 &
pid=$!
sleep 25

if ! kill -0 "$pid" 2>/dev/null; then
    cat "$log"
    echo "The app exited during startup."
    exit 1
fi
kill "$pid"
wait "$pid" 2>/dev/null || true
cat "$log"
echo "The app started and stayed running."
