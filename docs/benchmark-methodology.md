# Benchmark methodology

AArchTune uses deterministic task order, identical messages and generation settings, configurable warm-ups and repetitions, `perf_counter_ns` request durations, nearest-rank P95, and interval-based owned-process RSS/CPU sampling. Warm-ups never enter measured aggregates.

The historical baseline establishes provenance. Evaluation replays a fresh baseline before candidates and an ending baseline sentinel afterward. Low-level `llama-bench` screening is only a filter; final comparisons use real HTTP workload execution and quality validation.

Requests per minute means successful sequential measured requests divided by measured wall-clock interval. It is not multi-client concurrency. Prompt and decode rates remain separate. Non-streaming v1 does not claim client TTFT. Missing values remain unavailable rather than zero.

See [baseline](baseline-methodology.md), [screening](screening-methodology.md), and [evaluation](evaluation-methodology.md).
