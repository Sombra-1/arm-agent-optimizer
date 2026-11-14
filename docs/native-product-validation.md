# Controlled native Arm64 experiments for 0.2

The `feature/aarchtune-0.2` branch uses the existing manual native workflow to
run two separate experiments on `ubuntu-24.04-arm`. Main is unchanged. Dispatch:

```bash
gh workflow run real-arm64-smoke.yml \
  --ref feature/aarchtune-0.2 \
  -f mode=product-validation -f product_repeats=2
```

The product mode calls `native-arm64-product.yml`; the older `preflight` and
`smoke` modes remain available. Each matrix job proves native Linux AArch64,
builds the same pinned CPU runtime, verifies model size and SHA-256, and installs
AArchTune in a project-local virtual environment. No synthetic/native bypass is
enabled in the experiment driver.

`product_experiment=optimization` runs only B after A has been verified.
`product_model=qwen2.5-14b-q3-k-m` selects the existing immutable 14B profile for a
recorded model-only follow-up. The default B profile remains the original 7B
choice; results from different models are not pooled. See
[measured results](native-0.2-results.md).

| Experiment | Model | Workload / output mode | Acceptance |
| --- | --- | --- | --- |
| A: rejection | Qwen2.5-1.5B-Instruct Q4_K_M | Original smoke workload / `none` | Quality rejection, confirmed cleanup, and no planning, screening, evaluation, or finalization artifacts |
| B: full pipeline | Qwen2.5-7B-Instruct Q3_K_M | Support-triage starter / `json_schema` | All six stages complete; native evidence, final bundle, and passport validate; selection is a passing candidate or retained baseline |

The runtime pin is `1425386fd996511e1f3295e7366c38289a92a271` (`b10106`). The
immutable model revisions, sizes, and digests are recorded in the workflow and
public provenance. Model B reuses the existing verified 7B model profile.

Experiment B performs single-task protocol probes in `none`, `json_object`, and
`json_schema` modes before the full workload run. It also sends an unknown format
directly to the native server and requires HTTP 400 or 422. These probes are
protocol checks, not workload-level quality results. The full search uses the
same JSON Schema contract throughout; every measured request is checked against
its task's temperature, seed, token limit, streaming setting, output mode, and
generation schema.

The default quality policy is unchanged. The initial attempts used two baseline
repetitions, three evaluation repetitions, eight planned profiles, four advanced
profiles, and a 30-minute search deadline. Screening uses
the fixed, short prefill/decode scenarios in `configs/native-product-screening.yaml`.
Hosted jobs have a 120-minute timeout. Experiment A runs once; B runs twice
by default on the same runner. All attempts and outcomes are retained in order.

The bounded-context follow-up explicitly sets B's baseline to a 2,048-token
context and one serving slot. This is recorded as a separate configuration
experiment from the original runtime-default attempts. The workload, expected
answers, JSON Schema contract, and quality policy are unchanged. The CLI exposes
the same baseline controls through `optimize --context-size --parallel-slots`;
the planner keeps its existing memory guardrails and holds context fixed across
the default search space.

For this first full-pipeline CPU validation, the bounded-context follow-up uses
four planned profiles, two advanced profiles, and two evaluation repetitions
(the unchanged policy minimum), with a 45-minute search deadline. This limits
14B inference cost while retaining independent quality validation and baseline
drift checks. It is a small validation search, not an exhaustive tuning study.

If B's baseline fails, the search stops and the experiment fails its full-pipeline
acceptance criterion. If a passing baseline is retained, the experiment may pass
without establishing a speedup. No quality threshold, expected answer, or
selection-improvement threshold is relaxed to obtain a positive result.

The repeated-run summary reports shared-candidate quality decisions, changes in
candidate membership, and the observed range of baseline P95 latency. These are
descriptive results from a shared hosted runner, not confidence intervals or
evidence from independent hosts. Per-run drift checks still apply unchanged.

Artifacts named `aarchtune-0.2-native-{rejection,optimization}-RUN-ATTEMPT` contain
response-free projections, diagnostic/final reports when available, actual
hardware and source/model provenance, request-audit results, and original
artifact hashes. Original bundles and passports are validated **before**
projection and path redaction. The public projections do not replace the
original canonical evidence and should not be passed to bundle/passport
validators. Model weights, generated responses, and server logs are excluded.
Artifacts are uploaded only after the public-evidence scan passes.
