# Judging evidence map

**Challenge track: Cloud AI**

AArchTune targets native Arm64 cloud/server CPU inference. Its differentiator is
quality-gated candidate eligibility: performance measurements cannot become a
deployment recommendation unless correctness and evidence integrity pass.

| Criterion | Evidence |
| --- | --- |
| Technical implementation | Hardware/runtime discovery, deterministic planning, bounded screening, isolated server lifecycle, workload validators, quality gate, drift sentinel, Pareto analysis, canonical Passport hashes |
| Arm usage | Native AArch64 workflow on four Neoverse-N2 cores, CPU-only pinned llama.cpp, Arm capability/topology/memory evidence, KleidiAI build integration with an explicit no-q4_K-kernel runtime boundary |
| Reliability | Absolute and baseline-relative quality policy, critical validators, evidence-completeness checks, safe process ownership, privacy scan, permanently preserved original artifact and checksum |
| Developer experience | One-command orchestrator, independently validated stages, safe resume, concise CLI summaries, self-contained report, reproducible bundle |
| Validated outcome | 132/132 native candidate executions completed; 11 signatures; 4 evaluated profiles; `no_eligible_candidate` because every profile failed unchanged quality policy |
| Submission value | Demonstrates why a safe optimizer must reject faster-but-wrong configurations instead of publishing an unsupported speedup |

Synthetic screenshots demonstrate report and rejection behavior only. Native
claims trace to the run IDs in
[FINAL_VALIDATION_REPORT.md](FINAL_VALIDATION_REPORT.md) and the verified
[permanent evidence archive](evidence/README.md).
