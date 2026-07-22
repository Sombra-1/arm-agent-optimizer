# Devpost submission draft

## Project overview

AArchTune is a local, open-source autotuner that selects a fast `llama.cpp` CPU configuration without accepting measured regressions in a representative AI workload.

## Purpose and problem

Arm servers expose different core, memory, NUMA, and instruction characteristics. A low-level tokens-per-second result does not prove an agent still returns valid schemas or safe actions. AArchTune connects systems tuning to application correctness.

## Functionality

The pipeline detects hardware, records a fixed baseline, plans a bounded search, screens equivalent low-level signatures, evaluates advanced profiles through isolated `llama-server` processes, applies absolute and baseline-relative quality gates, checks drift, selects or retains a profile, and produces a Passport, report, and safe deployment bundle.

## Output

The final artifacts include a self-contained report, verifiable Optimization Passport, Pareto evidence, selected YAML, safe run/reproduction scripts, and SHA-256 checksums.

## Arm-specific value

AArchTune uses Arm feature evidence, core topology, NUMA, memory headroom, and conservative KleidiAI detection. Results remain explicitly hardware-specific.

## Developer experience

One command runs the pipeline; native stage commands remain available for debugging. Resume reuses only validated evidence.

## Setup and validation

See `README.md` and `docs/real-arm-validation-runbook.md`. Tests use small synthetic fixtures without models or Arm claims.

## Real results

- Service-rate improvement: **[INSERT REAL ARM64 RESULT]**
- P95 latency improvement: **[INSERT REAL ARM64 RESULT]**
- Peak RSS change: **[INSERT REAL ARM64 RESULT]**
- Quality preservation: **[INSERT REAL ARM64 RESULT]**
- Arm machine and model: **[INSERT REAL ARM64 RESULT]**

## Why it should win

AArchTune makes the quality/performance tradeoff inspectable: the headline can be a faster candidate rejected because correctness regressed. It packages that decision as reproducible evidence rather than an unexplained score.

## Limitations

Linux CPU-only v1, sequential workload service rate, non-streaming TTFT unavailable, and no universal optimality claim.

## License and repository

MIT. Repository: **[INSERT PUBLIC REPOSITORY LINK]**
