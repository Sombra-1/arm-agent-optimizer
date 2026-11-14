# Baseline quality before optimization

The 0.2 development branch adds a usable diagnosis when a model fails the workload.
It keeps the original runtime, benchmark runner, validators, quality policies, and
final evidence bundle. A passing preflight permits tuning; it establishes no speedup.

## Set up the CLI and create a workload

From a checkout, run `scripts/setup.sh` (`--dev` includes test and lint tools).
The script installs into this project's `.venv`; it does not install a runtime or
download models. Activate the environment and inspect the host:

```bash
source .venv/bin/activate
aarchtune doctor
aarchtune workload init --output my-workload.jsonl
aarchtune workload validate my-workload.jsonl
```

The starter routes five support tickets, including an ambiguous request and a
negated data-loss case. Each task checks ticket ID, queue, priority, JSON shape,
request success, and forbidden content. It is a small, realistic starting point;
add representative tasks before using its results for your own application.
Its validators are tested with synthetic correct and incorrect answers. Native
GGUF quality and performance have **not** been demonstrated for this workload.

Edit three independent parts of a task:

- `messages`: the input and instructions a user would provide.
- `output_schema`: the allowed output contract sent only in JSON Schema mode.
- `validators`: the scoring rules, including expected decisions never sent by the client.

Keep the output contract broad enough to allow wrong answers. Do not encode a
task's expected answer as a generation-schema constant.

## Run a quality preflight

Use an existing compatible `llama-server` and a local GGUF:

```bash
aarchtune preflight \
  --binary /path/to/llama-server --model /path/to/model.gguf \
  --workload my-workload.jsonl --response-format json_schema \
  --output-dir results/preflight
```

By default, measured repetitions equal the selected quality policy's minimum.
`--quality-policy` selects an alternative policy; `--repetitions` can override the
count. The baseline retains the existing request, timing, memory, and validation
artifacts. `preflight.json` and the self-contained `report.html` contain policy
checks, per-task failure categories, evidence hashes, and next steps without
generated response bodies. Raw baseline artifacts still contain model responses.

Diagnose existing evidence without starting inference:

```bash
aarchtune preflight --baseline-dir results/baseline \
  --output-dir results/diagnosis --json
```

| Exit | Meaning | Next step |
| --- | --- | --- |
| `0` | Baseline quality passed | Tune runtime settings and validate each candidate |
| `4` | Quality policy failed | Investigate the reported failures and repeat preflight |
| `3` | Evidence incomplete | Fix the run, repetition count, or missing metrics/validators |
| `1` | Invalid inputs or report-write failure | Correct the command or paths |

Formatting failures are distinguished from incorrect values in successfully
parsed outputs. A failed exact-value check after JSON parsing failed is not
independent evidence of a semantic mistake. Transport failures suggest checking
logs, memory, or timeouts; formatting failures suggest checking the chat template,
token budget, and supported structured output. Wrong values suggest reviewing
instructions or model capability. These are diagnostic leads, not proven causes.

## Optional JSON Schema generation

`--response-format` accepts `none` (default), `json_object`, and `json_schema` on
`baseline`, `preflight`, and `optimize`. JSON Schema mode requires an explicit
`output_schema` on **every** workload task, checked before starting the server.
The schemas use the same strict schema validation and external-reference
rejection as existing schema validators. Evaluation validators remain active in
every output mode.

The client uses the nested schema wrapper documented by
[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md):

```json
{"response_format":{"type":"json_schema","json_schema":{"name":"workload_output","schema":{"type":"object","properties":{"queue":{"type":"string"}},"required":["queue"]}}}}
```

The runtime supports a subset of JSON Schema. A valid schema in the workload
does not prove that a particular server build supports every keyword. HTTP 400
and 422 rejections are surfaced; the client never silently falls back to plain
generation. Acceptance does not prove enforcement or semantic correctness.
`response_format_applied` records the request constraint sent, not an independent
proof of decoder behavior. Request metrics retain the exact schema used.

## Stop expensive tuning when the baseline fails

`aarchtune optimize` now diagnoses its baseline before planning or screening.
When quality or evidence fails, it returns exit `4` or `3`, preserves the baseline,
and writes `preflight/report.html` and `preflight/preflight.json`. It recommends
retaining the current configuration and selects no deployment profile. This
terminal diagnostic result is accepted by `optimize validate`; validation means
the diagnostic evidence is consistent, not that model quality passed.

A resumed blocked run reuses the baseline and repeats the diagnosis. To change
the model, workload, output mode, or policy, start a new output directory.
Output mode is preserved in search profiles, baseline drift checks, and candidate
requests. Comparisons therefore retain the same generation contract. For a
selected constrained profile, clients must continue sending the recorded output
mode and per-task schema: launching the server alone does not enforce them.

## Native evidence still required

On native Linux Arm64, use the prepared script with an already available local
model and binaries:

```bash
scripts/native-preflight-demo.sh \
  /path/to/llama-server /path/to/llama-bench /path/to/model.gguf \
  /path/to/new-evidence-directory
```

It runs three preflight repetitions, proceeds to a bounded latency optimization
only after quality passes, and validates the resulting evidence bundle. A failed
preflight leaves a useful diagnostic report and stops the script.

Before publishing a performance claim, retain the model/runtime/workload hashes,
native hardware report, complete repeated quality evidence, baseline drift checks,
and selected candidate's measured improvements under the unchanged policy.
`baseline_retained` and `no_eligible_candidate` remain legitimate outcomes. A
quality-passing native optimization comparison is an outstanding milestone, not
a result established by the synthetic tests or this development branch.
