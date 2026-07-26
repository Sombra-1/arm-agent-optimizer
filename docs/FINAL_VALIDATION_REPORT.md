# Final validation report

## Executive conclusion

AArchTune is validated as a quality-gated optimization system for GGUF
inference workloads on native Arm64. It executes the real pipeline, preserves
hardware/model/runtime/workload/policy provenance, and refuses to recommend a
configuration when workload correctness does not pass.

The strongest result is not a percentage speedup:

> AArchTune completed a real 132-execution native Arm64 optimization workflow
> and correctly retained no candidate because none satisfied the unchanged
> quality policy.

`no_eligible_candidate` is a valid safety outcome. It prevents a speed-only
ranking from promoting malformed or semantically wrong responses.

## Validation environment

| Field | Validated environment |
| --- | --- |
| Runner image | GitHub-hosted `ubuntu-24.04-arm` |
| Architecture | Native AArch64; not emulation |
| CPU | Arm Neoverse-N2 |
| Cores | 4 |
| Memory | 16,722,046,976 bytes total (about 15.6 GiB); per-run `MemAvailable` varied on the shared runner |
| Runtime | CPU-only llama.cpp |
| llama.cpp release | `b10106` |
| llama.cpp commit | `1425386fd996511e1f3295e7366c38289a92a271` |
| GPU layers | `n_gpu_layers=0` |
| Memory mapping | enabled |
| KleidiAI | build integration enabled and verified at build time |
| KleidiAI runtime | unknown |
| Workload | five deterministic tasks, two measured repetitions per task |
| Quality policy | unchanged `configs/default-quality-policy.yaml` |

The runner is ephemeral and shared. These runs validate native execution and
product behavior; they are not a dedicated-host performance or repeatability
study.

## Product validation

The final repository validation establishes:

- clean release validation;
- strict MyPy;
- Ruff lint and formatting checks;
- 514 automated tests with 90% aggregate coverage;
- normal CI on Python 3.11 and 3.12;
- native Arm64 workflow execution;
- immutable model, source-model, license, GGUF, and llama.cpp provenance;
- privacy-safe public evidence boundaries;
- absolute and baseline-relative quality gates;
- drift validation;
- deterministic candidate eligibility and selection controls;
- deployment bundle and Optimization Passport validation; and
- explicit separation of synthetic behavior tests from native evidence.

Normal CI run
[30202291140](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30202291140)
passed on its first attempt for Python 3.11 and 3.12.

## Native evidence table

| Run ID | Purpose | Model | Output contract | Evidence type | Attempts | Outcome | Valid conclusion | Invalid conclusion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [30119492016](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30119492016) | Full native optimization smoke | Qwen2.5-1.5B Q4_K_M | prompt-only | Native | 132/132 configured candidate executions; 11 signatures; 4 evaluated profiles | `no_eligible_candidate`, optimize exit 4 | The real pipeline completed and rejected every evaluated candidate under the unchanged quality policy | A successful performance optimization or production recommendation |
| [30155729170](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30155729170) | Baseline-only quality precheck | Qwen2.5-7B Q3_K_M | prompt-only | Native | 10/10 measured | `quality_policy_failed` | Requests completed, but aggregate task/JSON/validator quality did not meet policy | The model is universally unsuitable or slow |
| [30158840897](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30158840897) | Baseline-only quality precheck | Qwen2.5-14B Q3_K_M | prompt-only | Native | 10/10 measured | `quality_policy_failed` | Scaling the tested configuration from 7B to 14B did not change this workload's aggregate result | 14B can never improve quality |
| [30161317463](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30161317463) | Response-contract precheck | Qwen2.5-7B Q3_K_M | JSON-object | Native | 10/10 measured | `quality_policy_failed` | Plain JSON-object mode did not change serialization or semantic aggregates for this run | Structured decoding can never help any model/workload |
| [30174908176](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30174908176) | Mistral baseline precheck | Mistral Nemo 12B Q4_K_M | prompt-only | Native | 0/10 measured; warm-up incomplete | `quality_evidence_incomplete` | Pinned provenance and model verification completed before hosted-runner loss | Mistral failed quality, exceeded RAM, caused OOM, or is Arm-incompatible |
| [30202422475](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30202422475) | Final supervised Mistral precheck | Mistral Nemo 12B Q4_K_M | prompt-only | Native | 0/10 measured; warm-up incomplete | `quality_evidence_incomplete` | The hosted runner again shut down during the CPU probe; initiating cause remains unproven | A model-caused shutdown, quality result, OOM result, or Qwen comparison |

