# Architecture

AArchTune is a local staged evidence pipeline. `doctor` records the machine; `baseline` uses one fixed `llama-server`; `plan` creates a deterministic bounded candidate set; `screen` deduplicates low-level signatures and invokes `llama-bench`; `evaluate` runs advanced profiles through isolated servers and the workload validator; `finalize` creates the Passport, Pareto frontier, report, and conditional deployment files.

The `optimize` orchestrator calls these native stage APIs and validates their native artifacts between transitions. It does not duplicate benchmark or quality logic. Stage directories remain separate, hashes bind downstream evidence to upstream inputs, and resume trusts completed stages only after validation.

Raw responses stay in evaluation candidate directories. The final bundle references stage manifests and hashes instead of copying logs or response bodies.

Related: [runtime safety](runtime-safety.md), [search planning](search-planning.md), [screening](screening-methodology.md), [evaluation](evaluation-methodology.md), and [Passport](optimization-passport.md).
