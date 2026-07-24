# Native GitHub Actions Arm64 validation

The `Native Arm64 Smoke Validation` workflow gives maintainers without physical
Arm hardware a bounded way to exercise AArchTune on a standard native
GitHub-hosted Linux Arm64 runner. It is evidence collection, not deployment.
The workflow is manual-only (`workflow_dispatch`), has read-only repository
permissions, never commits or pushes, and has no automatic or scheduled trigger.

## Modes

`preflight` proves the runner architecture, records non-sensitive hardware
facts, installs AArchTune from the checkout, fetches and builds the pinned
llama.cpp revision with KleidiAI enabled, verifies the three required binaries,
runs `aarchtune doctor`, uploads reviewed evidence, and cleans up. It does not
download a model, run inference, or optimize.

`smoke` performs the preflight and then validates the unchanged five-task
`workloads/smoke-test.jsonl` workload. It downloads and verifies the pinned Qwen
model, runs a short CPU loading probe, and runs AArchTune with two baseline
repetitions, two evaluation repetitions, one warm-up request, and four advanced
candidates. It does not enable synthetic or non-Arm development modes.

Dispatch either mode from an authenticated GitHub CLI:

```bash
gh workflow run real-arm64-smoke.yml \
  --repo Sombra-1/arm-agent-optimizer \
  --ref main \
  -f mode=preflight

gh workflow run real-arm64-smoke.yml \
  --repo Sombra-1/arm-agent-optimizer \
  --ref main \
  -f mode=smoke
```

Only one smoke run should be dispatched for a single validation task. The
workflow concurrency group cancels an older run for the same ref.

## Pinned external inputs

llama.cpp is fetched only from
`https://github.com/ggml-org/llama.cpp.git`. Release `b10106`, selected on
2026-07-24 with
`gh api repos/ggml-org/llama.cpp/releases/latest`, resolves to the immutable
commit:

```text
1425386fd996511e1f3295e7366c38289a92a271
```

That revision was inspected for `GGML_CPU_KLEIDIAI`, `llama-server`,
`llama-bench`, and `llama-cli`. The workflow requires the KleidiAI CMake option
and compile definition; configuration cannot silently fall back.

Smoke mode uses:

| Field | Pinned value |
| --- | --- |
| Repository | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` |
| Revision | `91cad51170dc346986eccefdc2dd33a9da36ead9` |
| File | `qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| Quantization | `Q4_K_M` |
| License | Apache-2.0 |
| Size | 1,117,320,736 bytes |
| SHA-256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |

The direct Hugging Face URL includes both the revision and filename. The model
is downloaded without a token only in smoke mode, checked for a plausible exact
size and SHA-256, and deleted during cleanup.

## Evidence and privacy boundary

Preflight artifacts contain `runner/`, `hardware-report.json`, `llama-build/`,
`versions/`, `checksums/`, `workflow-summary.txt`, cleanup proof, limitations,
and the privacy-scan result.

Smoke artifacts add model provenance (never weights), the workload hash and
validation, the compact validated final bundle, the exact stage files referenced
by its Passport, Pareto evidence, selected profile when present, validation
outputs, cleanup state, and a concise metrics summary. Retention is 14 days.

The reviewed upload directory excludes GGUF weights, model caches, raw model
responses, raw attempt/response data, server logs, process samples, complete
candidate directories, environment dumps, home-directory content, credentials,
SSH material, cookies, and authenticated URLs. A final content/name scan blocks
the upload if a forbidden pattern or file is found. No model weights are
committed or uploaded, and no raw responses are published.

## Outcomes and verification

The optimizer exit codes retain their native meaning:

- `0`: a candidate was selected or the baseline was retained.
- `3`: evaluation was invalidated by drift.
- `4`: no candidate was eligible.

Exit 3 and 4 remain workflow failures. Their validated diagnostic bundle is
uploaded when safely available; the workflow does not change the workload or
quality policy after an unfavorable result.

For an exit-0 result, the workflow requires all three validations:

```bash
aarchtune optimize validate OPTIMIZATION_DIRECTORY
aarchtune finalize validate OPTIMIZATION_DIRECTORY/final
aarchtune passport verify OPTIMIZATION_DIRECTORY/final/optimization-passport.json
```

After downloading the artifact, install the same AArchTune commit and run the
Passport verification against
`optimization/final/optimization-passport.json`. The artifact preserves only
the referenced stage files needed for integrity verification, not raw
optimization directories. Also inspect `passport-verification.json`,
`bundle-manifest.json`, `report-data.json`, `cleanup-proof.txt`, and
`privacy-scan.txt`.

KleidiAI build support is proven from CMake configuration and compile evidence.
Runtime status is `verified` only if the bounded model-loading probe emits
recognized positive KleidiAI evidence. A missing log line is recorded as
`unknown`, not as a positive or negative claim.

## Interpretation limits

GitHub-hosted runners are ephemeral and shared benchmark environments, not
dedicated production servers. Neighbors, page cache, thermal state, and runner
allocation can affect measurements. Sequential requests per minute is not
concurrent-client throughput.

One successful smoke run is insufficient for a final performance claim. Before
reporting a median, obtain at least three independent successful native runs
with identical pinned inputs and verify every Passport and bundle. Report the
median together with run-to-run spread and the recorded hardware limitations.
