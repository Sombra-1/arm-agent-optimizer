#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 (--source DIR | --clone-url URL) [--commit REF] [--kleidiai] [--source-only] [--build-root DIR]"
}

source_dir=''
clone_url=''
commit_ref=''
build_root=''
enable_kleidiai=false
source_only=false
while (($#)); do
  case "$1" in
    --source) source_dir=${2:?missing source directory}; shift 2 ;;
    --clone-url) clone_url=${2:?missing clone URL}; shift 2 ;;
    --commit) commit_ref=${2:?missing commit}; shift 2 ;;
    --build-root) build_root=${2:?missing build root}; shift 2 ;;
    --kleidiai) enable_kleidiai=true; shift ;;
    --source-only) source_only=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ $(uname -s) == Linux ]] || { echo "Linux is required" >&2; exit 1; }
[[ $EUID -ne 0 ]] || { echo "Do not run this helper as root" >&2; exit 1; }
[[ -z $source_dir || -z $clone_url ]] || { echo "Use --source or --clone-url, not both" >&2; exit 2; }
[[ -n $source_dir || -n $clone_url ]] || { usage >&2; exit 2; }
architecture=$(uname -m)
if [[ $architecture != aarch64 && $architecture != arm64 && $source_only != true ]]; then
  echo "Build execution requires AArch64; use --source-only only to prepare sources" >&2
  exit 1
fi

if [[ -n $clone_url ]]; then
  source_dir=${source_dir:-"$PWD/llama.cpp"}
  [[ ! -e $source_dir ]] || { echo "Clone target already exists: $source_dir" >&2; exit 1; }
  git clone -- "$clone_url" "$source_dir"
fi
source_dir=$(realpath "$source_dir")
[[ -f $source_dir/CMakeLists.txt ]] || { echo "Not a CMake source tree: $source_dir" >&2; exit 1; }
if [[ -n $commit_ref ]]; then
  git -C "$source_dir" checkout --detach "$commit_ref"
fi
commit=$(git -C "$source_dir" rev-parse HEAD)
echo "Prepared llama.cpp commit: $commit"
if [[ $source_only == true ]]; then
  echo "Source-only mode complete; no build was run."
  exit 0
fi

command -v cmake >/dev/null || { echo "Install CMake manually before continuing" >&2; exit 1; }
build_root=${build_root:-"$source_dir/build-aarchtune"}
mkdir -p "$build_root"
generic_dir="$build_root/generic-arm64"
generic_args=(-S "$source_dir" -B "$generic_dir" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF)
cmake "${generic_args[@]}"
cmake --build "$generic_dir" --config Release --parallel

metadata="$build_root/build-metadata.txt"
printf 'commit=%s\nbuild_type=Release\ngeneric_args=' "$commit" >"$metadata"
printf ' %q' "${generic_args[@]}" >>"$metadata"
printf '\n' >>"$metadata"

find_binary() {
  local root=$1 name=$2
  find "$root" -type f -name "$name" -perm -u+x -print -quit
}

if [[ $enable_kleidiai == true ]]; then
  kleidiai_dir="$build_root/kleidiai-arm64"
  kleidiai_args=(-S "$source_dir" -B "$kleidiai_dir" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF -DGGML_CPU_KLEIDIAI=ON)
  echo "Attempting KleidiAI build; confirm this CMake option for the pinned llama.cpp revision."
  cmake "${kleidiai_args[@]}"
  cmake --build "$kleidiai_dir" --config Release --parallel
  kleidiai_server=$(find_binary "$kleidiai_dir" llama-server)
  kleidiai_bench=$(find_binary "$kleidiai_dir" llama-bench)
  [[ -n $kleidiai_server && -n $kleidiai_bench ]] || {
    echo "KleidiAI build did not produce llama-server and llama-bench" >&2
    exit 1
  }
  "$kleidiai_server" --version
  "$kleidiai_bench" --version
  printf 'kleidiai_args=' >>"$metadata"
  printf ' %q' "${kleidiai_args[@]}" >>"$metadata"
  printf '\n' >>"$metadata"
  printf 'kleidiai_server=%s\nkleidiai_bench=%s\n' "$kleidiai_server" "$kleidiai_bench" >>"$metadata"
fi

server=$(find_binary "$generic_dir" llama-server)
bench=$(find_binary "$generic_dir" llama-bench)
[[ -n $server && -n $bench ]] || { echo "Expected llama-server and llama-bench were not produced" >&2; exit 1; }
"$server" --version
"$bench" --version
printf 'server=%s\nbench=%s\n' "$server" "$bench" >>"$metadata"
echo "Build verified. Next:"
printf '  aarchtune doctor\n  aarchtune optimize --server-binary %q --bench-binary %q --model MODEL.gguf --workload workloads/smoke-test.jsonl --output-dir results/arm-run\n' "$server" "$bench"
echo "No model weights were downloaded."
