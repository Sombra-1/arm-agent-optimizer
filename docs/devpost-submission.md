# Devpost submission draft

## Challenge track

**Cloud AI**

AArchTune improves AI inference on Arm-powered cloud and server systems by finding a fast, memory-aware, and quality-preserving `llama.cpp` runtime configuration for a representative application workload.

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

## Challenge-period confirmation

AArchTune was created and substantially implemented during the Arm AI Optimization Challenge period. The initial validated public MVP was published on July 22, 2026. The public Git history records the implementation and the narrowly scoped release-validation fixes completed before submission.

## Suggested submission screenshots

1. `docs/screenshots/01-project-overview-cloud-ai.png` — project overview and Cloud AI track.
2. `docs/screenshots/03-ci-passing.png` — public CI validation.
3. `docs/screenshots/05-fastest-candidate-rejected.png` — synthetic demonstration of quality-constrained rejection.
4. `docs/screenshots/06-synthetic-funnel-pareto.png` — synthetic candidate funnel and Pareto evidence.

The performance screenshots are synthetic behavioral demonstrations and must be replaced or supplemented with validated real Arm64 screenshots before making quantitative Arm-performance claims.

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

MIT. Repository: https://github.com/Sombra-1/arm-agent-optimizer
