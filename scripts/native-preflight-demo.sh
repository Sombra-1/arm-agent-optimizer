#!/usr/bin/env bash
# Run with an existing local GGUF on native Arm64. No model or runtime is downloaded.
set -euo pipefail

if [[ $# != 4 ]]; then
  echo 'Usage: scripts/native-preflight-demo.sh LLAMA_SERVER LLAMA_BENCH MODEL_GGUF OUTPUT_DIR' >&2
  exit 1
fi
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) echo 'Native Arm64 hardware is required for this demonstration.' >&2; exit 1 ;;
esac
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cli_bin=${AARCHTUNE_BIN:-"$project_root/.venv/bin/aarchtune"}
output_dir=$4
if [[ -e "$output_dir" ]]; then
  echo 'Use a new output directory to preserve prior evidence.' >&2
  exit 1
fi
"$cli_bin" preflight \
  --binary "$1" --model "$3" \
  --workload "$project_root/workloads/support-triage.jsonl" \
  --response-format json_schema --repetitions 3 \
  --output-dir "$output_dir/preflight"
"$cli_bin" optimize \
  --server-binary "$1" --bench-binary "$2" --model "$3" \
  --workload "$project_root/workloads/support-triage.jsonl" \
  --response-format json_schema --baseline-repetitions 3 --evaluation-repetitions 3 \
  --goal latency --max-profiles 8 --advance-count 4 \
  --output-dir "$output_dir/optimization"
"$cli_bin" optimize validate "$output_dir/optimization"
