# AArchTune

[![CI](https://github.com/Sombra-1/arm-agent-optimizer/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sombra-1/arm-agent-optimizer/actions/workflows/ci.yml)

**Diagnose, benchmark, and tune local GGUF inference with quality gates.**

Arm64-native · GGUF/llama.cpp · workload-aware quality gates · reproducible
evidence · safe candidate selection

AArchTune searches CPU inference configurations, measures real workload
behavior, verifies evidence and provenance, and recommends a result only when
performance and correctness both pass.

The **0.2 development branch** starts with model quality: `aarchtune preflight`
checks a baseline, explains failures, and exports a diagnostic report even when
tuning cannot proceed. `optimize` now stops before searching if that baseline
fails. Optional JSON Schema generation preserves the same output contract through
candidate evaluation. See the [preflight guide](docs/preflight.md) for setup,
starter workload creation, and [measured 0.2 native results](docs/native-0.2-results.md).

> **0.2 native result:** Preflight rejected the original poor-quality baseline
> before search. Two quality-passing 14B support-triage runs completed all
> six stages and produced verified `baseline_retained` results.

Both evaluated configurations passed every quality check, but no alternative
met the unchanged 2% improvement threshold. No runtime speedup is claimed.

## The problem

`llama.cpp` exposes useful controls for threads, batching, parallel slots,
memory mapping, prompt caching, and context. A speed-only optimizer can select a
configuration that produces malformed JSON, wrong actions, timeouts, or
semantic regressions.

AArchTune asks a stricter question:

> Which configuration is faster, correct, reproducible, and safe to deploy?

It refuses to convert faster-but-wrong measurements into a recommendation.

## What AArchTune does

- Detects Arm64 hardware, topology, memory, CPU capabilities, and runtime flags.
- Measures a fixed real-workload baseline.
- Diagnoses baseline quality and blocks expensive tuning when evidence or correctness fails.
- Supports explicit per-task JSON Schema output while retaining independent validators.
- Generates a deterministic, bounded, hardware-aware candidate plan.
- Deduplicates low-level benchmark signatures before expensive evaluation.
- Runs advanced candidates through isolated `llama-server` processes.
- Applies declarative task validators and absolute/baseline-relative quality gates.
- Validates start/end baseline drift.
- Selects an eligible candidate or explicitly retains/rejects the baseline.
- Produces a verifiable Optimization Passport, report, and deployment bundle.

No model is trained or modified. AArchTune tunes runtime configuration only.

## What was optimized

AArchTune replaces manual, speed-first llama.cpp tuning with a bounded,
hardware-aware workflow: equivalent low-level signatures are deduplicated,
expensive candidates are filtered before real-workload evaluation, and quality
plus drift must pass before ranking.

The original smoke workflow measured this funnel directly: 132 screening executions
completed, producing 11 distinct low-level signatures, and four profiles
advanced to real-workload evaluation. All four failed the unchanged quality
policy, so AArchTune emitted `no_eligible_candidate` instead of an unsafe
recommendation.

That is measurable developer-workflow and decision-quality evidence. It is not
an end-to-end inference speedup claim.

## Why Arm64

Arm cloud and server CPUs differ in core topology, memory headroom, NUMA
layout, and instruction support. AArchTune uses recorded hardware evidence
rather than assuming that more threads, larger batches, mmap changes, or
parallel requests are always beneficial.

Real optimization requires Linux AArch64. Synthetic and non-Arm modes require
explicit development opt-ins and cannot produce Arm performance claims.

KleidiAI was compiled into the pinned llama.cpp build. During the
Qwen2.5-1.5B Q4_K_M native smoke, llama.cpp reported that q4_K tensors had no
KleidiAI kernel and were not accelerated; the available kernels were reported
for Q4_0 and Q8_0. No end-to-end KleidiAI runtime speedup is claimed for this
tested model. Build integration was verified, tested Q4_K runtime acceleration
was not present, and no general claim is made about other tensor formats or
hardware.

## Architecture

```mermaid
flowchart TD
    H[Hardware detection] --> R[Runtime discovery]
    R --> B[Baseline execution]
    B --> F{Baseline preflight}
    F -- quality or evidence failed --> Z[Diagnostic report; retain current configuration]
    F -- quality passed --> C[Candidate generation]
    C --> S[Screening]
    S --> N[Native measurement]
    N --> V[Quality validators]
    V --> G{Eligibility gate}
    G -- quality failed --> X[Rejected by quality gate]
    G -- quality passed --> D[Drift validation]
    D --> Q{Selection decision}
    Q --> O[Candidate selected or baseline retained]
    O --> P[Deployment Passport]

    E[(Evidence and provenance)]
    E -. hardware and runtime hashes .-> R
    E -. model, workload, policy hashes .-> B
    E -. measurements and manifests .-> N
    E -. validator decisions .-> G
    E -. drift and selection rationale .-> Q
    E -. canonical bundle hashes .-> P
```

The `optimize` orchestrator calls the same validated stage APIs exposed by the
CLI. Stage artifacts remain separate, and downstream hashes bind each decision
to its inputs.

## Optimization lifecycle

```text
doctor → baseline → plan → screen → evaluate → quality gate
       → drift validation → select or retain → finalize → Passport
```

The final decision can legitimately be:

- an eligible candidate was selected;
- the baseline was retained because improvement was insufficient;
- no candidate was eligible because quality failed; or
- evidence was invalidated or incomplete.

## Quality-gating model

The quality gate runs before ranking. The default policy requires:

| Check | Default threshold |
| --- | ---: |
| Request success | at least 0.98 |
| Task success | at least 0.95 |
| JSON validity | at least 0.98 |
| Validator pass rate | at least 0.97 |
| Timeout rate | at most 0.02 |
| Completed evidence | at least 0.98 |
| Repetitions per task | at least 2 |

It also limits regression from a fresh baseline, protects critical validators,
and rejects evidence when drift exceeds policy. A quality failure excludes a
profile from ranking without deleting its diagnostic evidence.

See [quality policy](docs/quality-policy.md) and
[evaluation methodology](docs/evaluation-methodology.md).

## Native Arm64 validation

Validation used GitHub-hosted `ubuntu-24.04-arm`, native AArch64, four Arm
Neoverse-N2 cores, CPU inference, `n_gpu_layers=0`, and pinned llama.cpp
`b10106` at commit
`1425386fd996511e1f3295e7366c38289a92a271`.

### 0.2 product validation

[Run 43614303062](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43614303062)
reproduced the original 1.5B quality failure and stopped before planning or
screening. [Run 43622255530](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43622255530)
completed the full pipeline with Qwen2.5-14B Q3_K_M, the support-triage workload,
JSON Schema output, a 2,048-token context, and one serving slot.

The baseline and cache candidate each passed all ten evaluated task attempts.
Their P95 latencies were 14.581 s and 14.665 s respectively. The baseline was
retained; drift checks, request-generation audit, final bundle, Passport, and
cleanup verification passed. Screening finished with partial status: four
planned profiles became three low-level signatures and two evaluated profiles.

An [unchanged repeat](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/43638354390)
produced the same evaluated profiles, passing quality decisions, and retained
baseline. Evaluated-baseline P95 differed by about 0.40% between runs. These are
two bounded native validations, not a tuning speedup or statistical confidence.
See [all attempts, limitations, and permanent evidence](docs/native-0.2-results.md).

### Original native optimization smoke

[Run 30119492016](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30119492016)
completed the real pipeline:

| Evidence | Result |
| --- | ---: |
| Configured candidate executions | 132 |
| Completed candidate executions | 132 |
| Distinct candidate signatures | 11 |
| Advanced/evaluated candidates | 4 |
| Optimization outcome | `no_eligible_candidate` |
| Native optimize exit | 4 |

All candidates failed the unchanged quality policy. AArchTune therefore
published no speedup or deployment recommendation.

### Permanent native evidence

The reviewed Actions artifact had temporary retention, so its original ZIP
bytes are preserved in the repository with the original SHA-256:

- [evidence record and verification instructions](docs/evidence/README.md);
- [original artifact ZIP](docs/evidence/aarchtune-real-arm64-smoke-30119492016-1.zip);
  and
- [SHA-256 checksum](docs/evidence/aarchtune-real-arm64-smoke-30119492016-1.sha256).

Verify the checksum before trusting or extracting the ZIP. Preservation keeps
the existing sanitized evidence available; it does not create a new benchmark
claim or repeatability proof.

### Baseline-only model quality evidence

| Model and contract | Request | Task | JSON | Validators | Policy |
| --- | ---: | ---: | ---: | ---: | --- |
| Qwen2.5-1.5B Q4_K_M, prompt-only | 1.00 | 0.00 | 0.30 | 0.48 | failed |
| Qwen2.5-7B Q3_K_M, prompt-only | 1.00 | 0.40 | 0.80 | 0.72 | failed |
| Qwen2.5-14B Q3_K_M, prompt-only | 1.00 | 0.40 | 0.80 | 0.72 | failed |
| Qwen2.5-7B Q3_K_M, JSON-object | 1.00 | 0.40 | 0.80 | 0.72 | failed |
| Mistral Nemo 12B Q4_K_M, prompt-only | unavailable | unavailable | unavailable | unavailable | incomplete |

Scaling tested Qwen from 7B to 14B did not change the aggregate workload
result. Plain JSON-object mode also produced the same aggregate result as
prompt-only Qwen 7B. These observations are specific to the pinned models,
workload, settings, and runner; they are not universal model rankings.

Mistral was immutably pinned and verified, but both final CPU probe attempts
lost the hosted runner before baseline inference. Its quality, Arm
compatibility, RAM fit, and relative performance remain unknown.

See the [final validation report](docs/FINAL_VALIDATION_REPORT.md).

## Reproducible local demo

This short path validates the product without downloading a model or claiming
native performance:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

aarchtune doctor --json
aarchtune workload validate workloads/smoke-test.jsonl --json
scripts/validate-release.sh
```

`doctor` describes the machine on which it runs; do not present x86 output as
Arm evidence. The demo should use the existing native report and sanitized
artifact for native claims. See the [under-three-minute demo
script](docs/DEMO_SCRIPT.md).

## Evidence and privacy

Every stage records typed configuration, exact hashes, manifests, completion
state, and provenance. Warm-ups are excluded from measured statistics.
Candidate order is deterministic, and an ending baseline sentinel checks drift.

Raw responses and server logs remain outside the compact final bundle. The
reviewed native workflow excludes model weights, caches, process streams,
request bodies, environment dumps, credentials, and private paths, then scans
the artifact before upload. The Passport references validated stage hashes
instead of copying sensitive evidence.

See [security](docs/security.md), [runtime safety](docs/runtime-safety.md), and
[Optimization Passport](docs/optimization-passport.md).

## Current limitations

- Native results come from an ephemeral shared runner, not a dedicated benchmark host.
- Two 0.2 searches repeated quality and retention decisions; broader repeatability remains unproven.
- The original smoke workload failed quality; the 14B support-triage pairing passed.
- Mistral quality could not be measured because the hosted runner was lost.
- KleidiAI build integration is proven; the tested Q4_K tensors had no
  KleidiAI kernel, and no end-to-end runtime speedup is claimed.
- No candidate is claimed as production-optimal.
- Sequential service rate is not concurrent-client throughput.
- Broader Arm hardware and workload validation remains future work.

These boundaries are evidence discipline: unavailable or incomplete results are
not converted into zeroes, failures, or performance claims.

## Repository structure

| Path | Purpose |
| --- | --- |
| `src/aarchtune/` | Product implementation and CLI |
| `workloads/` | Deterministic workload definitions |
| `configs/` | Search, screening, and quality policies |
| `scripts/` | Release validation and Arm build helpers |
| `tests/` | Unit, integration, artifact, privacy, and workflow tests |
| `docs/` | Methodology, evidence, demo, and submission material |
| `.github/workflows/` | Normal CI and manual native evidence workflows |

## Installation

Requirements: Python 3.11+, Linux CPU inference, a readable GGUF model, and
compatible `llama-server`/`llama-bench` binaries for real optimization.

```bash
scripts/setup.sh
source .venv/bin/activate
aarchtune --version
```

Use `scripts/setup.sh --dev` to include test and lint tools. This sets up the
Python CLI in the project environment; real inference still requires your local
GGUF and llama.cpp binaries.

## CLI examples

Inspect the current host:

```bash
aarchtune doctor --json
```

Validate the workload:

```bash
aarchtune workload validate workloads/smoke-test.jsonl --json
```

Create a starter workload and assess baseline quality:

```bash
aarchtune workload init --output my-workload.jsonl
aarchtune preflight \
  --binary LLAMA_SERVER --model MODEL_GGUF \
  --workload my-workload.jsonl --response-format json_schema \
  --output-dir results/preflight
```

Run the full pipeline on an authorized local model:

```bash
aarchtune optimize \
  --server-binary LLAMA_SERVER \
  --bench-binary LLAMA_BENCH \
  --model MODEL_GGUF \
  --workload workloads/reliability-agent.jsonl \
  --goal balanced \
  --output-dir results/optimization
```

Validate generated evidence:

```bash
aarchtune optimize validate results/optimization --json
aarchtune finalize validate results/optimization/final --json
aarchtune passport verify \
  results/optimization/final/optimization-passport.json --json
```

See [workload format](docs/workload-format.md),
[baseline methodology](docs/baseline-methodology.md), and
[search planning](docs/search-planning.md).

## Tests

```bash
source .venv/bin/activate
scripts/validate-release.sh
pytest
ruff check .
ruff format --check .
mypy src
```

The automated suite covers runtime management, workload validation, preflight
diagnostics, candidate selection, artifact integrity, and workflow behavior.
Normal CI validates Python 3.11 and 3.12. Synthetic fixtures are explicitly
labelled and are product-behavior evidence, never native performance evidence.

## Submission status

The original hackathon experiments remain frozen as historical evidence.
No full-performance recommendation or unsupported speedup claim is published.
The original project and demo video are published on
[Devpost](https://devpost.com/software/aarchtune), and the video is also
available directly on [YouTube](https://youtu.be/RemP5SKa0RM).

- [Final validation report](docs/FINAL_VALIDATION_REPORT.md)
- [Devpost submission text](docs/devpost-submission.md)
- [Demo script](docs/DEMO_SCRIPT.md)
- [Video production bundle](video/README.md)
- [Submission checklist](docs/SUBMISSION_CHECKLIST.md)

## License

MIT. See [LICENSE](LICENSE).
