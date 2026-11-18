# Controlled native Arm64 experiments for 0.2

The `feature/aarchtune-0.2` branch uses the existing manual native workflow to
run controlled experiments on `ubuntu-24.04-arm`. Main is unchanged. The proven
full-pipeline pairing is **14B with a 2,048-token context and one serving slot**.
The workflow's default B model is still 7B; dispatching without a model selection
does not reproduce the successful 14B experiment.

Run experiment A to verify early rejection:

```bash
gh workflow run real-arm64-smoke.yml \
  --ref feature/aarchtune-0.2 \
  -f mode=product-validation -f product_experiment=rejection -f product_repeats=1
```

Explicitly select the validated model for experiment B:

```bash
gh workflow run real-arm64-smoke.yml \
  --ref feature/aarchtune-0.2 \
  -f mode=product-validation -f product_experiment=optimization \
  -f product_model=qwen2.5-14b-q3-k-m -f product_repeats=1
```

The published successful comparisons used this B configuration in two separate
workflow runs. Run the B command twice to repeat that arrangement. Setting
`product_repeats=2` instead runs two searches within one hosted job.

The product mode calls `native-arm64-product.yml`; the older `preflight` and
`smoke` modes remain available. Each matrix job proves native Linux AArch64,
builds the same pinned CPU runtime, verifies model size and SHA-256, and installs
AArchTune in a project-local virtual environment. No synthetic/native bypass is
enabled in the experiment driver.

`product_experiment=optimization` runs only B after A has been verified.
`product_model=qwen2.5-14b-q3-k-m` selects the pinned 14B profile. Model and
configuration changes are recorded separately; results are not pooled. See
[measured results](native-0.2-results.md).

| Experiment / selection | Model | Workload / output mode | Measured status |
| --- | --- | --- | --- |
| A: rejection | Qwen2.5-1.5B-Instruct Q4_K_M | Original smoke / `none` | Verified rejection before planning and screening |
| B: workflow default, `qwen2.5-7b-q3-k-m` | Qwen2.5-7B-Instruct Q3_K_M | Support triage / `json_schema` | Original runtime-default attempt failed preflight at 80% task success; the current bounded-context 7B configuration has not been validated |
| B: validated selection, `qwen2.5-14b-q3-k-m` | Qwen2.5-14B-Instruct Q3_K_M | Support triage / `json_schema`, context 2,048, one slot | Two verified full pipelines; baseline retained both times; no configuration speedup |

A requires quality rejection, confirmed cleanup, and no planning, screening,
evaluation, or finalization artifacts. B requires all six stages, verified native
evidence, final bundle and Passport, and a quality-passing selected or retained
profile. The successful searches had partial screening coverage and evaluated
only two profiles each; they do not demonstrate broad search effectiveness.

The runtime pin is `1425386fd996511e1f3295e7366c38289a92a271` (`b10106`). The
immutable model revisions, sizes, and digests are recorded in the workflow and
public provenance. Both B model choices use existing immutable model profiles.

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

The current experiment driver explicitly sets B's baseline to a 2,048-token
context and one serving slot. This is recorded as a separate configuration
experiment from the original runtime-default attempts. The workload, expected
answers, JSON Schema contract, and quality policy are unchanged. The CLI exposes
the same baseline controls through `optimize --context-size --parallel-slots`;
the planner keeps its existing memory guardrails and holds context fixed across
the default search space.

The validated bounded full-pipeline CPU search uses
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
