# Native 0.2 validation results

These are measured results from native GitHub-hosted Arm64, kept separately from
the original hackathon evidence. No quality policy or workload answer was changed.

The 0.2 native milestones now include early quality rejection and two verified
full-pipeline comparisons with a quality-passing baseline. Both retained the
baseline; **no runtime-configuration speedup is established**. The two bounded
searches had identical evaluated profiles and quality decisions, stable within-run
drift, and a 0.40% relative range in evaluated-baseline P95. This is limited
repeatability evidence from two hosted workflow runs, not statistical confidence
or validation across independent hardware platforms.

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

## Run 43619596239: quality-passing 14B baseline, rejected search baseline

[Diagnostic replay](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43619596239)
on commit `e7dc3cb5487197dba8661cd16aec1497a7fc54a1` captured the missing stage state:

- The 14B baseline passed **10/10 tasks and 70/70 validators**, with valid JSON,
  valid schemas, successful requests, and no timeouts.
- Preflight passed the unchanged quality policy.
- Planning rejected the required baseline at `generate_candidates`; screening
  and finalization did not run. The original generic error covered both
  compatibility and memory-guardrail rejection, so the artifact alone does not
  establish which condition applied.
- Baseline server and sampler cleanup was confirmed.

This establishes a quality-passing native model/workload pairing, not a completed
optimization or performance improvement.

- [Diagnostic replay archive](evidence/aarchtune-0.2-optimization-43619596239-1.zip)
  ([checksum](evidence/aarchtune-0.2-optimization-43619596239-1.sha256))

The follow-up makes planning errors identify unsupported flags or memory pressure
with measured RSS and available-memory values, without bypassing either guard.
It also exposes explicit baseline context/slot controls and runs the same bounded
workload with a 2,048-token context and one serving slot. That changed server
configuration is recorded separately; previous failures remain evidence.

## Run 43622255530: verified full pipeline, baseline retained

[Bounded-context run](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43622255530)
used commit `5cbd233f9bb2409e9d82cb689daf3616f7c04630`, the same pinned 14B model,
runtime, support-triage workload, generation contract, and quality policy. The
explicit baseline configuration was a 2,048-token context and one serving slot.
Hardware provenance records native AArch64, four Neoverse-N2 cores, and about
15.6 GiB total RAM. The controlled experiment took about 33 minutes including
protocol probes.

All six stages completed: doctor, baseline/preflight, planning, screening,
evaluation, and finalization. Optimization evidence, the original final bundle,
and its Optimization Passport passed verification before public projection.
Process and sampler cleanup was confirmed. The request audit passed for all
50 measured workload requests across the initial baseline, start/end drift
sentinels, and the two evaluated configurations. Every audited request retained
the same JSON Schema, output mode, temperature, seed, and token limit.

The initial baseline passed 10/10 tasks and 70/70 validators. Both evaluated
configurations also passed 10/10 tasks with 100% JSON validity and validator
success. No quality gate was weakened. Start/end baseline drift was classified
stable: median latency moved about 0.56%, P95 about 0.49%, and all recorded
quality metrics were unchanged.

| Evaluated configuration | Median latency | P95 latency | Sequential requests/min | Peak RSS | Quality |
| --- | ---: | ---: | ---: | ---: | --- |
| Baseline, retained | 13.831 s | 14.581 s | 4.379 | 11,267,907,584 bytes | passed |
| `latency-p1-c2048-cache` | 13.893 s | 14.665 s | 4.361 | 11,267,895,296 bytes | passed |

Selection returned `baseline_retained_no_meaningful_improvement`, exit 0. The
alternative's measured P95 was about 0.58% slower than the evaluated baseline.
The report's approximately 1.06% P95 improvement field compares the selected
baseline with its fresh start sentinel; it is repeated-baseline variation, not
a gain from a changed configuration, and is below the unchanged 2% threshold.
The subsequent report correction omits configuration-improvement deltas when
the baseline is retained and explains the selection reason in the HTML. It also
suppresses this claim when rendering older report-data files. The original ZIP
and its originally rendered report remain immutable evidence of this attempt.
The correction passed the full local release checks with 560 tests, including
retained-baseline, rejected-selection, drift-invalid, and selected-alternative
report regressions. It also rendered this native run's actual report data without
claiming a configuration improvement.

