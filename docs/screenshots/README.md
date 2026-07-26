# Submission screenshot evidence

These images support the Arm Create 2026 Cloud AI submission. They were
captured at 1440 × 1000 with the existing Brave browser in headless mode. No
browser package or browser binary was installed or downloaded.

Repository-facing capture source:
`60c99aad29c13f42151af60fdb55d0489b5e6c2a`
(`docs: preserve native Arm64 submission evidence`).

Capture date: `2026-07-26`.

The browser used an isolated, unauthenticated profile against the real public
GitHub pages. No local GitHub-like UI or documentation overlay was substituted.

## Evidence classification

- `01-project-overview-cloud-ai.png` is a current public GitHub README capture.
  It shows Arm Create 2026 / Cloud AI, the live CI badge, native Arm64,
  quality gating, reproducible evidence, and the validated safety outcome.
- `02-public-github-repository.png` is a current public repository capture from
  <https://github.com/Sombra-1/arm-agent-optimizer>. It shows `main`, the
  current description/topics, README, MIT license, and project identity.
- `03-ci-passing.png` is current software-validation evidence from public CI
  run
  [`30204488854`](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30204488854).
  It shows the evidence-preservation commit succeeded with both Python 3.11 and
  3.12 jobs. CI does not validate Arm performance.
- `04-synthetic-report-hero.png`, `05-fastest-candidate-rejected.png`, and
  `06-synthetic-funnel-pareto.png` are retained synthetic behavioral evidence
  from one locally generated, validated AArchTune report. They are not real
  Arm64 performance evidence and were not changed during the repository-facing
  refresh.

## Synthetic scenario

The synthetic report was generated in an external disposable demo directory with:

```text
FAKE_LLAMA_SCENARIO=fast-quality-regression
FAKE_LLAMA_BENCH_SCENARIO=healthy-jsonl
workload=workloads/smoke-test.jsonl
goal=balanced
baseline repetitions=2
evaluation repetitions=2
warm-up requests=1
advanced candidates=6
```

The run explicitly used `--allow-synthetic` and `--allow-non-arm-development`. Before capture, `aarchtune optimize validate`, `aarchtune finalize validate`, and `aarchtune passport verify` all passed.

Every synthetic performance screenshot visibly includes `SYNTHETIC TEST EVIDENCE` and `Not Arm or model-performance evidence`. Synthetic measurements demonstrate product behavior only; they must not be presented as Arm64, KleidiAI, or real-model performance.

## Permanent native evidence

The repository now permanently preserves the original reviewed native artifact
and checksum in [`docs/evidence/`](../evidence/README.md), because the Actions
copy had temporary retention. This preservation does not turn a single native
smoke into repeatability proof.

Screenshots 04–06 remain synthetic product-behavior illustrations and must
retain their visible synthetic labels. Native quantitative claims may be made
only from the validated native Passport and bundle.
