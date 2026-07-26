# AArchTune — Quality-Gated GGUF Optimization for Native Arm64

**Challenge track:** Cloud AI

**One-line description:** AArchTune searches native Arm64 inference
configurations and recommends a result only when performance, correctness,
provenance, and repeatability all pass.

## Inspiration

Most inference optimizers ask which configuration is fastest. That is not
enough for an agent or structured-output workload: a faster server can still
return malformed JSON, choose the wrong action, time out, or violate a safety
constraint.

AArchTune began with a stricter question:

> Which configuration is faster, correct, reproducible, and safe to deploy?

## What it does

AArchTune is an open-source, local-first optimizer for GGUF inference with
`llama.cpp` on Linux Arm64. It:

- detects hardware, topology, memory, and runtime capabilities;
- measures a fixed real-workload baseline;
- creates a deterministic bounded candidate plan;
- screens duplicate low-level signatures;
- evaluates advanced profiles against the real workload;
- applies absolute and baseline-relative quality gates;
- validates temporal drift;
- selects an eligible candidate or records why no candidate is safe; and
- creates a verifiable Optimization Passport, report, and deployment bundle.

It tunes runtime configuration. It does not train or modify the model.

## How it works

The pipeline is:

```text
hardware detection → runtime discovery → baseline → candidate plan
→ screening → native measurement → workload validators → eligibility gate
→ drift validation → selection or baseline retention → Passport
```

Performance ranking happens only after evidence completeness and workload
quality pass. Failed candidates keep diagnostic evidence but cannot enter final
ranking.

## How Arm technology is used

Real optimization requires native Linux AArch64. AArchTune records Arm CPU
features, physical/logical core topology, NUMA layout, available memory, and
runtime-supported flags. Candidate generation uses those facts rather than
assuming that maximum threads, larger batches, mmap changes, caching, or
parallel requests are always beneficial.

Native validation ran on GitHub-hosted `ubuntu-24.04-arm` with four Arm
Neoverse-N2 cores and CPU-only llama.cpp. The workflow pinned llama.cpp release
`b10106` to immutable commit
`1425386fd996511e1f3295e7366c38289a92a271`.

KleidiAI was compiled into the pinned llama.cpp build. During the
Qwen2.5-1.5B Q4_K_M native smoke, llama.cpp reported that q4_K tensors had no
KleidiAI kernel and were not accelerated; available kernels were reported for
Q4_0 and Q8_0. No end-to-end KleidiAI runtime speedup is claimed for this tested
model, and no general conclusion is made about other tensor formats or
hardware.

## Quality and reliability

The default policy requires at least:

- 0.98 request success;
- 0.95 task success;
- 0.98 JSON validity;
- 0.97 validator pass rate; and
- 0.98 completed evidence.

It also limits timeouts, baseline-relative regression, critical-validator
failures, and start/end baseline drift. A fast configuration that fails these
checks is ineligible.

The strongest native result demonstrates this safety behavior. AArchTune
completed all 132 configured candidate executions, deduplicated them into 11
signatures, advanced four profiles to real-workload evaluation, and returned
`no_eligible_candidate` because every evaluated profile failed the unchanged
quality policy.

That is not a successful performance optimization. It is a successful refusal
to deploy a faster-but-incorrect configuration.

## Technical architecture

AArchTune uses typed Pydantic artifacts, deterministic stage boundaries,
SHA-256 provenance, isolated process groups, bounded output/timeouts, loopback
server binding, declarative validators, and resumable orchestration that trusts
only revalidated completed stages.

The final bundle includes:

- a canonical Optimization Passport;
- stage and bundle hashes;
- quality decisions and rejection reasons;
- drift evidence;
- a self-contained report with no network dependencies;
- a selected configuration only when eligible; and
- safe reproduction/deployment files or an explicit unavailable result.

Raw responses, server logs, model weights, caches, environment dumps, and
credentials are excluded from reviewed public evidence.

## Challenges

The main challenge was separating three kinds of truth:

1. low-level performance evidence;
2. application-quality evidence; and
3. evidence completeness and provenance.

The native smoke completed but found no quality-eligible candidate. Later Qwen
quality prechecks improved aggregate quality over the 1.5B model but still
failed policy. Two final Mistral attempts lost the shared hosted runner during
CPU loading before inference, so Mistral quality remains unknown. The project
records that as incomplete evidence rather than inventing an OOM or quality
conclusion.

## Accomplishments

- Completed a real 132-execution optimization workflow on native Arm64.
- Correctly returned `no_eligible_candidate` under the unchanged quality policy.
- Verified immutable runtime, model, source-model, license, workload, and policy
  provenance.
- Implemented baseline-relative quality gating, drift checks, deterministic
  planning, safe process ownership, and candidate eligibility controls.
- Built validated final bundles and canonical Optimization Passports.
- Kept native evidence visibly separate from synthetic product-behavior tests.
- Passed 514 automated tests with 90% aggregate coverage, strict MyPy, Ruff, and
  Python 3.11/3.12 CI.

## What we learned

- Request success does not imply application correctness.
- Scaling the tested Qwen model from 7B to 14B did not change this workload's
  aggregate quality result.
- Plain JSON-object mode did not change the tested Qwen 7B aggregate result.
- `no_eligible_candidate` is an important production safety state, not an error
  to hide.
- Missing evidence must stay missing: hosted-runner shutdown does not prove
  model failure, Arm incompatibility, or OOM.
- Reproducibility requires hashes, provenance, stage validation, and drift
  evidence—not just a benchmark screenshot.

## Limitations

- Native results come from an ephemeral shared runner.
- One full native optimization smoke is not repeatability proof.
- Tested Qwen configurations did not satisfy the workload quality policy.
- Mistral quality was not measured because the hosted runner was lost before
  baseline inference.
- KleidiAI build integration is proven; the tested Q4_K tensors had no
  KleidiAI kernel, and no end-to-end runtime speedup is claimed.
- Sequential service rate is not concurrent-client throughput.
- No candidate is claimed as production-optimal.
- No performance speedup is claimed.

## What is next

Experimental work is frozen for submission. The immediate next steps are video
recording, screenshot/link review, and final Devpost publication.

Future work after submission may add repeated measurements on dedicated Arm
hardware, broader Arm server families, more representative workloads,
concurrent-client testing, and stronger runtime-level acceleration evidence.
Those would be new validation campaigns, not retroactive claims about the
current runs.

## Built with

- Python 3.11/3.12
- Typer
- Pydantic
- `llama.cpp`
- GGUF models
- Linux AArch64
- GitHub-hosted Arm runners
- KleidiAI build integration
- pytest, Ruff, MyPy, JSON Schema, and Rich
- GitHub Actions

## Repository and evidence

- Repository: https://github.com/Sombra-1/arm-agent-optimizer
- License: MIT
- Final validation report:
  https://github.com/Sombra-1/arm-agent-optimizer/blob/main/docs/FINAL_VALIDATION_REPORT.md
- Demo script:
  https://github.com/Sombra-1/arm-agent-optimizer/blob/main/docs/DEMO_SCRIPT.md
- Submission checklist:
  https://github.com/Sombra-1/arm-agent-optimizer/blob/main/docs/SUBMISSION_CHECKLIST.md
- Permanent native evidence:
  https://github.com/Sombra-1/arm-agent-optimizer/blob/main/docs/evidence/README.md
