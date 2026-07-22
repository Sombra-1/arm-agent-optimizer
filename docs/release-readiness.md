# Release readiness checklist

- [ ] Full pytest suite passes with reported coverage.
- [ ] Ruff check and format check pass.
- [ ] Strict MyPy passes.
- [ ] MIT `LICENSE` exists and README links it.
- [ ] Secret scan reviewed; no credentials or personal data.
- [ ] No GGUF model weights except the explicit tiny fake text fixture.
- [ ] Synthetic evidence is visibly labelled in CLI, report, Passport, and README.
- [ ] Real Arm evidence follows the runbook and replaces no placeholder without validation.
- [ ] Documentation links and CLI examples reviewed.
- [ ] Public repository metadata and contribution/security files reviewed.
- [ ] Devpost fields completed without fabricated results.
- [ ] Optional video uses real or clearly labelled synthetic evidence.
- [ ] `optimize validate`, `finalize validate`, and `passport verify` pass.
- [ ] Cleanup audit finds no fake or real owned processes.
- [ ] `scripts/validate-release.sh` passes.
