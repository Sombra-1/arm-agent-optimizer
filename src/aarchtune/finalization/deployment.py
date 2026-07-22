"""Safe deployment command and local artifact generation."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from aarchtune.evaluation.models import SelectedProfile
from aarchtune.finalization.context import FinalizationContext
from aarchtune.finalization.errors import FinalizationInputError
from aarchtune.finalization.models import DeploymentProfile, OptimizationPassport
from aarchtune.optimization.artifacts import atomic_write_yaml
from aarchtune.runtime.capabilities import inspect_llama_server_capabilities
from aarchtune.runtime.command import build_llama_server_command
from aarchtune.runtime.config import LlamaServerConfig

_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}$")


def selected_command(context: FinalizationContext) -> list[str] | None:
    selected = context.selected_profile
    if selected is None:
        return None
    runtime = selected.runtime_configuration
    binary = context.manifest.runtime_fingerprint
    model = context.manifest.model_fingerprint
    if binary is None or model is None:
        raise FinalizationInputError("Selected command provenance is incomplete")
    config = LlamaServerConfig(
        binary_path=binary.binary_path,
        model_path=model.path,
        host="127.0.0.1",
        port=8080,
        threads=cast(int | None, runtime.get("threads")),
        threads_batch=cast(int | None, runtime.get("threads_batch")),
        batch_size=cast(int | None, runtime.get("batch_size")),
        ubatch_size=cast(int | None, runtime.get("ubatch_size")),
        context_size=cast(int | None, runtime.get("context_size")),
        parallel_slots=cast(int | None, runtime.get("parallel_slots")),
        prompt_cache=cast(bool, runtime.get("prompt_cache", False)),
        mmap=cast(bool, runtime.get("mmap", True)),
        numa_mode=cast(Any, runtime.get("numa_mode", "disabled")),
    )
    capabilities = inspect_llama_server_capabilities(binary.binary_path, include_probe_output=True)
    return build_llama_server_command(config, capabilities).arguments


def deployment_profile(
    context: FinalizationContext,
    passport: OptimizationPassport,
    command: list[str],
) -> DeploymentProfile:
    selected: SelectedProfile | None = context.selected_profile
    if selected is None:
        raise FinalizationInputError("No selected profile is available")
    return DeploymentProfile(
        profile_id=selected.candidate_id,
        candidate_hash=selected.candidate_hash,
        goal=selected.goal.value,
        selection_outcome=context.selection.outcome.value,
        runtime_settings=selected.runtime_configuration,
        runtime_binary_hash=selected.runtime_binary_hash,
        model_hash=selected.model_hash,
        workload_hash=selected.workload_hash,
        hardware_fingerprint=cast(
            dict[str, JsonValue], selected.hardware_fingerprint.model_dump(mode="json")
        ),
        quality_policy_hash=selected.quality_policy_hash,
        evaluation_id=selected.evaluation_id,
        passport_id=passport.passport_id,
        generated_command=command,
        limitations=selected.limitations,
        scope_statement=(
            "This profile is specific to the recorded hardware, runtime binary, model, "
            "workload, generation settings, and quality policy."
        ),
    )


def _bash_array(name: str, arguments: list[str]) -> str:
    lines = [f"{name}=("]
    lines.extend(f"  {shlex.quote(argument)}" for argument in arguments)
    lines.append(")")
    return "\n".join(lines)


def write_run_script(
    path: Path,
    profile: DeploymentProfile,
    passport: OptimizationPassport,
) -> None:
    binary = profile.generated_command[0]
    model_index = profile.generated_command.index("--model") + 1
    model = profile.generated_command[model_index]
    hardware_hash = str(profile.hardware_fingerprint.get("fingerprint_hash", "unknown"))
    binary_hash = shlex.quote(profile.runtime_binary_hash)
    model_hash = shlex.quote(profile.model_hash)
    script = f"""#!/usr/bin/env bash
set -euo pipefail

# AArchTune Passport: {passport.passport_id}
# Profile: {profile.profile_id}
# Hardware fingerprint: {hardware_hash}
# Binary SHA-256: {profile.runtime_binary_hash}
# Model SHA-256: {profile.model_hash}