The funnel contains four planned profiles, three low-level signatures, and two
advanced/evaluated profiles. Screening ended with `partial` status. The public
projection retains this status, aggregate funnel, and original source hashes,
but not individual screening exclusion/error reasons. This run therefore does
not establish complete successful coverage of every planned screening attempt.

All three single-task output-mode probes passed, and the unknown format returned
HTTP 400. Full-workload quality evidence here is for `json_schema` only. This is
a bounded hosted-runner validation, not an exhaustive search, a concurrent-load
benchmark, or a production performance guarantee.

- [Full-pipeline archive](evidence/aarchtune-0.2-optimization-43622255530-1.zip)
  ([checksum](evidence/aarchtune-0.2-optimization-43622255530-1.sha256))
- Artifact ID: `14126395481`; original ZIP SHA-256:
  `34854cd551621e146187926c3b1b494e02939f663560f00f9cc939e14cf88ce0`.
- [Python 3.11/3.12 CI](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43622255201)
  passed for the exact source commit; local release checks passed with 555 tests.

## Run 43638354390: unchanged repeat, same quality and retention decisions

[Repeat run](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43638354390)
used the exact same source commit `5cbd233f9bb2409e9d82cb689daf3616f7c04630`, model,
workload, runtime binary digests, policy, context/slot limits, and search bounds.
It was a separate hosted workflow job with the same visible four-core Neoverse-N2
hardware configuration. Physical-host independence is not established.

All six stages, optimization evidence, original final bundle, Passport, process
cleanup, and the 50-request generation audit passed again. The initial baseline
passed 10/10 tasks and 70/70 validators; both evaluated profiles passed 10/10 tasks
with 100% JSON validity and validator success. All three protocol probes passed,
and the unknown output mode returned HTTP 400. Baseline drift remained stable:
median latency moved about 0.45%, P95 about 1.51%, and quality did not change.

| Run | Baseline median | Baseline P95 | Cache candidate P95 | Quality-passing profiles | Selection |
| --- | ---: | ---: | ---: | ---: | --- |
| `43622255530` | 13.831 s | 14.581 s | 14.665 s | 2/2 | baseline retained |
| `43638354390` | 13.839 s | 14.639 s | 14.613 s | 2/2 | baseline retained |

The evaluated profile hashes were identical across runs, and both profiles'
quality decisions agreed. The evaluated baseline's P95 relative range was
`(14.639019423 - 14.581027603) / 14.581027603`, approximately **0.40%**.
The cache candidate was about 0.18% faster than the evaluated baseline in the
repeat, reversing the small ordering difference in the first run. Relative to
the fresh start sentinel, the best observed P95 improvement was approximately
1.59%, still below the unchanged 2% selection threshold. Retention was correct
in both runs; there is no verified configuration improvement.

Screening again had partial status with the same four-profile, three-signature,
two-evaluation funnel. The individual screening exclusion/error reasons are
absent from both public projections. Repetition does not remove that coverage
and diagnostic limitation. Two short searches on shared hosted infrastructure
also do not establish confidence intervals, production reliability, concurrent
serving behavior, or performance on other Arm systems.

- [Repeat archive](evidence/aarchtune-0.2-optimization-43638354390-1.zip)
  ([checksum](evidence/aarchtune-0.2-optimization-43638354390-1.sha256))
- Artifact ID: `14131843968`; original ZIP SHA-256:
  `a7980a7135f53ca1678ef2d1c6a0a18962521b7f5a077f2c8897ad6a61fc47f5`.
- [Python 3.11/3.12 CI for the report correction](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43639012372)
  passed on commit `3a0e409687f32363b723946a513ac49fa9fc6309`.
