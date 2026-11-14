# Native 0.2 validation results

These are measured results from native GitHub-hosted Arm64, kept separately from
the original hackathon evidence. No quality policy or workload answer was changed.

## Run 43614303062: rejection and first optimization-model preflight

[Workflow run](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43614303062)
used product commit `9e1d44749075461d6b92aa9e7a002366e940f741` and pinned llama.cpp
`1425386fd996511e1f3295e7366c38289a92a271`. The repository's normal CI passed on
Python 3.11 and 3.12 for this commit.

| Experiment | Model / workload | Requests | Valid JSON | Passing tasks | Result |
| --- | --- | --- | --- | --- | --- |
| A: rejection | Qwen2.5-1.5B Q4_K_M / original smoke | 10/10 | 3/10 | 0/10 | Expected rejection passed; only doctor and baseline ran |
| B: optimization attempt | Qwen2.5-7B Q3_K_M / support triage, JSON Schema | 10/10 | 10/10 | 8/10 | Baseline quality failed; full-pipeline acceptance not established |

Experiment A used the exact model and workload hashes from the historical native
experiment. Its rejection occurred before planning or screening. Cleanup and
request-generation provenance validated. It establishes native early rejection.

For B, every output passed its JSON Schema validator, with no request failure or
timeout. `support-T-105`, the missing-information case, failed an exact-value check
in both repetitions. The public aggregate does not retain the failed field or
response, so this report does not guess the model's actual answer. The unchanged
95% task-success minimum rejected the observed 80%. Screening did not run.

All three supported output-mode protocol probes (`none`, `json_object`,
`json_schema`) succeeded on their single probe task. The unknown mode was rejected
with HTTP 400. These protocol probes do not establish full-workload quality in
each mode, and schema validity did not establish semantic correctness in B.

The overall workflow is correctly marked failed because B did not reach its
full-pipeline acceptance criteria. No speedup, selected candidate, retained
quality-passing deployment baseline, or repeatability result was established.

Permanent response-free artifacts and SHA-256 files:

- [Experiment A archive](evidence/aarchtune-0.2-rejection-43614303062-1.zip)
  ([checksum](evidence/aarchtune-0.2-rejection-43614303062-1.sha256))
- [Experiment B archive](evidence/aarchtune-0.2-optimization-43614303062-1.zip)
  ([checksum](evidence/aarchtune-0.2-optimization-43614303062-1.sha256))

The next controlled model-only substitution uses the existing pinned
Qwen2.5-14B Q3_K_M profile with the same support workload, generation contract,
quality policy, and search limits. Its results must be recorded independently,
including failure if it cannot pass preflight.

## Run 43615601029: 14B follow-up, incomplete pipeline evidence

[Workflow run](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43615601029)
used commit `fe669fe9361d50e16bc527a93fb47aff6972eecb` and the existing 14B profile,
with unchanged workload and policy hashes. Model identity and all three single-task
protocol probes passed; the unknown output mode again returned HTTP 400.

The first optimization call raised `StageError`. The initial experiment harness
recorded the exception class but did not preserve the failed stage's metadata.
Consequently, this attempt establishes neither baseline-quality failure nor a
quality-passing full pipeline. It is recorded as incomplete pipeline evidence,
not as a performance result. The second planned repetition did not start.

- [14B attempt archive](evidence/aarchtune-0.2-optimization-43615601029-1.zip)
  ([checksum](evidence/aarchtune-0.2-optimization-43615601029-1.sha256))

The failure-capture correction retains stage state, available baseline quality,
preflight reports, and existing sanitized screening diagnostics when execution
raises. A same-input diagnostic replay uses one repetition to identify the
failure before spending compute on a repeatability run. Response text and
arbitrary exception messages remain excluded.

Diagnostic replay [43617257770](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43617257770)
remained in model transfer beyond a complete 20-minute request window and was
replaced before inference. It establishes no additional quality or performance
result. The download retry now resumes partial transfers, detects sustained low
transfer speed, and still requires the exact immutable model size and SHA-256.
