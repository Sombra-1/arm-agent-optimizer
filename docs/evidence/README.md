# Preserved native Arm64 evidence

This directory permanently preserves the reviewed, sanitized evidence from the
native Arm64 experiments. Each ZIP is an immutable copy of its original GitHub
Actions artifact download, retained because Actions artifact retention is temporary.

## 0.2 product validation

The [0.2 results record](../native-0.2-results.md) links every retained attempt,
including quality rejections, incomplete pipeline evidence, and the verified
full-pipeline `baseline_retained` result. The corresponding `aarchtune-0.2-*.zip`
files each have a matching `.sha256` file. Public projections exclude generated
responses; original bundles and Passports were verified before projection.
The projections are not canonical bundles and cannot replace their validators.

For example, verify the full-pipeline artifact from the repository root:

```bash
cd docs/evidence
sha256sum -c aarchtune-0.2-optimization-43622255530-1.sha256
unzip -t aarchtune-0.2-optimization-43622255530-1.zip
```

## Original 0.1 artifact

| Field | Value |
| --- | --- |
| Source workflow run | [`30119492016`](https://github.com/Sombra-1/arm-agent-optimizer/actions/runs/30119492016) |
| Artifact ID | `8607905782` |
| Original artifact name | `aarchtune-real-arm64-smoke-30119492016-1` |
| Original run commit | `8376178cc37d00d64fccc2d0276161e0c8f7fd23` |
| Original creation time | `2026-07-24T20:00:38Z` |
| Original GitHub expiration time | `2026-08-07T20:00:36Z` |
| Archive size | 151,907 bytes |
| Original ZIP SHA-256 | `2cc03947eb624ee03ea5268c5a9fb2003eecd57ffd0ed50d7807ffaca4f326ee` |
| Privacy scan | `forbidden_content_matches=0` |
| Cleanup proof | `cleanup_complete=true` |
| Evidence mode | `synthetic=false` |
| Outcome | `no_eligible_candidate` |
| Native optimize exit | `4` |

The archive contains sanitized native evidence, not model weights or raw model
responses. Preserving it does not create a new benchmark claim or turn one
shared-runner smoke into repeatability proof.

## Verify before extraction

From the repository root:

```bash
cd docs/evidence
sha256sum -c aarchtune-real-arm64-smoke-30119492016-1.sha256
unzip -t aarchtune-real-arm64-smoke-30119492016-1.zip
```

Do not trust or extract the ZIP before its SHA-256 and archive integrity pass.
Extract it only into a disposable directory outside the repository. Do not
recommit extracted reports, Passports, manifests, or other generated files.

The original temporary Actions artifact may expire after the date above. These
repository files are the permanent references:

- [`aarchtune-real-arm64-smoke-30119492016-1.zip`](aarchtune-real-arm64-smoke-30119492016-1.zip)
- [`aarchtune-real-arm64-smoke-30119492016-1.sha256`](aarchtune-real-arm64-smoke-30119492016-1.sha256)