binary={shlex.quote(binary)}
model={shlex.quote(model)}
[[ -x "$binary" ]] || {{ echo "llama-server is missing or not executable: $binary" >&2; exit 1; }}
[[ -r "$model" ]] || {{ echo "Model is missing or unreadable: $model" >&2; exit 1; }}
command -v sha256sum >/dev/null || {{ echo "sha256sum is required" >&2; exit 1; }}
[[ "$(sha256sum "$binary" | cut -d' ' -f1)" == {binary_hash} ]] || \
  {{ echo "Binary hash mismatch" >&2; exit 1; }}
[[ "$(sha256sum "$model" | cut -d' ' -f1)" == {model_hash} ]] || \
  {{ echo "Model hash mismatch" >&2; exit 1; }}

{_bash_array("args", profile.generated_command)}
printf 'Starting:'
printf ' %q' "${{args[@]}}"
printf '\n'
child_pid=''
forward_signal() {{ [[ -n "$child_pid" ]] && kill -TERM "$child_pid" 2>/dev/null || true; }}
trap forward_signal INT TERM
"${{args[@]}}" &
child_pid=$!
wait "$child_pid"
"""
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def write_reproduction_script(path: Path, context: FinalizationContext) -> None:
    config = context.manifest.configuration
    args = [
        "aarchtune",
        "evaluate",
        "--screening",
        str(config.screening_dir),
        "--output-dir",
        "${output_dir}",
        "--repetitions",
        str(config.repetitions),
        "--warmup-requests",
        str(config.warmup_requests),
        "--request-timeout",
        str(config.request_timeout_seconds),
        "--startup-timeout",
        str(config.startup_timeout_seconds),
        "--sample-interval",
        str(config.sample_interval_seconds),
    ]
    if config.quality_policy_path is not None:
        args.extend(["--quality-policy", str(config.quality_policy_path)])
    if context.summary.synthetic_fixture:
        args.append("--allow-synthetic")
    quoted = []
    for argument in args:
        quoted.append(
            '  "$output_dir"' if argument == "${output_dir}" else f"  {shlex.quote(argument)}"
        )
    script = (
        """#!/usr/bin/env bash
set -euo pipefail

echo "Warning: repeated performance can vary with thermal state, page cache, and system load." >&2
output_dir="${1:-reproduced-evaluation}"
[[ ! -e "$output_dir" ]] || { echo "Refusing to overwrite $output_dir" >&2; exit 1; }
args=(
"""
        + "\n".join(quoted)
        + '\n)\n"${args[@]}"\n'
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def write_compose(
    bundle: Path,
    image: str | None,
    profile: DeploymentProfile | None,
) -> str:
    if image is None:
        status = {
            "schema_version": "1.0",
            "available": False,
            "reason": "No container image was provided",
        }
        (bundle / "docker-compose-status.json").write_text(
            json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return "docker-compose-status.json"
    if profile is None:
        status = {
            "schema_version": "1.0",
            "available": False,
            "reason": "No deployable selected profile is available",
        }
        (bundle / "docker-compose-status.json").write_text(
            json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return "docker-compose-status.json"
    if not _IMAGE.fullmatch(image):
        raise FinalizationInputError("Container image contains unsupported characters")
    model = profile.generated_command[profile.generated_command.index("--model") + 1]
    container_command = list(profile.generated_command[1:])
    container_command[container_command.index(model)] = "/models/model.gguf"
    compose = {
        "services": {
            "llama-server": {
                "image": image,
                "command": container_command,
                "ports": ["127.0.0.1:8080:8080"],
                "volumes": [f"{model}:/models/model.gguf:ro"],
                "healthcheck": {
                    "test": ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/health"],
                    "interval": "10s",
                    "timeout": "2s",
                    "retries": 6,
                },
                "restart": "no",
            }
        }
    }
    atomic_write_yaml(bundle / "docker-compose.optimized.yaml", compose)
    return "docker-compose.optimized.yaml"
