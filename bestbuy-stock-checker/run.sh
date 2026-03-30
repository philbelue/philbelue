#!/bin/bash
# Best Buy Stock Checker — run helper
# Usage:
#   ./run.sh              # continuous loop (default)
#   ./run.sh --once       # single check
#   ./run.sh --test-notify # test all notification channels

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

python3 stock_checker.py "$@"
