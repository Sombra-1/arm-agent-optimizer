# Under-three-minute demo script

Target duration: **2:50**. Devpost judges are not required to watch beyond
three minutes, so do not add material during recording.

## Recording principle

Use the existing completed evidence. Do not download a model, dispatch a
workflow, or wait for a benchmark during the recording. Keep native and
synthetic evidence visibly labelled.

Prepare these tabs:

1. Repository `README.md`.
2. `docs/FINAL_VALIDATION_REPORT.md`.
3. Native run `https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30119492016`.
4. `docs/evidence/README.md`.

## 0:00–0:20 — Problem

Say:

> Most inference optimizers ask only which configuration is fastest. AArchTune
> asks which configuration is faster, correct, reproducible, and safe to deploy
> on native Arm64.

Show the README title and Cloud AI track.

## 0:20–0:40 — Working project

Show the latest green CI run and repository structure. Say:

> The release gate runs the complete test, lint, format, typing, CLI, license,
> artifact, and secret checks on Python 3.11 and 3.12.

Do not run the full suite live.

## 0:40–1:05 — Native Arm64 execution

Show the native run and final validation environment:

- Linux AArch64;
- four Arm Neoverse-N2 cores;
- CPU-only llama.cpp;
- pinned llama.cpp source and binary hashes; and
- `n_gpu_layers=0`.

Say:

> KleidiAI was compiled into the pinned build. The tested Q4_K tensors had no
> matching KleidiAI kernel, so I make no runtime-acceleration claim for that
> model.

## 1:05–1:30 — Optimization changes

Show the architecture diagram and quality policy. Say:

> AArchTune replaces manual, speed-only tuning with deterministic candidate
> planning, low-level signature deduplication, real-workload validation,
> baseline drift checks, and a hash-bound evidence trail.

Point to the workload validators and unchanged quality thresholds.

## 1:30–2:05 — Measured result

Show native run `30119492016` and the evidence table:

```text
132 completed screening executions
11 distinct low-level signatures
4 profiles advanced to real-workload evaluation
Optimization result: no_eligible_candidate
```

Say:

> Every advanced profile failed the unchanged workload-quality policy. AArchTune
> therefore produced no speedup or deployment recommendation. That refusal is
> the optimization result: faster measurements cannot bypass correctness.

Do not call this a successful performance optimization.

## 2:05–2:30 — Reusable output

Show the permanent evidence record and Optimization Passport. Point out:

- hardware, runtime, model, workload, and policy hashes;
- stage validation and cleanup proof;
- drift and rejection evidence; and
- a deployment bundle only when a profile is eligible.

Say:

> The same workflow can be reused for another Arm server, GGUF model, workload,
> or quality policy without converting missing evidence into a performance
> claim.

## 2:30–2:50 — Close

Show the repository URL and MIT license. Say:

> AArchTune makes Arm inference optimization safer to automate: measure natively,
> validate the actual workload, preserve provenance, and deploy only when speed
> and correctness both survive.

Stop recording by 2:50. Verify the exported video remains below three minutes.
