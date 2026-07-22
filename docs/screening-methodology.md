# Low-level candidate screening

`aarchtune screen` uses a local `llama-bench` executable as a bounded technical
filter between search planning and expensive real-workload evaluation. Screening
does not run candidate `llama-server` processes and does not evaluate agent output,
schemas, tool calls, HTTP latency, prompt caching, parallel request scheduling, or
production stability.

## Capability inspection

The executable is resolved from `--bench-binary`, `AARCHTUNE_LLAMA_BENCH`, `PATH`,
an unambiguous sibling of the planned server, or an unambiguous known local build
path. AArchTune verifies that it is executable, hashes it, and runs bounded
`--help` and `--version` argument-list probes.

Complete option tokens from help output determine the actual mapping for model,
threads, batch threads, batch and micro-batch sizes, prompt/generation tokens,
repetitions, output format, mmap, and NUMA. Version text never implies a feature.
Inspection results are cached by absolute path, size, and nanosecond modification
time.

AArchTune requires JSONL, JSON, or CSV output, in that preference order. It does
not scrape Markdown or terminal tables.

## Scenarios and bounds

The default scenario set contains:

| Scenario | Prompt tokens | Generation tokens | Meaning |
|---|---:|---:|---|
| `prefill-small` | 128 | 0 | Small prompt-processing probe |
| `prefill-medium` | 512 | 0 | Medium prompt-processing probe |
| `decode` | 0 | 128 | Generation probe |
| `mixed` | 512 | 128 | Combined low-level probe |

Unsupported scenarios are omitted explicitly. These token counts are synthetic
low-level probes and are not estimates of the real workload distribution.

Defaults cap screening at 24 signatures, four scenarios, three repetitions, and
288 invocations. Execution is sequential with per-invocation and whole-run
timeouts.

## Benchmark signatures

Screenable fields are threads, batch threads, batch size, micro-batch size, mmap,
and NUMA when the inspected binary exposes a mapping. Server binary, context,
parallel slots, prompt cache, and CPU-affinity policy remain recorded as
unscreenable.

Candidates with the same effective screenable settings share one canonical
signature and execute only once per scenario/repetition. Original candidate IDs,
hashes, and every unscreenable difference remain in `signature-membership.jsonl`.

## Processes and evidence

Every invocation uses an argument list with `shell=False` and a new owned process
session. Machine-readable stdout streams directly to a raw artifact. Diagnostic
stderr retains a bounded, redacted tail. A process sampler follows only the owned
PID and descendants. Timeout and interruption first request graceful termination,
then force-kill only the owned process group if necessary. The sampler and drain
thread are joined on every path.

Parsers reject malformed JSONL, JSON, and CSV. Known numeric fields reject
booleans, negative values, NaN, infinity, and arbitrary strings. CSV conversion
is limited to recognized numeric columns with strict numeric syntax. Unknown raw
fields remain in the untouched stdout artifact. Normalized values include source
paths and explicit unavailable reasons.

Prompt-only, decode-only, and combined throughput remain separate. Combined
results are never relabelled as prefill or decode throughput.

## Stability and advancement

Repetitions use deterministic statistics and nearest-rank P95. Coefficient of
variation uses sample standard deviation divided by the mean:

- `<= 0.03`: stable
- `<= 0.10`: variable
- `> 0.10`: highly variable
- fewer than two measurements: insufficient data

Goal-specific screening scores are low-level heuristics only. Components are
min-max normalized among eligible signatures and weights renormalize when a
component is unavailable:

- Latency: 55% decode, 30% combined, 15% stability.
- Throughput: 30% prefill, 35% decode, 25% combined, 10% stability.
- Memory: 60% inverse sampled peak RSS, 15% decode, 15% prefill, 10% stability.
- Balanced: 35% prefill, 35% decode, 15% inverse sampled peak RSS, 15% stability.

The baseline is retained when eligible. Selection then deliberately preserves a
prompt-cache and parallel-slot alternative where available before filling the
remaining bounded set using score and configuration diversity. Those server-only
dimensions remain unmeasured until later real-workload evaluation.

## Synthetic fixture

`tests/fixtures/bin/fake-llama-bench` uses a deterministic formula based on input
threads, batch size, and scenario type. It exists only to test orchestration,
parsers, timeouts, instability, memory sampling, and cleanup. Its values do not
model any real CPU, model, or llama.cpp build and are always labelled:

```text
Synthetic low-level measurements — not Arm performance evidence
```

## Security and limitations

- Screening launches local `llama-bench` processes only.
- No shell command strings, process-name termination, dynamic code, or model
  output execution is used.
- Raw outputs remain local and are never uploaded.
- Sampled memory precision is bounded by the sampling interval.
- Screening advancement is not final optimization ranking.
- Agent correctness and end-to-end server behavior remain completely unevaluated.
