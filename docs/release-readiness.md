# Release review status

This document tracks repository-level release review. Manual submission actions
live in [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md).

## Completed validation

- [x] Full pytest suite passes with 90% aggregate coverage.
- [x] Ruff check and format check pass.
- [x] Strict MyPy passes.
- [x] Python 3.11 and 3.12 passed on the evidence-preservation commit; the
      [live main-branch CI workflow](https://github.com/Sombra-1/arm-agent-optimizer/actions/workflows/ci.yml)
      remains the current status reference.
- [x] MIT `LICENSE` exists and README links it.
- [x] No GGUF model weights are committed.
- [x] Synthetic evidence is visibly labelled.
- [x] Native and synthetic evidence are explicitly separated.
- [x] Native run IDs and limitations are documented.
- [x] The original sanitized native archive is permanently preserved with its
      SHA-256 and verification instructions.
- [x] Devpost text contains no real-result placeholders.
- [x] No unsupported speedup claim is published.
- [x] `scripts/validate-release.sh` passes.

## Publication review

- [x] Render the README Mermaid diagram on GitHub.
- [x] Test installation commands in a fresh environment.
- [x] Record and review the final video.
- [x] Refresh the three repository-facing screenshots.
- [x] Verify repository visibility and all public links.
- [x] Publish and review the final Devpost entry.
- [x] Verify the final submission URL and published state.

No tag move or GitHub release was required for this submission.
