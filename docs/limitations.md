# Limitations

V1 supports Linux CPU inference through `llama.cpp` only. Real optimization targets AArch64. There is no streaming TTFT, concurrent multi-client load, distributed inference, GPU tuning, training, or automatic model acquisition.

Sequential service rate is workload service capacity, not concurrency throughput. Memory sampling can miss short peaks. Deterministic order, page cache, thermal state, frequency scaling, and background load can bias results; the baseline-end sentinel provides partial protection only. Practical improvement thresholds are noise guardrails, not formal significance tests.

Quality is limited to declared workload validators. Profiles must not be assumed optimal on another machine, binary, model, workload, or policy.
