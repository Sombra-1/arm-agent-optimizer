# Native Arm64 quality diagnosis: run 30119492016

## Conclusion

Run `30119492016` does not show an HTTP request-format incompatibility. AArchTune sent
OpenAI-compatible chat messages to llama.cpp's `/v1/chat/completions` endpoint, the exact
pinned GGUF contains a native chat template, and the pinned llama.cpp server automatically
loaded and applied that template. All measured requests completed, with no timeouts.

The quality failure is best classified as a **model-capability limitation** of the
Q4_K_M-quantized Qwen2.5-1.5B-Instruct model on this strict workload. The runtime did not use
constrained decoding: JSON was requested in natural-language system messages, but no stop
sequence, `response_format`, grammar, JSON Schema, explicit chat-template override, or
chat-template option was sent. That absence made the workload a test of the model's native
instruction-following and structured-output ability. It is a relevant configuration
characteristic, but the evidence does not prove a malformed request or template defect.

At the time of this diagnosis, the smallest justified follow-up was a
model-only substitution to a more capable instruction model while retaining
the workload, quality policy, screening settings, llama.cpp pin, benchmark
repetitions, and candidate count. Those follow-ups were subsequently completed,
and model-family experimentation is now frozen.

No quality threshold was weakened, and this diagnosis changes no product behavior or policy.

## 1. Run provenance and evidence scope

