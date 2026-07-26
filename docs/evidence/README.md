# Preserved native Arm64 evidence

This directory permanently preserves the reviewed, sanitized evidence from the
completed native Arm64 optimization smoke. The ZIP is an immutable copy of the
original GitHub Actions artifact download. It is retained here because Actions
artifact retention was temporary.

## Original artifact

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
