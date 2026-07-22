# Search planning

`aarchtune plan` creates a deterministic set of candidate `llama-server`
configurations. Planning is deliberately separate from execution: it starts no
server, sends no HTTP request, collects no benchmark metric, and makes no
performance claim.

## Inputs and provenance

A plan can be created from a completed baseline:

```bash
aarchtune plan \
  --baseline results/baseline \
  --goal balanced \
  --output-dir results/search-plan
```

The planner requires the baseline manifest, hardware, runtime inspection,
server command, model, workload, and baseline summary artifacts. The baseline
must be complete and its binary, model, and workload hashes must be available.
Partial and interrupted runs are rejected. Synthetic fixture baselines require
the explicit `--allow-synthetic` development opt-in.

The current machine, binary, model, and workload are inspected again. Changes
to architecture, model hash, or workload hash are incompatible. Runtime binary,
version, or supported-flag changes are incompatible unless
`--allow-runtime-change` is supplied; the override is recorded. CPU model,
core-count, feature, NUMA, and memory differences produce compatibility
warnings. An Arm baseline cannot silently become an x86 plan.

Planning can also use explicit local inputs:

```bash
aarchtune plan \
  --binary /opt/llama.cpp/llama-server \
  --model /models/model.gguf \
  --workload workloads/reliability-agent.jsonl \
  --goal latency \
  --output-dir results/search-plan
```

Explicit planning has no measured baseline RSS anchor. Its `baseline` profile
represents the runtime defaults plus the planner's typed invariants, not a
measured result.

## Search-space schema

Search spaces are strict YAML with schema version `1.0`. Unknown keys,
duplicate values, non-positive values, invalid limits, and spaces with no valid
`ubatch_size <= batch_size` relationship are rejected. The source file's exact
SHA-256 is stored in the plan. The shipped spaces are:

- `configs/default-search-space.yaml`: up to 24 diverse profiles.
- `configs/conservative-search-space.yaml`: up to 12 narrower profiles.

The configured maximum is capped at 64. `--max-profiles` may lower, but not
raise, that maximum.

## Bounded staged generation

The planner never constructs a full Cartesian product. It adds a small number
of candidates in deterministic stages:

1. Exact baseline or explicit-runtime-default profile.
2. Generation-thread scaling.
3. Batch-processing-thread scaling.
4. Representative batch/micro-batch pairs.
5. Goal-specific parallel-slot alternatives.
6. Prompt-cache and mmap experiments where representable.
7. Explicit NUMA or context experiments when enabled and applicable.
8. One memory-conscious experiment.
9. Stable deduplication, capability filtering, memory guardrails, and diversity
   selection.

Physical cores are the primary thread basis. Logical cores are the fallback.
Configured fractions are rounded deterministically, never produce zero, and
are capped at logical cores. Batch pairs select configured micro-batches near
the representative relationships `batch`, `batch / 2`, and `batch / 4` without
testing every pair.

Latency plans emphasize one slot and moderate/high thread counts. Throughput
plans include small concurrent values where the core count permits. Memory
plans emphasize one slot and small batches. Balanced plans retain candidates
from all three concerns. These choices determine experimental coverage only;
they are not predictions or rankings.

When more compatible profiles exist than the limit, a deterministic greedy
selector keeps the baseline and maximizes uncovered stage, thread, batch,
micro-batch, parallelism, cache, and mmap values. Stable candidate IDs and full
profile hashes are calculated from canonical JSON, independent of dictionary
insertion order.

## Capability and resource guardrails

Every non-default typed field is mapped to a flag proven by the exact binary's
help output. Unsupported settings are preserved as exclusions with the field,
value, required flag, and reason; they are never silently removed.

Prompt caching and mmap are described as experiments, not benefits. NUMA
alternatives require multiple detected NUMA nodes, an explicit search-space
opt-in, and a supported flag. CPU affinity remains `none` because v1 has no
safe runtime mapping; AArchTune does not invoke `taskset`.

Memory classification is conservative. A measured baseline peak RSS is the
preferred anchor. Available and total memory, model file size, parallel slots,
and context changes contribute to `safe`, `warning`, `high_risk`, or `unknown`
classification. AArchTune rejects only clearly dangerous headroom. It does not
publish an approximate memory formula as an exact prediction.

## Artifacts and validation

The output contains:

```text
search-plan.json
search-plan-summary.json
baseline-reference.json
hardware-fingerprint.json
runtime-fingerprint.json
search-space.json
candidates.jsonl
excluded-possibilities.jsonl
warnings.json
profiles/*.yaml
```

Important JSON and YAML files are atomically replaced. Existing non-empty
directories require `--overwrite`, and dangerous targets such as `/`, the home
directory, and the repository root are rejected.

Validate an offline plan with:

```bash
aarchtune plan validate results/search-plan
```

Validation checks required artifacts, supported schemas, canonical plan and
profile hashes, unique candidates, JSONL/YAML agreement, baseline provenance,
profile limits, compatibility state, and absence of benchmark-result files.

## Limitations

- Plans are hypotheses to test, not performance conclusions.
- No candidate is executed during planning.
- Tokenized context requirements cannot be derived exactly from JSONL text, so
  baseline context is preserved unless explicit increases are configured.
- Memory risk is a conservative guardrail, not a precise estimator.
- Prompt-prefix similarity is not tokenized in this phase; configured cache
  comparison remains an explicit experiment.
- One runtime binary is planned at a time; alternate backends/builds are not
  invented.
- Synthetic fixtures are development evidence and never Arm performance data.
