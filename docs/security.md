# Security and privacy

AArchTune never uses shell command strings for runtime execution. It validates paths and environment names, binds generated server commands to `127.0.0.1`, inspects flags before use, owns and cleans process groups, caps subprocess time/output and response sizes, and never kills by process name.

Workloads use declarative validators only. Model output is data and is never executed. No telemetry, uploads, cloud resources, root commands, package installation, or model downloads occur automatically.

Generated deployment scripts use Bash arrays, quoted paths, hash checks, signal forwarding, and no `eval`. Raw evaluation responses can be sensitive; sanitize upstream evidence before publication. The final bundle does not copy them.
