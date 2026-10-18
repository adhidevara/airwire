#!/bin/bash
# Double-click or run from Terminal to start Airwire Receiver on macOS.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ -f "$DIR/.venv/bin/activate" ]; then
    source "$DIR/.venv/bin/activate"
fi

python3 -m airwire "$@"
