# Explain every screening decision

The 0.2.1 development branch adds a response-free diagnosis of the existing
screening artifacts. It does not change planning, screening eligibility, ranking,
quality thresholds, or drift policy. Its results describe recorded evidence;
use `screen validate` to check artifact integrity.

## Read a run

Screening automatically writes `screening-diagnostics.json` and a self-contained
`screening-report.html`, including for partial, failed, and interrupted runs once
the screening directory has been initialized. To inspect an existing run without
loading a model, discovering a runtime, or executing a benchmark:

```bash
aarchtune screen diagnose results/optimization/screening \
  --output-dir results/screening-diagnosis

aarchtune screen diagnose results/optimization/screening --json
```

Exit 0 means the diagnostic metadata was available, even when screening itself
failed. Exit 3 means some diagnostic evidence is unavailable or inconsistent;
the partial diagnosis is still returned. Exit 1 means the requested report
could not be written. The command preserves the source artifacts.

## What the report explains

- **Configurations:** Every profile in the available search plan, its settings,
  low-level signature, shared measurements, and fields that require server-level
  workload evaluation. Planning exclusions appear separately with their recorded
  reason codes and proposed settings.
- **Decisions:** Each profile's recorded advancement decision and reason, signature
  eligibility, scenario results, repetition counts, stability classification, and
  coefficient of variation. A missing decision is labeled `not_recorded`.
- **Failures:** Every planned benchmark invocation, signature/scenario/repetition,
  execution or measurement status, exit code, failure codes, and recorded
  measurement-provenance errors. Budget-skipped invocations are distinguished from
  attempts that failed to start a process.
- **Coverage:** Planned profiles, deduplicated signatures, planned/attempted/usable
  invocations, usable invocation fraction, and advanced profiles. Final optimization
  reports also show completed workload evaluations and their fraction of the plan.
- **Next steps:** Suggestions tied to recorded failure and exclusion codes: inspect
  local stderr or flag mappings, check scenario rows and provenance, reduce competing
  load, revise resource/search budgets, or consider advancing more profiles.

The standalone screening snapshot cannot know whether later workload evaluation
completed. Its workload counts remain unavailable. Final reports obtain those
counts from the validated evaluation context, matching profile IDs and hashes.
Advancement alone never establishes a completed workload evaluation or model quality.

Missing, malformed, or unavailable evidence produces explicit errors and null
counts where the required source is unavailable. It is not converted into zero
attempts, zero failures, or a successful empty search. Source SHA-256 values identify
the metadata read by the diagnosis. Validation errors do not quote input values.

## Published native evidence

The native product harness now calls the existing path-redacting collector for
successful runs as well as failures. Future artifacts include the per-profile
decisions, per-signature results, invocation/measurement failures, inventory, JSON
diagnosis, and HTML screening report. Final report data contains the full-workload
coverage snapshot. Public reports are rendered after path redaction.

Raw benchmark stdout/stderr, process samples, server logs, generated model responses,
and model weights are not read by the diagnosis or added to the public projection.
The existing public-evidence scan still gates upload. Original bundles and Passports
are validated before public projection; the diagnostic report does not replace them.

The preserved 0.2 artifacts remain immutable. Their missing screening reasons cannot
be reconstructed from aggregate counts. This change improves future evidence; it
does not retroactively establish those reasons, broader native search coverage, or
a performance improvement. The implementation is tested with synthetic fixtures;
a fresh native run has not yet validated the expanded artifact export.
