# First real Arm64 validation runbook

1. Use Linux AArch64 with Python 3.11+, local storage, and CPU inference.
2. Prefer enough RAM for the chosen GGUF plus context, parallel slots, and system headroom.
3. Install compiler, CMake, Git, Python headers, and project dependencies manually; do not use the helper as root.
4. Build pinned `llama.cpp` generic Arm binaries with `scripts/build-llama-arm64.sh --source DIR --commit COMMIT`.
5. Build KleidiAI explicitly with `--kleidiai`; retain build metadata and inspect whether the pinned revision supports the selected CMake flag.
6. Verify `uname -m` reports `aarch64` or `arm64`.
7. Inspect `lscpu`, `/proc/cpuinfo`, NUMA nodes, ASIMD, DotProd, I8MM, SVE, and SME evidence.
8. Run `aarchtune doctor --output hardware-report.json`; confirm runtime versions and KleidiAI evidence.
9. Obtain a GGUF manually from an authorized source. AArchTune does not download weights.
10. Read and record the model license and redistribution restrictions.
11. Run a one-repetition baseline with `workloads/smoke-test.jsonl`; inspect failures and cleanup.
12. Run the reliability workload baseline and confirm deterministic settings and validator expectations.
13. Run `aarchtune optimize` with pinned server/bench paths, model, workload, goal, and a new results directory.
14. Check start/end baseline drift; do not publish invalidated evidence.
15. Run `aarchtune optimize validate`, `aarchtune finalize validate`, and `aarchtune passport verify`.
16. Open `final/report.html` locally and inspect quality rejection, Pareto evidence, provenance, and unavailable metrics.
17. Optionally record Arm Performix or Streamline evidence separately; do not merge unsupported counters into AArchTune metrics.
18. Sanitize raw responses, paths, logs, and machine identifiers before publication.
19. Replace every `[INSERT REAL ARM64 RESULT]` placeholder only with validated run evidence; remove no synthetic warnings from synthetic artifacts.
20. Stop servers, verify no owned processes remain, retain hashes, and archive only reviewed artifacts.
