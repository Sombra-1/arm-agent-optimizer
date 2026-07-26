# Submission screenshot evidence

These images support the Arm AI Optimization Challenge submission. They were captured at 1440 × 1000 with the locally installed Brave browser in headless mode. No browser package or browser binary was installed or downloaded.

Historical capture source: `8f2149d4190071f98edf1e024ec15c4d37fd0170`
(`v0.1.0-arm-validation`). Screenshot 01 contains a documentation overlay
prepared from that source commit, so it should be treated as an archived
illustration rather than a current repository capture.

## Evidence classification

- `01-project-overview-cloud-ai.png` is repository documentation evidence. `README.md` was rendered with GitHub's authenticated Markdown API into a temporary, GitHub-like local HTML page.
- `02-public-github-repository.png` is public repository evidence captured from `https://github.com/Sombra-1/arm-agent-optimizer`. It shows the public repository, `main` branch, description, README, and MIT license.
- `03-ci-passing.png` is real, historical software-validation evidence captured
  from public GitHub Actions run `29967500449`. It shows successful Python 3.11
  and 3.12 jobs. CI does not validate Arm performance. Use a current green CI
  capture in the final submission if the visible commit must match `main`.
- `04-synthetic-report-hero.png`, `05-fastest-candidate-rejected.png`, and `06-synthetic-funnel-pareto.png` are synthetic behavioral evidence captured from one locally generated, validated AArchTune report. They are not real Arm64 performance evidence.

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

## Regeneration requirement

The repository now has separately documented native Arm64 evidence. These
screenshots remain synthetic product-behavior illustrations and must retain
their visible synthetic labels. Native quantitative claims may be made only
from a validated native Passport and bundle.
