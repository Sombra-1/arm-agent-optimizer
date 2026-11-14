#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${PYTHON_BIN:-python3}
target="$project_root"
if [[ "${1:-}" == "--dev" && $# == 1 ]]; then
  target="$project_root[dev]"
elif [[ $# != 0 ]]; then
  echo 'Usage: scripts/setup.sh [--dev]' >&2
  exit 1
fi
"$python_bin" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
if [[ ! -f "$project_root/.venv/pyvenv.cfg" ]]; then
  "$python_bin" -m venv "$project_root/.venv"
fi
"$project_root/.venv/bin/python" -m pip install -e "$target"
"$project_root/.venv/bin/aarchtune" --version
echo "CLI ready. Activate with: source \"$project_root/.venv/bin/activate\""
echo 'Next: aarchtune doctor, then aarchtune workload init --output my-workload.jsonl'