Synthetic fixtures separately validate selection, rejection, reporting, and
artifact behavior. Their generated measurements are not Arm or model-performance
evidence and are excluded from the native table.

## Full native optimization smoke

The completed Qwen2.5-1.5B Q4_K_M smoke exercised the real pipeline:

```text
Configured candidate executions: 132
Completed candidate executions: 132
Distinct candidate signatures: 11
Advanced/evaluated candidates: 4
Optimization result: no_eligible_candidate
Native optimize exit: 4
```

The initial baseline and all evaluated candidates failed the unchanged absolute
quality thresholds. Candidate execution and evidence validation completed; no
candidate was eligible for performance ranking. No speedup is claimed.

## Qwen quality evidence

| Configuration | Request success | Task success | JSON validity | Validator pass |
| --- | ---: | ---: | ---: | ---: |
| Qwen2.5-1.5B Q4_K_M, prompt-only | 1.00 | 0.00 | 0.30 | 0.48 |
| Qwen2.5-7B Q3_K_M, prompt-only | 1.00 | 0.40 | 0.80 | 0.72 |
| Qwen2.5-14B Q3_K_M, prompt-only | 1.00 | 0.40 | 0.80 | 0.72 |
| Qwen2.5-7B Q3_K_M, JSON-object | 1.00 | 0.40 | 0.80 | 0.72 |

Supported conclusions:

- Scaling the tested Qwen configuration from 7B to 14B did not improve this
  workload result.
- Plain JSON-object mode did not improve serialization or semantic quality.
- Qwen 7B, Qwen 14B, and constrained Qwen 7B produced identical aggregate
  results.
- A full optimization using these configurations was correctly blocked by the
  quality policy.

These are workload-specific observations, not general model rankings.

## Mistral evidence boundary

Mistral Nemo 12B Q4_K_M was immutably pinned. Both final attempts completed:

- model metadata and exact size verification;
- exact SHA-256 verification;
- source-model and license provenance;
- GGUF metadata validation;
- pinned llama.cpp compatibility inspection; and
- native Arm64 runner startup.

Both attempts then lost the hosted runner during the CPU probe, before baseline
inference:

```text
quality_evidence_incomplete
measured attempts: 0/10
warm-up: not completed
quality metrics: unavailable
resource incompatibility: unproven
OOM: unproven
model-quality failure: unproven
```

No evidence supports saying that Mistral failed quality, is incompatible with
Arm, exceeded RAM, caused OOM, or performed better or worse than Qwen. The
initiating shutdown cause remains unproven.

## KleidiAI statement

KleidiAI build integration was enabled and verified at build time. Existing
evidence does not conclusively verify runtime acceleration, so runtime status is
reported as unknown.

## Evidence and privacy

Reviewed public artifacts exclude GGUF weights, caches, raw responses, request
bodies, server logs, process streams, credentials, environment dumps, and
private paths. Final bundles use hashes and manifests to bind results to stage
evidence. Synthetic reports carry visible synthetic warnings.

The final Mistral run produced no artifact because runner shutdown skipped
cleanup-proof, privacy-scan, and upload steps. This absence is recorded as
incomplete evidence rather than treated as proof of cleanup, privacy, OOM, or
model behavior.

## Final conclusions

- The product pipeline executes on native Arm64.
- Quality gates operate correctly and can safely return
  `no_eligible_candidate`.
- Tested Qwen configurations did not satisfy the unchanged workload quality
  policy.
- JSON-object mode did not change the tested Qwen aggregate result.
- Mistral quality remains unknown because the hosted runner was lost before
  baseline inference.
- KleidiAI build integration is proven; runtime activation is unknown.
- No full-performance recommendation is published.
- No unsupported speedup claim is made.
- Model-family experimentation is frozen.

The correct next step is documentation, demonstration, and submission
finalization—not another experiment.
