# Suggested screenshot captions

### Project overview and Cloud AI track

AArchTune is positioned for the Cloud AI track: it tunes CPU inference for `llama.cpp` workloads on Linux AArch64 while keeping application correctness in the selection loop. This is repository documentation evidence, not a performance result.

### Public GitHub repository

The public AArchTune repository contains the implementation, tests, workloads, documentation, MIT license, and reproducible release history on the `main` branch. This is repository evidence, not a performance result.

### Passing CI

The public CI workflow passes on Python 3.11 and 3.12. This is real software-validation evidence; it does not validate Arm64 performance.

### Synthetic report hero

This synthetic test demonstrates AArchTune's final report and selected-profile summary. It is not real Arm64 performance evidence; the prominent banner identifies the numbers as fixture-generated behavior.

### Fastest candidate rejected

AArchTune does not automatically select the configuration with the highest measured service rate. In this clearly labelled synthetic test, the fastest profile was rejected after task success, JSON validity, and validator pass rate regressed. A slower quality-passing profile was selected instead. This is not real Arm64 performance evidence.

### Candidate funnel and performance evidence

This synthetic test shows how AArchTune narrows planned profiles into low-level signatures, advanced candidates, real-workload evaluations, and quality-passing profiles, then compares the evaluated candidates. It is not real Arm64 performance evidence.
