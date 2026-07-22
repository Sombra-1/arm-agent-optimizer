# Runtime safety

AArchTune owns one local `llama-server` child at a time. It builds an argument list from
strict typed settings and option tokens observed in that exact binary's `--help` output.
Version strings are provenance only and never imply flag support.

## Capability inspection

Both `--version` and `--help` are invoked as argument lists with a bounded timeout,
captured stdout/stderr, and no shell. Results are cached by resolved path, byte size, and
nanosecond modification time. Diagnostic output is retained internally and emitted only
when requested. Complete long-option tokens are parsed; substrings do not count.

## Binding and port selection

The default bind address is `127.0.0.1`. Non-loopback addresses require explicit
`allow_public_bind=true`. An explicit port is checked before startup without inspecting,
signaling, or killing its owner.

For automatic loopback selection, AArchTune asks the kernel for an ephemeral port and
then closes the temporary socket before starting `llama-server`. No portable API can
reserve that port across `exec`, so a small race remains. A bind failure during that race
is reported as a port-in-use error; AArchTune never terminates the competing process.

## Readiness and ownership

Startup succeeds only after one configured HTTP endpoint responds successfully. Logs may
support diagnostics but never replace the network check. Polling uses short request
timeouts and stops immediately if the child exits.

The child starts in a new session. Shutdown sends SIGTERM only to that owned process
group, waits for the configured grace period, and sends SIGKILL only to the same group if
needed. `stop()` is idempotent, and context-manager exit performs cleanup after normal
operation or exceptions.

Stdout and stderr are drained continuously. Only a bounded recent tail is retained, with
an explicit truncation marker. Common inline credentials and secret-named environment
values are redacted from persisted diagnostics. Redaction is defensive, not a substitute
for keeping secrets out of command output.

## HTTP behavior

The HTTPX client disables environment proxy use, performs only non-streaming local
requests, caps response sizes, and distinguishes connection failures, timeouts, non-2xx
responses, server errors, invalid JSON, missing completion content, and oversized
responses. Returned completion text is never executed or interpreted by the runtime
layer; declarative workload validators handle correctness.

