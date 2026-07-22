# Baseline execution methodology

AArchTune's baseline command executes exactly one user-selected `llama-server`
configuration. It does not search configurations, rank results, or claim that the
configuration is optimal.

## Reproducibility and provenance

Each run records the detected machine, absolute runtime and model paths, exact server argument
list, supported flags, redacted environment overrides, and SHA-256 hashes of the runtime binary,
model, and exact workload bytes. Model and binary hashes use bounded-memory streaming reads.
Results are specific to that machine, binary, model, workload, and generation settings.

Synthetic fake-server results are labelled as fixtures and are not Arm64 or model-performance
evidence.

## Warm-up and measured order

Warm-up task selection cycles deterministically through workload order. Warm-up request IDs and
request-success outcomes are recorded, but their request timing, token fields, validation results,
and quality outcomes are excluded from measured aggregates. Measured requests traverse workload
order for every repetition without randomization.

## Timing and throughput

End-to-end latency uses `time.perf_counter_ns()`. UTC timestamps provide provenance but are not
used to calculate duration. P95 uses the nearest-rank method: `ceil(0.95 * n)`. Requests per minute
is successful measured requests divided by the measured wall-clock interval, multiplied by 60.

The client is non-streaming, so true time to first token is unavailable. Total latency is never
substituted for TTFT. Optional `usage` and `timings` fields are normalized conservatively, retain
their exact source path, and remain explicitly unavailable when absent or malformed.

## Process measurements

`psutil` samples only the owned server PID and descendants discovered from that PID. Samples are
streamed to JSONL with startup, warm-up, measured, and shutdown phase labels. The sampler stops and
joins before final summaries are marked complete. Very fast synthetic runs may have few samples;
unavailable measured-phase statistics are represented as null with a reason, never as zero.

## Quality

The declared workload validators run for every measured attempt. Runtime request success, task
validation success, JSON validity, and individual validator pass rate are reported separately.
Poor model output does not make an otherwise complete baseline command fail.

## Artifacts and privacy

Important JSON documents are written through a temporary file, flushed, and atomically replaced.
Raw responses are stored once in `raw-attempts.jsonl`; request metrics reference the attempt ID.
Responses can contain sensitive workload data. Artifacts remain local and are never uploaded by
AArchTune. Users should protect and remove run directories according to their data-retention policy.

Partial, failed, and interrupted runs retain completed JSONL records, a terminal manifest, bounded
server logs, process data when available, and `failure.json`. Stack traces and secret-like
environment values are not persisted as ordinary failure messages.

## Limitations

- Baseline execution is sequential.
- Server-reported timing fields vary by `llama.cpp` version.
- Process sampling is interval-based and can miss brief peaks between samples.
- Client-derived token rates divide counts by total request latency; they are labelled separately
  from server-reported prompt and generation rates.
- No quality policy, configuration comparison, or optimization is performed in this phase.
