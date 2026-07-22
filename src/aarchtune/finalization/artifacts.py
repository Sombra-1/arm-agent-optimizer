"""Final bundle persistence, checksums, and human-readable README generation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.optimization.artifacts import atomic_write_yaml


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path, root: Path) -> str:
    return os.path.relpath(path.resolve(), root.resolve())


def write_json(path: Path, value: Any) -> None:
    if hasattr(value, "model_dump"):
        atomic_write_json(path, value)
    else:
        atomic_write_json(path, value)


def write_yaml(path: Path, value: Any) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    atomic_write_yaml(path, payload)


def artifact_hashes(root: Path, *, exclusions: set[str] | None = None) -> dict[str, str]:
    skipped = exclusions or set()
    return {
        item.name: file_sha256(item)
        for item in sorted(root.iterdir(), key=lambda entry: entry.name)
        if item.is_file() and item.name not in skipped
    }


def write_checksums(root: Path) -> None:
    hashes = artifact_hashes(root, exclusions={"checksums.sha256"})
    payload = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    (root / "checksums.sha256").write_text(payload, encoding="utf-8")


def read_checksums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or Path(name).name != name:
            raise ValueError(f"Malformed checksum line: {line!r}")
        values[name] = digest
    return values


def write_bundle_readme(
    path: Path,
    *,
    synthetic: bool,
    deployable: bool,
    outcome: str,
) -> None:
    warning = (
        "\nSYNTHETIC TEST EVIDENCE — NOT ARM OR MODEL-PERFORMANCE EVIDENCE.\n" if synthetic else ""
    )
    deployment = (
        "Run ./run-optimized.sh after reviewing selected-profile.yaml and verifying local paths."
        if deployable
        else "No deployment script was generated because this outcome is diagnostic-only."
    )
    path.write_text(
        f"""AArchTune final evidence bundle
================================
{warning}
Outcome: {outcome}

Open report.html directly in a browser; it is self-contained and makes no network requests.
Verify optimization-passport.json with:
  aarchtune passport verify optimization-passport.json
Validate this bundle with:
  aarchtune finalize validate .

{deployment}
Use reproduce-evaluation.sh, when present, to repeat the selected evaluation inputs.

This selection is specific to the recorded hardware, runtime binary, model, workload,
generation settings, and quality policy. Performance can vary with system load, page cache,
and thermal conditions.

Privacy: raw model responses remain in the upstream evaluation directory. Review and sanitize
those upstream artifacts before publication. This final bundle does not copy response bodies.
""",
        encoding="utf-8",
    )
