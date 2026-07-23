# Submission screenshot evidence

These images support the Arm AI Optimization Challenge submission. They were captured at 1440 × 1000 with the locally installed Brave browser in headless mode. No browser package or browser binary was installed or downloaded.

Source commit: `8f2149d4190071f98edf1e024ec15c4d37fd0170` (`v0.1.0-arm-validation`). The documentation overlay shown in screenshot 01 includes the uncommitted submission-positioning edits prepared from that source commit.

## Evidence classification

- `01-project-overview-cloud-ai.png` is repository documentation evidence. `README.md` was rendered with GitHub's authenticated Markdown API into a temporary, GitHub-like local HTML page.
- `02-public-github-repository.png` is public repository evidence captured from `https://github.com/Sombra-1/arm-agent-optimizer`. It shows the public repository, `main` branch, description, README, and MIT license.
- `03-ci-passing.png` is real software-validation evidence captured from public GitHub Actions run `29967500449`. It shows the successful CI status and Python 3.11 and 3.12 jobs. CI does not validate Arm performance.
- `04-synthetic-report-hero.png`, `05-fastest-candidate-rejected.png`, and `06-synthetic-funnel-pareto.png` are synthetic behavioral evidence captured from one locally generated, validated AArchTune report. They are not real Arm64 performance evidence.

## Synthetic scenario

The synthetic report was generated outside the repository at `/tmp/aarchtune-submission-synthetic` with:

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

Regenerate or supplement the synthetic performance screenshots after the first validated real Arm64 optimization run. Quantitative Arm claims may be made only from that run's validated Passport and bundle. Do not remove the synthetic labels from retained fixture screenshots.
