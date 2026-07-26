# Arm64 optimization

AArchTune uses physical-core evidence, CPU features, memory headroom, NUMA
topology, runtime-supported flags, model size, workload context, and baseline
RSS to plan representative configurations. It never assumes that all cores,
larger batches, mmap changes, prompt caching, or higher parallelism are
beneficial.

Real optimization requires Linux AArch64. x86 and synthetic execution require
explicit development opt-in and cannot produce Arm claims.

KleidiAI build integration was enabled and verified at build time in the native
workflow. Runtime activation remains unknown; AArchTune does not infer it from
Arm architecture alone.

Experimental model-family work is frozen for submission. The build helper and
runbook remain implementation documentation, not instructions to generate new
submission evidence.

See [build-llama-arm64.sh](../scripts/build-llama-arm64.sh),
[architecture](architecture.md), and the
[final validation report](FINAL_VALIDATION_REPORT.md).
