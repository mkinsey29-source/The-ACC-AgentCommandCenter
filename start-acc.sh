#!/usr/bin/env bash
set -euo pipefail
ACC_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
  ACC_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  ACC_PYTHON=python
else
  echo 'ACC requires Python 3.10 or newer on PATH.' >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then
  set -- launch
fi
exec "$ACC_PYTHON" "$ACC_ROOT/acc/setup.py" "$@"
