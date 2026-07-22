#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"
python_bin=${PYTHON_BIN:-.venv/bin/python}
pytest_bin=${PYTEST_BIN:-.venv/bin/pytest}
ruff_bin=${RUFF_BIN:-.venv/bin/ruff}
mypy_bin=${MYPY_BIN:-.venv/bin/mypy}
cli_bin=${AARCHTUNE_BIN:-.venv/bin/aarchtune}

"$pytest_bin"
"$ruff_bin" check .
"$ruff_bin" format --check .
"$mypy_bin" src
"$cli_bin" --help >/dev/null
for command in doctor baseline plan screen evaluate optimize finalize passport; do
  "$cli_bin" "$command" --help >/dev/null
done

[[ -f LICENSE && -f README.md ]] || { echo "LICENSE or README.md missing" >&2; exit 1; }
if find . -type f -name '*.gguf' ! -path './tests/fixtures/models/fake-model.gguf' -print -quit | grep -q .; then
  echo "Unexpected GGUF model file found" >&2
  exit 1
fi
if find . -type f \( -name '*.tmp' -o -name 'optimization-passport.json' -o -name 'report.html' \) ! -path './tests/*' -print -quit | grep -q .; then
  echo "Generated or temporary artifact found in repository" >&2
  exit 1
fi
if rg -n --glob '!scripts/validate-release.sh' --glob '!docs/security.md' '(API_KEY|ACCESS_TOKEN|PASSWORD|SECRET)=[^[:space:]]+' .; then
  echo "Possible secret assignment found" >&2
  exit 1
fi
"$python_bin" -c 'from pathlib import Path; p=Path("tests/fixtures/models/fake-model.gguf"); assert "not a real" in p.read_text().lower()'
echo "AArchTune release validation passed. This does not validate real Arm performance."
