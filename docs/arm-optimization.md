# Arm64 optimization

AArchTune uses physical-core evidence, CPU features, memory headroom, NUMA
topology, runtime-supported flags, model size, workload context, and baseline
RSS to plan representative configurations. It never assumes that all cores,
larger batches, mmap changes, prompt caching, or higher parallelism are
beneficial.

Real optimization requires Linux AArch64. x86 and synthetic execution require
explicit development opt-in and cannot produce Arm claims.

KleidiAI was compiled into the pinned native llama.cpp build. For the recorded
Qwen2.5-1.5B Q4_K_M smoke, llama.cpp reported no q4_K KleidiAI kernel and no
acceleration; available kernels were reported for Q4_0 and Q8_0. No end-to-end
KleidiAI speedup is claimed, and this model-specific result is not generalized
to other tensor formats or hardware.

Experimental model-family work is frozen for submission. The build helper and
runbook remain implementation documentation, not instructions to generate new
submission evidence.

See [build-llama-arm64.sh](../scripts/build-llama-arm64.sh),
[architecture](architecture.md), and the
[final validation report](FINAL_VALIDATION_REPORT.md). The reviewed native
result is available through the
[permanent evidence index](evidence/README.md); verify the archived ZIP before
extracting it outside the repository.
