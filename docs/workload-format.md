# Workload format

AArchTune workloads are trusted project inputs stored as UTF-8 JSON Lines. Every
non-blank line is exactly one task object. Task order is preserved. Comments are not
supported: a line beginning with `#` is rejected rather than silently changing the
bytes covered by the workload hash.

Workloads are declarative data. AArchTune never imports callbacks, evaluates code,
executes response content, or repairs malformed output.

## Task structure

```json
{
  "id": "incident-001",
  "category": "incident_classification",
  "description": "Classify continuous memory growth.",
  "messages": [
    {"role": "system", "content": "Return only valid JSON."},
    {"role": "user", "content": "A worker's RSS grows continuously..."}
  ],
  "generation": {
    "temperature": 0,
    "max_tokens": 200,
    "seed": 42
  },
  "validators": [
    {"type": "valid_json"},
    {"type": "exact_value", "path": "$.category", "expected": "memory_leak"}
  ]
}
```

All fields shown above are required. Unknown fields are rejected. IDs must be non-blank
and unique within the file. Categories and descriptions must be non-blank. Messages
support the explicit roles `system`, `user`, and `assistant`; message lists and content
must not be empty.

Generation settings are workload inputs and must remain identical when comparing
runtime configurations. Temperature is between 0 and 2, `max_tokens` is between 1 and
32,768, and `seed` is an integer or `null`. A workload is reported as deterministic
when every task uses temperature 0 and a non-null seed.

## Validators

Validators run in declared order. Every validator produces its own result, even when an
earlier validator fails. JSON-dependent validators return dependency failures when the
entire response is not JSON.

### `valid_json`

Passes only when Python's JSON parser consumes the entire response. Markdown fences,
leading explanations, and trailing prose fail. JSON is not extracted or repaired.

```json
{"type": "valid_json"}
```

### `json_schema`

Validates against JSON Schema Draft 2020-12 using `jsonschema`. The schema itself is
checked while loading the workload.

```json
{"type": "json_schema", "schema": {"type": "object", "required": ["status"]}}
```

### `required_fields`

Requires every restricted JSON path to exist. A present JSON `null` counts as present.

```json
{"type": "required_fields", "paths": ["$.root_cause", "$.evidence"]}
```

### `exact_value`

Requires normal, type-preserving JSON equality at a path. Strings, numbers, and
booleans are not coerced.

```json
{"type": "exact_value", "path": "$.category", "expected": "memory_leak"}
```

### `allowed_value`

Requires the observed value to exactly equal one member of a non-empty allowed list.

```json
{"type": "allowed_value", "path": "$.action", "allowed": ["retry", "operator_review"]}
```

### `contains_text` and `not_contains_text`

Search the raw response. Matching is case-sensitive by default; set `case_sensitive` to
`false` for Unicode case-folded matching.

```json
{"type": "contains_text", "text": "insufficient evidence", "case_sensitive": false}
{"type": "not_contains_text", "text": "delete", "case_sensitive": false}
```

### `regex_match`

Searches the raw response with a pattern compiled during workload loading. Supported
flags are `IGNORECASE`, `MULTILINE`, `DOTALL`, and `ASCII`. Patterns are limited to
1,024 characters. This bounding does not prevent every pathological regular expression;
workload files are expected to be reviewed, trusted project inputs. Replacement code is
never accepted or evaluated.

```json
{"type": "regex_match", "pattern": "^(supported|unsupported|uncertain)$", "flags": ["IGNORECASE"]}
```

### `maximum_response_length`

Passes when `len(response_text)` is at most `max_characters`. Python string length counts
Unicode code points, not UTF-8 bytes or display cells.

```json
{"type": "maximum_response_length", "max_characters": 2000}
```

### `request_succeeded`

Uses request metadata instead of response text. The request must have
`request_succeeded: true`; a timeout fails unless `allow_timeout` is explicitly true.

```json
{"type": "request_succeeded", "allow_timeout": false}
```

## Restricted JSON paths

Supported syntax is intentionally smaller than JSONPath:

```text
$
$.field
$.nested.field
$[0]
$.items[0]
$.items[0].name
```

Paths resolve exact object keys and non-negative array indexes. Missing paths are
distinct from present `null` values. Quoted keys, negative indexes, wildcards, recursive
descent, filters, expressions, scripts, and function calls are rejected. No `eval` or
equivalent mechanism is used. Dot notation therefore cannot address keys containing a
literal dot.

## Safety limits

| Input | Limit |
| --- | ---: |
| Workload or fixture file | 5 MiB |
| Tasks or fixture records | 1,000 |
| JSONL line | 256 KiB |
| Messages per task | 32 |
| Message content | 64 KiB characters |
| Validators per task | 32 |
| Regex pattern | 1,024 characters |
| Evaluated response | 1 MiB characters |

File size is checked before the full read where practical, then checked again after the
read. SHA-256 is calculated over the exact source bytes, including blank lines and final
newlines. The fixture line limit is intentionally stricter than the runtime response cap;
future runtime responses can use the full response limit without being encoded in JSONL.

## Response fixture format

Fixtures are synthetic test data, not benchmark results or claims about a model:

```json
{
  "task_id": "incident-001",
  "text": "{\"status\":\"ok\"}",
  "request_succeeded": true,
  "timed_out": false,
  "status_code": 200,
  "error": null
}
```

Each non-blank JSONL line is one response. Response task IDs must be unique and must
match the workload. Missing records remain explicitly unevaluated; they are not converted
into evaluated task failures.

## CLI

```bash
aarchtune workload validate workloads/smoke-test.jsonl
aarchtune workload validate workloads/smoke-test.jsonl --json
aarchtune workload validate workloads/smoke-test.jsonl --output summary.json

aarchtune workload evaluate workloads/smoke-test.jsonl \
  --responses tests/fixtures/responses/passing.jsonl
```

Both commands support JSON output. Evaluation JSON contains task-level validator
results, full-precision rates, per-category statistics, and per-validator-type statistics.

Exit codes:

| Code | Meaning |
| ---: | --- |
| 0 | Input is valid and every workload task passed evaluation |
| 1 | Workload, fixture, or output file is invalid |
| 2 | Evaluation completed, but a task failed or a response was missing |

## Known limitations

Quality checks measure only the declarative validators chosen by the workload author.
They do not prove general correctness or safety. Regex evaluation does not provide a
hard execution-time sandbox. The v1 path syntax cannot address keys containing dots.
Fixture evaluation does not run a model and must not be presented as real inference
quality evidence.

