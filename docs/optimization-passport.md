# Optimization Passport

`optimization-passport.json` is the compact audit record for a selection or diagnostic outcome. It records project version, hardware, Arm status, runtime and benchmark hashes, KleidiAI evidence, model/workload fingerprints, stage hashes, policies, summaries, drift, selection rationale, fastest rejection, unavailable metrics, limitations, and reproduction steps.

`passport_content_hash` is SHA-256 of canonical JSON with that field omitted. `aarchtune passport verify FILE` recomputes it and validates referenced stage hashes plus required hardware-specific and synthetic disclosures. The Passport excludes raw responses, environment dumps, and secrets.
