# Three-to-five-minute demo script

## Recording principle

Use the existing completed evidence. Do not download a multi-gigabyte model,
start a native workflow, or wait for GitHub Actions during the recording.

Keep native and synthetic evidence visibly labelled:

- **Native evidence:** completed GitHub Actions runs and the final validation
  report.
- **Synthetic evidence:** product-behavior screenshots/fixtures only; never Arm
  or model-performance evidence.

## Before recording

Prepare these tabs:

1. Repository `README.md`.
2. `docs/FINAL_VALIDATION_REPORT.md`.
3. Public native run
   `https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30119492016`.
4. `docs/native-arm64-quality-diagnosis.md`.
5. `docs/screenshots/05-fastest-candidate-rejected.png`.
6. `docs/evidence/README.md` and the permanently preserved native archive.

Prepare a clean terminal at the repository root with the virtual environment
installed. Clear scrollback containing tokens, private paths, or unrelated
commands.

## 0:00–0:30 — Opening

Say:

> Most inference optimizers ask only: “Which configuration is fastest?”
>
> AArchTune asks a harder question: “Which configuration is faster, correct,
> reproducible, and safe to deploy?”

Show the first screen of `README.md` and point to:

- native Arm64;
- GGUF/llama.cpp;
- workload quality gates;
- reproducible evidence; and
- safe candidate selection.

## 0:30–1:05 — Repository and product validation

Run:

```bash
git status --short
git log -1 --oneline
source .venv/bin/activate
aarchtune --version
```

Explain that normal CI validates Python 3.11 and 3.12 and the release suite has
514 tests with 90% aggregate coverage.

Do not run the full suite live unless recording time permits. Show the existing
green CI screenshot or public CI run instead.

## 1:05–1:35 — Hardware and runtime detection

Run:

```bash
aarchtune doctor --json
```

Say explicitly whether this terminal is Arm64 or a development machine. Do not
present local x86 output as native evidence.

Then show the native environment row in `docs/FINAL_VALIDATION_REPORT.md`:

- native AArch64;
- four Arm Neoverse-N2 cores;
- CPU inference;
- pinned llama.cpp;
- `n_gpu_layers=0`; and
- mmap enabled.

Use the exact wording:

> KleidiAI was compiled into the pinned llama.cpp build. During the
> Qwen2.5-1.5B Q4_K_M native smoke, llama.cpp reported that q4_K tensors had no
> KleidiAI kernel and were not accelerated; the available kernels were reported
> for Q4_0 and Q8_0. No end-to-end KleidiAI runtime speedup is claimed for this
> tested model.

## 1:35–2:05 — Workload and quality policy

Run:

```bash
aarchtune workload validate workloads/smoke-test.jsonl --json
python -c "from pathlib import Path; from aarchtune.evaluation.quality_policy import load_quality_policy; print(load_quality_policy(Path('configs/default-quality-policy.yaml')).sha256)"
```

Show the five workload tasks and explain that validators test transport,
serialization, schema, allowed actions, exact values, forbidden text, regexes,
and response length.

Show `configs/default-quality-policy.yaml` and point out the absolute floors:

- request success 0.98;
- task success 0.95;
- JSON validity 0.98; and
- validator pass rate 0.97.

## 2:05–2:50 — Native optimization result

Open the native evidence table and run page `30119492016`.

Say:

> This is a real native Arm64 optimization workflow. It completed all 132
> configured candidate executions, produced 11 distinct low-level signatures,
> and advanced four profiles to real-workload evaluation.

Then show:

```text
Optimization result: no_eligible_candidate
Native optimize exit: 4
```

Explain:

> Every evaluated candidate failed the unchanged quality policy, so AArchTune
> correctly made no performance recommendation. This is the safety result:
> faster measurements cannot bypass correctness.

Do not describe the run as a successful performance optimization.

## 2:50–3:25 — Quality rejection and evidence structure

Show the clearly labelled synthetic fastest-rejected screenshot only to explain
the interface. Keep the `SYNTHETIC TEST EVIDENCE` label visible and say:

> This screenshot demonstrates report behavior, not Arm performance.

Then return to native evidence and show the documented sanitized artifact
inventory:

- stage manifests and hashes;
- quality decisions;
- drift evidence;
- selection outcome;
- `optimization-passport.json`;
- `report-data.json`;
- cleanup proof; and
- privacy-scan result.

Verify and list the permanent repository archive without extracting it in
place:

```bash
cd docs/evidence
sha256sum -c aarchtune-real-arm64-smoke-30119492016-1.sha256
unzip -l aarchtune-real-arm64-smoke-30119492016-1.zip | head -40
```

To show the report or validate the generated bundle, extract the verified ZIP
into a disposable directory outside the repository. Do not extract into
`docs/evidence/`, modify the generated files, or recommit them:

```bash
evidence_demo_dir=$(mktemp -d)
unzip -q aarchtune-real-arm64-smoke-30119492016-1.zip \
  -d "$evidence_demo_dir"
aarchtune optimize validate "$evidence_demo_dir/optimization" --json
aarchtune finalize validate "$evidence_demo_dir/optimization/final" --json
aarchtune passport verify \
  "$evidence_demo_dir/optimization/final/optimization-passport.json" --json
```

Do not substitute a synthetic directory while calling it native.

## 3:25–4:05 — Model quality evidence

Show the Qwen table:

- 7B, 14B, and JSON-object 7B produced identical aggregate results;
- all completed requests;
- none passed the quality policy; and
- full optimization was correctly blocked.

Then show the Mistral boundary:

```text
quality_evidence_incomplete
measured attempts: 0/10
quality metrics: unavailable
OOM: unproven
```

Say:

> The hosted runner was lost before inference. We do not call that a Mistral
> quality failure, an Arm incompatibility, or an OOM.

## 4:05–4:30 — Closing

Show the Mermaid architecture diagram and Passport endpoint.

Close with:

> AArchTune does not promise an invented speedup. It provides a safer
> optimization decision: measure on native Arm64, validate the real workload,
> preserve provenance, and refuse to deploy a faster configuration when
> correctness does not survive.

End on the repository URL and MIT license.

## Short version

If the recording must stay near three minutes:

1. Compress repository/CI status to 15 seconds.
2. Show workload and policy together.
3. Keep the native 132-execution result and `no_eligible_candidate`
   explanation intact.
4. Use one quality table rather than opening multiple run pages.
5. Keep the Mistral limitation and final safety message.
