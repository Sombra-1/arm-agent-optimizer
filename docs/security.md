# Security and privacy

AArchTune never uses shell command strings for runtime execution. It validates paths and environment names, binds generated server commands to `127.0.0.1`, inspects flags before use, owns and cleans process groups, caps subprocess time/output and response sizes, and never kills by process name.

Workloads use declarative validators only. Model output is data and is never executed.
External JSON Schema references are rejected, and JSON Schema/regex evaluation runs in
a time-limited isolated process. No telemetry, uploads, cloud resources, root commands,
package installation, or model downloads occur automatically.

Generated deployment scripts use Bash arrays, quoted paths, hash checks, signal
forwarding, and no `eval`. Container bundles keep host publication on loopback while the
server listens on the container interface. Raw evaluation responses can be sensitive;
sanitize upstream evidence before publication. The final bundle does not copy them.
