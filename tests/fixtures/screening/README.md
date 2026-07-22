# Synthetic screening fixture

The screening tests generate a complete artifact directory from the synthetic
search plan fixture and `tests/fixtures/bin/fake-llama-bench`. All resulting
measurements carry this label:

> Synthetic low-level screening data — not Arm or model-performance evidence

The fake benchmark's deterministic formula is documented in
`docs/screening-methodology.md`. Generated artifact directories are deliberately
created under pytest temporary directories rather than committed as measured
results. This prevents synthetic values from being mistaken for real benchmark
evidence while still testing full plan-to-screening validation.

