#!/bin/sh
# POSIX launcher for Linux, macOS, BSD and other compatible terminals.
set -eu
sparker_root=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
if [ -n "${SPARKER_PYTHON:-}" ]; then
    if ! "$SPARKER_PYTHON" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
        printf '%s\n' 'SPARKER iCLI: SPARKER_PYTHON must name a working Python 3.11 or newer interpreter.' >&2
        exit 2
    fi
    exec "$SPARKER_PYTHON" "$sparker_root/run.py" "$@"
fi
for sparker_python in python3 python; do
    if command -v "$sparker_python" >/dev/null 2>&1 &&
       "$sparker_python" -c 'import sys; assert sys.version_info >= (3,11)' >/dev/null 2>&1; then
        exec "$sparker_python" "$sparker_root/run.py" "$@"
    fi
done
printf '%s\n' 'SPARKER iCLI requires Python 3.11 or newer. Install Python, then run again.' >&2
exit 2
