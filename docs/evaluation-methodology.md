# Real-workload evaluation and selection

`aarchtune evaluate` is the first phase that makes a profile selection. It loads
only candidates advanced by a validated screening run and replays the original
JSONL workload through a fresh `llama-server` process for each profile.

## Isolation and ordering

The deterministic order is `baseline-start`, advanced profiles in search-plan
order, then `baseline-end`. Every entry gets a new server, sampler, readiness
check, identical warm-up count, workload task order, generation settings,
repetitions, and request timeout. Warm-up requests are excluded from measured
performance and quality. A process is never reused across profiles.

Stable ordering can introduce thermal and temporal bias. AArchTune does not
clear the operating-system page cache or use elevated privileges. The ending
baseline sentinel detects substantial drift but cannot eliminate page-cache,
thermal, or background-load effects.

## Quality policy

The default policy applies absolute floors to request success, task success,
JSON validity, and validator pass rate. It also limits the absolute
percentage-point drop from the fresh baseline. For example, `0.01` permits a
one-percentage-point drop, not a one-percent relative decrease.

Timeouts, evidence completeness, repetitions, and critical validator types are
separate gates. Critical validator failure counts and rates cannot increase.
An inherited baseline critical failure is recorded but does not permit another
failure.

## Ranking and practical improvement

Only completed, comparable, quality-passing candidates are ranked. Latency uses
real P95 request latency; throughput uses sequential successful requests per
minute; memory uses sampled measured-phase peak RSS. Balanced ranking uses:

```text
0.35 × normalized requests per minute
+ 0.25 × normalized inverse P95 latency
+ 0.20 × normalized inverse measured peak RSS
+ 0.10 × normalized repetition consistency
+ 0.10 × normalized quality margin
```

Screening scores remain provenance and never enter final ranking. A practical
two-percent improvement guardrail (one score point for balanced ranking) avoids
switching profiles for tiny observed differences. This is a noise guardrail,
not formal statistical significance.

Sequential requests-per-minute is not multi-client concurrency throughput.
Requests are non-streaming, so client-measured time to first token remains
unavailable. Total latency is never substituted for TTFT.

## Synthetic fixture behavior

The fake server uses artificial delays based on actual thread, batch, cache,
mmap, and parallel arguments. Evaluation labels distinguish the two baseline
sentinels from candidate runs. Synthetic scenarios deliberately introduce
quality regressions, failures, memory allocations, or end-sentinel drift. These
formulas test orchestration and policy behavior only and do not model Arm or
`llama.cpp` performance.

Every synthetic evaluation is labelled:

```text
Synthetic real-workload measurements — not Arm performance evidence
```

Raw responses stay in each isolated run directory and are never uploaded.
