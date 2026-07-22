# Arm64 optimization

AArchTune uses physical cores where available, CPU features, memory headroom, NUMA topology, runtime-supported flags, model size, workload context, and baseline RSS to plan representative configurations. It never assumes all cores, larger batches, mmap changes, prompt caching, or higher parallelism are beneficial.

Real optimization requires Linux AArch64. x86 and synthetic execution require explicit development opt-in and cannot produce Arm claims. KleidiAI status is `verified` only from recognized positive build/runtime evidence; absence remains `unknown`, while `not_detected` requires affirmative negative evidence.

Use [build-llama-arm64.sh](../scripts/build-llama-arm64.sh) and follow the [real Arm runbook](real-arm-validation-runbook.md).