| Field | Observed value |
|---|---|
| GitHub Actions run | `30119492016`, attempt 1 |
| Workflow / event | `Native Arm64 Smoke Validation` / `workflow_dispatch` |
| Run time | 2026-07-24 19:05:55Z to 20:00:43Z |
| Repository commit | `8376178cc37d00d64fccc2d0276161e0c8f7fd23` |
| Runner | native `aarch64`, 4-core Neoverse-N2, 16,722,046,976 bytes RAM |
| llama.cpp | release `b10106`, commit `1425386fd996511e1f3295e7366c38289a92a271` |
| Model | `Qwen/Qwen2.5-1.5B-Instruct-GGUF`, revision `91cad51170dc346986eccefdc2dd33a9da36ead9`, `qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| Model SHA-256 | `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e` |
| Workload | `workloads/smoke-test.jsonl`, SHA-256 `31d4de96109aaee5382da300a947c0677d1b52d9d423ca6de4036513dc9f57db` |
| Quality policy | `configs/default-quality-policy.yaml`, SHA-256 `597add6069f4565230d0eea13cccd8ffa0997b981bfae15f1fc8313ca99ef67f` |
| Evaluation | 5 tasks × 2 repetitions; 1 warm-up; 4 advanced/evaluated profiles |
| Evaluation result | completed; 4 quality rejections; no eligible candidate; exit code 4 |

The requested temporary review directory was not present when this diagnosis
began. The still-available sanitized artifact
`aarchtune-real-arm64-smoke-30119492016-1` (artifact ID `8607905782`) was therefore inspected
read-only through the GitHub Actions API. Inspected files were:

- `workflow-summary.txt`, `smoke-summary.json`, and `model-provenance.json`;
- baseline and baseline-start `baseline-summary.json` and `manifest.json`;
- evaluation config, execution plan, manifest, summary, selection, and quality policy;
- final `report-data.json` and `optimization-passport.json`;
- the run/job metadata and configuration-only run-log lines;
- the repository workload, evaluator/validator schemas, runtime client, server command builder,
  baseline/candidate runners, workflow, and runtime-client tests;
- the pinned llama.cpp source at the exact runtime commit; and
- the metadata region of the exact revision-pinned GGUF, without retaining the model.

The sanitized public artifact intentionally omits `raw-attempts.jsonl`, per-candidate run
directories, and server logs even though the private manifests list those files. Consequently,
this report does not publish response bodies and does not invent per-attempt response-shape
detail that the retained evidence cannot establish.

## 2. Baseline and candidate quality totals

The initial optimization baseline has complete count evidence. Every evaluated profile reports
the same three quality rates. Since every profile has 10 attempts and 50 validator applications,
the rates correspond to the counts shown below. Candidate request success is also 10/10: the
quality policy has a 0.98 request-success minimum and none of the candidate decisions contains a
request-success violation; with 10 attempts, any failure would have violated that minimum.

| Execution | Role/settings difference | Requests | Valid JSON | Validators passed | Fully passing tasks |
|---|---|---:|---:|---:|---:|
| Initial baseline | optimization baseline | 10/10 | 3/10 | 24/50 | 0/10 |
| Baseline-start | drift sentinel | 10/10 | 3/10 | 24/50 | 0/10 |
| `baseline` | first of four evaluated profiles | 10/10 | 3/10 | 24/50 | 0/10 |
| `balanced-p1-nocache` | `parallel_slots=1` | 10/10 | 3/10 | 24/50 | 0/10 |
| `balanced-nocache-nommap` | `mmap=false` | 10/10 | 3/10 | 24/50 | 0/10 |
| `balanced-cache` | prompt cache enabled | 10/10 | 3/10 | 24/50 | 0/10 |
| Baseline-end | drift sentinel | 10/10 | 3/10 | 24/50 | 0/10 |

The run's “4 advanced candidates” includes the `baseline` profile plus three non-baseline
variants. The initial optimization baseline and two drift sentinels are separate executions.
Baseline-start and baseline-end had exactly zero quality drift. The four evaluated profiles have
identical aggregate semantic quality. The retained evidence proves no candidate improved any
quality rate; it cannot prove byte-for-byte identical responses because those bodies were
correctly excluded from the sanitized artifact.

## 3. Per-task outcomes

The initial baseline's per-category summary proves that both repetitions of every task failed.
Its validator-type totals also prove the task-specific results below where a validator occurs in
only one task, or where a validator passed or failed every occurrence. “Not attributable” means
the aggregate retained evidence pools that validator across two tasks, so assigning individual
repetitions would be fabrication.

| Task | Request success | Valid JSON | Validators known to pass | Validators known to fail | Sanitized failure classification |
|---|---:|---|---|---|---|
| `smoke-incident-001` | 2/2 | not attributable; 3/10 globally | `request_succeeded` | `exact_value` 2/2 | otherwise incorrect answer: `wrong_allowed_value` or missing/unparseable `category`; `valid_json`, `json_schema`, and `allowed_value` split with planning are unavailable per task |
| `smoke-recovery-001` | 2/2 | not attributable; 3/10 globally | `request_succeeded`, `not_contains_text` 2/2 | `exact_value` 2/2 | wrong action or missing/unparseable `action`; no forbidden delete text; `required_fields` split with summary is unavailable per task |
| `smoke-summary-001` | 2/2 | not attributable; 3/10 globally | `request_succeeded`, `maximum_response_length` 2/2 | `exact_value` 2/2 | wrong or missing/unparseable `root_cause`; response length was acceptable; `required_fields` split with recovery is unavailable per task |
| `smoke-contradiction-001` | 2/2 | not attributable; 3/10 globally | `request_succeeded` | `exact_value`, `contains_text`, `regex_match`, each 2/2 | wrong or missing/unparseable `assessment`, missing required phrase, and response not shaped as a brace-delimited object |
| `smoke-planning-001` | 2/2 | not attributable; 3/10 globally | `request_succeeded`, `not_contains_text` 2/2 | at least one of `valid_json`, `json_schema`, or `allowed_value` on every repetition | malformed/wrong-schema/missing or wrong first tool; no forbidden invented `delete_files` text |

This table applies exactly to the initial baseline and its two repetitions. All five categories
also failed both baseline-start repetitions. Candidate-level per-task records were not retained
in the sanitized artifact, so only their identical aggregate totals—not task-by-task response
identity—can be asserted.

## 4. Validator failure counts

The complete initial-baseline validator totals are:

| Validator | Passed | Failed | Interpretation |
|---|---:|---:|---|
| `request_succeeded` | 10 | 0 | transport and completion extraction succeeded |
| `valid_json` | 3 | 7 | dominant response-shape failure |
| `json_schema` | 2 | 2 | incident/planning pool |
| `required_fields` | 1 | 3 | recovery/summary pool |
| `exact_value` | 0 | 8 | every exact semantic answer was wrong, missing, or unavailable because JSON parsing failed |
| `allowed_value` | 2 | 2 | incident/planning pool |
| `contains_text` | 0 | 2 | contradiction response lacked `insufficient evidence` |
| `not_contains_text` | 4 | 0 | forbidden delete strings were absent |
| `regex_match` | 0 | 2 | contradiction response failed `^\{.*\}$` |
| `maximum_response_length` | 2 | 0 | summary stayed within 600 characters |
| **Total** | **24** | **26** | **0.48 pass rate** |

The most consequential result is not merely malformed JSON: all eight exact-value checks failed.
Thus even the three valid-JSON responses did not rescue any task, and the evidence includes a
semantic correctness problem as well as a serialization problem.

## 5. Response-shape classifications

For each 10-attempt execution, the retained aggregate evidence supports:

| Classification | Count | Confidence |
|---|---:|---|
| `request_failed` | 0 | exact |
| `timeout` | 0 | exact |
| `valid_json` | 3 | exact |
| JSON parse failure | 7 | exact |
| fully correct answer | 0 | exact |

The seven parse failures can be safely grouped as `malformed_json`. Without response bodies,
they cannot be subdivided among `markdown_wrapped_json`, `plain_text`, prose plus JSON, or
syntactically malformed JSON. Similarly, valid JSON cannot be classified per task as
`missing_required_field`, `wrong_allowed_value`, or wrong schema beyond the validator totals
above. No evidence supports claiming Markdown fences or prose specifically.

## 6. Request, generation, and chat-template configuration

### HTTP request path

`LlamaServerClient.chat_completion_detailed` in
`src/aarchtune/runtime/client.py:168` constructs:

- `messages` as the workload's `{role, content}` chat-message array;
- `temperature`, `max_tokens`, and `seed` from each task;
- `stream: false`;
- `POST /v1/chat/completions`; and
- extraction from `choices[0].message.content`.

This is consistent with the documented OpenAI-compatible llama-server endpoint and schema. The
pinned server source registers `/v1/chat/completions`, requires `messages` to be an array, parses
the OpenAI-compatible messages, and applies its selected chat template. Ten successful requests,
nonzero prompt and completion token metrics, and zero HTTP/invalid-envelope failures confirm that
the server accepted this schema; they do not prove that every possible formatting detail was
perfect. The repository test
`test_generation_parameters_and_non_streaming_are_preserved` verifies the exact payload.

### Determinism

Every workload task specifies `temperature: 0` and `seed: 42`; maximum tokens vary from 100 to
160. The client forwards all three fields without transformation and disables streaming.
Evaluation uses two repetitions, a 60-second request timeout, and one warm-up request. No
evidence shows those settings being dropped. Temperature zero and the fixed seed were therefore
applied as intended, subject to llama.cpp's normal platform-level determinism limits.

### Structured output and stopping

Neither `src/aarchtune/runtime/client.py:169-175` nor
`src/aarchtune/runtime/command.py:81-115` configures:

- stop sequences;
- `response_format`;
- request-level `json_schema` or grammar;
- server-level `--json-schema`, `--grammar`, or grammar file;
- `--chat-template`, `--chat-template-file`, `--chat-template-kwargs`, or Jinja switches.

The JSON Schema validators in `workloads/smoke-test.jsonl` are post-generation checks; they are
not copied into the request. Each system message asks for JSON in prose. Therefore the workload
expects structured JSON but deliberately leaves production of that JSON unconstrained.

### Native chat template

The exact pinned GGUF metadata contains `tokenizer.chat_template`,
`add_generation_prompt`, and the Qwen/ChatML assistant generation marker. The exact pinned
llama.cpp source initializes the server template from `llama_model_chat_template(model, null)`
when no override is supplied and then applies that template to the request's messages. A ChatML
fallback exists only if model metadata is empty.

Together with the absence of an override and successful chat requests, this strongly supports
that the model's embedded native template was detected and applied. There is no evidence of raw
prompt concatenation, use of the legacy `/completion` endpoint, or role-message loss.

## 7. Cause assessment

### Evidence against a formatting/configuration defect

- Correct chat endpoint, request body, roles, and response extraction are visible in source and
  covered by a payload-preservation test.
- All 10 initial-baseline requests succeeded, with no HTTP failures, invalid server envelopes, or
  timeouts.
- The exact model embeds a template and the exact server pin automatically selects and applies it.
- The quality totals remained identical through baseline-start, all four evaluated profiles, and
  baseline-end. Performance-only settings did not perturb quality.

No concrete request-formatting defect is proven, so there is no source correction to propose.

### Evidence for model-capability limitation

- Seven of ten responses were not parseable as full-response JSON despite explicit system
  instructions.
- Every one of eight exact semantic checks failed.
- All five tasks failed both repetitions.
- The model is a small 1.5B-parameter instruct model quantized to Q4_K_M.
- No constrained decoder compensated for weak structured-output adherence.
- Changing parallelism, mmap, or prompt caching produced no measurable semantic-quality change.

The best-supported cause is model capacity/instruction-following weakness, amplified by
prompt-only JSON production. The absence of structured-output constraints is not hidden: it is
part of what this workload measured. Adding a grammar would be a different experiment and could
mask whether a stronger model can satisfy the existing workload contract, so it is not the
smallest diagnostic next step.

## 8. Completed follow-up and experimental freeze

The recommended model-capability prechecks were subsequently completed with the
same workload, quality policy, llama.cpp pin, generation settings, and baseline
shape:

- Qwen2.5-7B Q3_K_M, prompt-only;
- Qwen2.5-14B Q3_K_M, prompt-only; and
- Qwen2.5-7B Q3_K_M, JSON-object.

All three produced identical aggregate results and remained below the unchanged
quality thresholds. Two final Mistral Nemo 12B Q4_K_M attempts lost the hosted
runner before baseline inference, leaving quality evidence incomplete.

Model-family experimentation is now frozen. See
`docs/FINAL_VALIDATION_REPORT.md` for the final evidence table and limitations.

## 9. Policy integrity

The run used the unchanged default absolute minimums:

- task success `0.95`;
- JSON validity `0.98`;
- validator pass rate `0.97`;
- request success `0.98`; and
- timeout rate at most `0.02`.

The optimizer correctly rejected every evaluated profile and retained no selected candidate.
This diagnosis does not weaken those thresholds, alter validators, change search-space values,
change workflow benchmark settings, change model selection in the repository, or edit Devpost
results.
