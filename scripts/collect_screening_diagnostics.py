#!/usr/bin/env python3
"""Collect path-redacted screening metadata for public CI artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPECTED_FILES = (
    "screening-manifest.json",
    "screening-summary.json",
    "screening-config.json",
    "llama-bench-inspection.json",
    "scenarios.json",
    "bench-signatures.jsonl",
    "signature-membership.jsonl",
    "benchmark-matrix.jsonl",
    "raw-executions.jsonl",
    "normalized-measurements.jsonl",
    "process-summaries.jsonl",
    "failures.jsonl",
    "signature-results.jsonl",
    "advancement-decisions.jsonl",
    "advanced-candidates.jsonl",
    "non-advanced-candidates.jsonl",
)

SAFE_METADATA = {
    "screening-summary.json",
    "bench-signatures.jsonl",
    "signature-membership.jsonl",
    "normalized-measurements.jsonl",
    "process-summaries.jsonl",
    "failures.jsonl",
    "signature-results.jsonl",
    "advancement-decisions.jsonl",
}

KNOWN_FAILURE_CODES = (
    "total_timeout",
    "process_start_failure",
    "timeout",
    "nonzero_exit",
    "parser_failure",
    "settings_mismatch",
)

SIGNATURE_STATUSES = (
    "completed",
    "partial",
    "failed",
    "timed_out",
    "unstable",
    "unsupported",
)

MMAP_FORMS = (
    "numeric_true",
    "numeric_false",
    "paired_true",
    "paired_false",
    "valueless_numeric_error",
    "missing_mmap_argument",
)

UUID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)


@dataclass(frozen=True)
class Redactor:
    """Replace known ephemeral paths without changing argument structure."""

    replacements: tuple[tuple[str, str], ...]

    @classmethod
    def from_paths(
        cls,
        *,
        runner_temp: Path,
        llama_build_dir: Path,
        llama_source_dir: Path,
        model_path: Path,
        model_dir: Path,
        optimize_dir: Path,
    ) -> Redactor:
        values = {
            str(model_path.absolute()): "$MODEL_PATH",
            str(llama_build_dir.absolute()): "$LLAMA_BUILD_DIR",
            str(llama_source_dir.absolute()): "$LLAMA_SOURCE_DIR",
            str(optimize_dir.absolute()): "$OPTIMIZE_DIR",
            str(model_dir.absolute()): "$MODEL_DIR",
            str(runner_temp.absolute()): "$RUNNER_TEMP",
            "/opt/hostedtoolcache": "$HOSTED_TOOLCACHE",
            "/home/runner": "$RUNNER_HOME",
        }
        ordered = tuple(sorted(values.items(), key=lambda item: len(item[0]), reverse=True))
        return cls(replacements=ordered)

    def text(self, value: str) -> str:
        for source, replacement in self.replacements:
            value = value.replace(source, replacement)
        return UUID_PATTERN.sub("$TEMP_UUID", value)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        return value


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {line_number} must contain a JSON object")
        records.append(value)
    return records


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        for value in values
    )
    path.write_text(content, encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sanitized(source: Path, destination: Path, redactor: Redactor) -> None:
    if source.is_symlink():
        raise ValueError(f"Refusing symlinked screening artifact: {source.name}")
    if source.suffix == ".jsonl":
        records = _load_jsonl(source)
        assert records is not None
        _write_jsonl(destination, redactor.value(records))
        return
    value = _load_json(source)
    assert value is not None
    _write_json(destination, redactor.value(value))


def _command_arguments(entry: dict[str, Any]) -> list[str]:
    command = entry.get("command")
    if not isinstance(command, dict):
        return []
    arguments = command.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        return []
    return arguments


def _semantic_mmap(entry: dict[str, Any], signatures: dict[str, dict[str, Any]]) -> bool | None:
    signature = signatures.get(str(entry.get("signature_id", "")), {})
    settings = signature.get("settings")
    if not isinstance(settings, dict):
        return None
    mmap = settings.get("mmap")
    return mmap if isinstance(mmap, bool) else None


def _mmap_form(
    arguments: list[str],
    semantic_mmap: bool | None,
    capability_form: str | None,
) -> str | None:
    if "--no-mmap" in arguments:
        return "paired_false"
    if "--mmap" in arguments:
        index = arguments.index("--mmap")
        following = arguments[index + 1] if index + 1 < len(arguments) else None
        if following == "1":
            return "numeric_true"
        if following == "0":
            return "numeric_false"
        if capability_form == "numeric_01":
            return "valueless_numeric_error"
        return "paired_true"
    if semantic_mmap is not None:
        return "missing_mmap_argument"
    return None


def _nullable_counts(names: tuple[str, ...], available: bool) -> dict[str, int | None]:
    return {name: 0 if available else None for name in names}


def _derive_command_evidence(
    matrix: list[dict[str, Any]] | None,
    signatures_list: list[dict[str, Any]] | None,
    inspection: dict[str, Any] | None,
    redactor: Redactor,
) -> tuple[list[dict[str, Any]], dict[str, int | None]]:
    counts = _nullable_counts(MMAP_FORMS, matrix is not None)
    if matrix is None:
        return [], counts
    signatures = {
        str(item.get("id")): item for item in (signatures_list or []) if item.get("id") is not None
    }
    capability_form: str | None = None
    if inspection is not None:
        mappings = inspection.get("mappings")
        mmap_mapping = mappings.get("mmap") if isinstance(mappings, dict) else None
        if isinstance(mmap_mapping, dict) and isinstance(mmap_mapping.get("boolean_form"), str):
            capability_form = mmap_mapping["boolean_form"]
    evidence: list[dict[str, Any]] = []
    for entry in matrix:
        command = entry.get("command")
        command = command if isinstance(command, dict) else {}
        arguments = redactor.value(_command_arguments(entry))
        semantic = _semantic_mmap(entry, signatures)
        form = _mmap_form(arguments, semantic, capability_form)
        if form is not None:
            current = counts[form]
            counts[form] = 1 if current is None else current + 1
        evidence.append(
            {
                "invocation_id": entry.get("invocation_id"),
                "signature_id": entry.get("signature_id"),
                "scenario_id": entry.get("scenario_id"),
                "repetition": entry.get("repetition"),
                "argv": arguments,
                "semantic_mmap": semantic,
                "output_format": command.get("output_format"),
            }
        )
    return evidence, counts


def _failure_counts(failures: list[dict[str, Any]] | None) -> dict[str, int | None]:
    counts = _nullable_counts(KNOWN_FAILURE_CODES, failures is not None)
    if failures is None:
        return counts
    for failure in failures:
        code = str(failure.get("code", "unknown"))
        counts[code] = int(counts.get(code) or 0) + 1
    return counts


def _exit_counts(
    executions: list[dict[str, Any]] | None,
) -> tuple[dict[str, int] | None, int | None]:
    if executions is None:
        return None, None
    exits: Counter[str] = Counter()
    timeouts = 0
    for execution in executions:
        exit_code = execution.get("exit_code")
        exits["null" if exit_code is None else str(exit_code)] += 1
        if execution.get("timed_out") is True:
            timeouts += 1
    return dict(sorted(exits.items())), timeouts


def _signature_counts(
    signature_results: list[dict[str, Any]] | None,
) -> dict[str, int | None]:
    counts = _nullable_counts(SIGNATURE_STATUSES, signature_results is not None)
    if signature_results is None:
        return counts
    for result in signature_results:
        status = str(result.get("status", "unknown"))
        counts[status] = int(counts.get(status) or 0) + 1
    return counts


def _failure_samples(
    failures: list[dict[str, Any]] | None,
    matrix: list[dict[str, Any]] | None,
    executions: list[dict[str, Any]] | None,
    redactor: Redactor,
) -> list[dict[str, Any]]:
    matrix_by_id = {
        str(item.get("invocation_id")): item
        for item in (matrix or [])
        if item.get("invocation_id") is not None
    }
    execution_by_id = {
        str(item.get("invocation_id")): item
        for item in (executions or [])
        if item.get("invocation_id") is not None
    }
    samples: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for failure in failures or []:
        invocation_id = str(failure.get("invocation_id", ""))
        code = str(failure.get("code", "unknown"))
        reason = redactor.text(str(failure.get("reason", "")))
        identity = (invocation_id, code, reason)
        if identity in seen:
            continue
        seen.add(identity)
        matrix_entry = matrix_by_id.get(invocation_id, {})
        execution = execution_by_id.get(invocation_id, {})
        samples.append(
            {
                "invocation_id": invocation_id or None,
                "code": code,
                "reason": reason,
                "signature_id": matrix_entry.get("signature_id"),
                "scenario_id": matrix_entry.get("scenario_id"),
                "repetition": matrix_entry.get("repetition"),
                "exit_code": execution.get("exit_code"),
                "timed_out": execution.get("timed_out"),
            }
        )
        if len(samples) == 50:
            break
    return samples


def _manifest_field(
    manifest: dict[str, Any] | None,
    name: str,
) -> Any:
    if manifest is not None and name in manifest:
        return manifest[name]
    return None


def _summary_field(
    manifest: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    name: str,
) -> Any:
    if summary is not None and name in summary:
        return summary[name]
    embedded = manifest.get("summary") if manifest is not None else None
    if isinstance(embedded, dict) and name in embedded:
        return embedded[name]
    return None


def _coalesce(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def collect_diagnostics(
    *,
    optimize_dir: Path,
    evidence_dir: Path,
    redactor: Redactor,
) -> Path:
    optimize_root = optimize_dir.absolute()
    screening_dir = optimize_root / "screening"
    if screening_dir.is_symlink():
        raise ValueError("Refusing symlinked screening directory")
    if screening_dir.exists() and screening_dir.resolve().parent != optimize_root.resolve():
        raise ValueError("Screening directory escaped the optimization root")
    destination = evidence_dir / "screening-diagnostics"
    destination.mkdir(parents=True, exist_ok=True)

    inventory: dict[str, dict[str, Any]] = {}
    for name in EXPECTED_FILES:
        source = screening_dir / name
        included = source.is_file()
        target = destination / name
        if included:
            _copy_sanitized(source, target, redactor)
        inventory[name] = {
            "classification": (
                "safe_metadata" if name in SAFE_METADATA else "safe_after_path_redaction"
            ),
            "source_exists": source.exists(),
            "included": included,
            "sanitized": included,
            "size_bytes": target.stat().st_size if included else None,
            "sha256": _sha256(target) if included else None,
            "exclusion_reason": None if included else "source_missing",
        }

    manifest = _load_json(screening_dir / "screening-manifest.json")
    summary = _load_json(screening_dir / "screening-summary.json")
    inspection = _load_json(screening_dir / "llama-bench-inspection.json")
    matrix = _load_jsonl(screening_dir / "benchmark-matrix.jsonl")
    signatures = _load_jsonl(screening_dir / "bench-signatures.jsonl")
    executions = _load_jsonl(screening_dir / "raw-executions.jsonl")
    failures = _load_jsonl(screening_dir / "failures.jsonl")
    signature_results = _load_jsonl(screening_dir / "signature-results.jsonl")

    command_evidence, mmap_counts = _derive_command_evidence(
        matrix, signatures, inspection, redactor
    )
    _write_jsonl(destination / "command-evidence.jsonl", command_evidence)
    _write_json(
        destination / "failure-samples.json",
        _failure_samples(failures, matrix, executions, redactor),
    )
    failure_counts = _failure_counts(failures)
    exit_counts, timeout_count = _exit_counts(executions)
    signature_counts = _signature_counts(signature_results)
    diagnostic_summary = {
        "screening_id": _manifest_field(manifest, "screening_id")
        or _summary_field(manifest, summary, "screening_id"),
        "status": _manifest_field(manifest, "status")
        or _summary_field(manifest, summary, "status"),
        "stage": _manifest_field(manifest, "stage"),
        "error_type": _manifest_field(manifest, "error_type"),
        "error_message": redactor.value(_manifest_field(manifest, "error_message")),
        "plan_profiles": _summary_field(manifest, summary, "plan_profiles"),
        "bench_signatures": _summary_field(manifest, summary, "bench_signatures"),
        "scenarios": _summary_field(manifest, summary, "scenarios"),
        "expected_invocations": _summary_field(manifest, summary, "expected_invocations"),
        "completed_invocations": _coalesce(
            _summary_field(manifest, summary, "completed_invocations"),
            _manifest_field(manifest, "completed_invocations"),
        ),
        "failed_invocations": _coalesce(
            _summary_field(manifest, summary, "failed_invocations"),
            _manifest_field(manifest, "failed_invocations"),
        ),
        "normalized_results": _manifest_field(manifest, "normalized_results"),
        "successful_signatures": _summary_field(manifest, summary, "successful_signatures"),
        "partial_signatures": _summary_field(manifest, summary, "partial_signatures"),
        "failed_signatures": _summary_field(manifest, summary, "failed_signatures"),
        "advanced_candidates": _coalesce(
            _summary_field(manifest, summary, "advanced_candidates"),
            _manifest_field(manifest, "advanced_candidate_count"),
        ),
        "owned_processes_stopped": _manifest_field(manifest, "owned_processes_stopped"),
        "samplers_stopped": _manifest_field(manifest, "samplers_stopped"),
        "failure_codes": failure_counts,
        "execution_exit_codes": exit_counts,
        "execution_timeout_count": timeout_count,
        "signature_statuses": signature_counts,
        "mmap_command_forms": mmap_counts,
    }
    _write_json(destination / "diagnostic-summary.json", diagnostic_summary)
    _write_json(
        destination / "inventory.json",
        {
            "screening_source_available": screening_dir.is_dir(),
            "files": inventory,
            "raw_logs_included": False,
            "process_samples_included": False,
            "model_included": False,
            "raw_model_responses_included": False,
        },
    )
    return destination


def redact_tree(root: Path, redactor: Redactor) -> None:
    """Redact UTF-8 public evidence files created outside the collector."""

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        sanitized = redactor.text(text)
        if sanitized != text:
            path.write_text(sanitized, encoding="utf-8")


def _add_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runner-temp", type=Path, required=True)
    parser.add_argument("--llama-build-dir", type=Path, required=True)
    parser.add_argument("--llama-source-dir", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--optimize-dir", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Collect screening diagnostic metadata")
    _add_path_arguments(collect)
    collect.add_argument("--evidence-dir", type=Path, required=True)
    redact = commands.add_parser("redact-tree", help="Redact paths in a public evidence tree")
    _add_path_arguments(redact)
    redact.add_argument("--root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    redactor = Redactor.from_paths(
        runner_temp=args.runner_temp,
        llama_build_dir=args.llama_build_dir,
        llama_source_dir=args.llama_source_dir,
        model_path=args.model_path,
        model_dir=args.model_dir,
        optimize_dir=args.optimize_dir,
    )
    if args.command == "collect":
        collect_diagnostics(
            optimize_dir=args.optimize_dir,
            evidence_dir=args.evidence_dir,
            redactor=redactor,
        )
    else:
        redact_tree(args.root, redactor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
