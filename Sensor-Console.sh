#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec ComputerVision/.venv/bin/python scripts/sensor-console.py "$@"
