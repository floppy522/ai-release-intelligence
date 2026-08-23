#!/usr/bin/env bash
set -euo pipefail

script_directory="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)"
cd -- "${script_directory}/.."
exec uv run --project apps/api python demo/seed_state.py "$@"
