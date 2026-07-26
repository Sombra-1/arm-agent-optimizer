# Limitations and evidence boundaries

These boundaries define what the validated evidence supports.

## Native evidence scope

- Native results come from an ephemeral shared GitHub-hosted runner, not a
  dedicated benchmark host.
- The completed full native optimization is one smoke run, not repeatability
  proof.
- Shared-runner page cache, neighboring work, frequency state, and thermal
  conditions can affect measurements.
- Sequential service rate measures workload service capacity, not
  concurrent-client throughput.
- Memory sampling is interval-based and can miss short peaks.

## Model-quality scope

- Tested Qwen configurations did not satisfy the unchanged workload quality
  policy.
- Qwen 7B, Qwen 14B, and JSON-object Qwen 7B produced identical aggregate
  results on this workload; that is not a universal model ranking.
- Mistral quality could not be measured because the hosted runner was lost
  before baseline inference.
- Mistral quality failure, Arm incompatibility, RAM exhaustion, and OOM remain
  unproven.
- Quality is limited to the declared workload validators.

## Runtime and selection scope

- V1 supports Linux CPU inference through `llama.cpp`; it does not tune GPUs,
  training, distributed inference, or concurrent multi-client load.
- Client-side time-to-first-token is unavailable in non-streaming V1.
- KleidiAI build integration is proven. For the recorded Q4_K_M smoke, llama.cpp
  reported no q4_K KleidiAI kernel and no acceleration; this does not establish
  behavior for other tensor formats or hardware.
- Practical improvement thresholds are noise guardrails, not formal
  statistical significance tests.
- No candidate is claimed as production-optimal or transferable to another
  machine, runtime, model, workload, or policy.
- Broader Arm hardware validation remains future work.

Unavailable evidence is reported as unavailable. It is never plotted as zero or
converted into a favorable or unfavorable claim.
