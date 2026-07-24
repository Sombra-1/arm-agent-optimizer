from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/real-arm64-smoke.yml"


def _patterns() -> tuple[str, str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    environment = workflow["jobs"]["native-arm64"]["env"]
    return (
        environment["PUBLIC_EVIDENCE_SCAN_PATTERN"],
        environment["HF_TOKEN_ASSIGNMENT_PATTERN"],
    )


def _matches(pattern: str, text: str) -> bool:
    result = subprocess.run(
        ["grep", "-Eq", pattern],
        input=text,
        text=True,
        check=False,
    )
    return result.returncode == 0


def test_token_documentation_placeholders_are_allowed() -> None:
    general, assignment = _patterns()
    allowed = [
        "value from HF_TOKEN environment variable",
        "--hf-token <token>",
        "HF_TOKEN=<token>",
        '"HF_TOKEN": "<token>"',
    ]

    assert all(not _matches(general, value) for value in allowed)
    assert all(not _matches(assignment, value) for value in allowed)


def test_actual_token_values_and_authorization_are_rejected() -> None:
    general, assignment = _patterns()

    assert _matches(assignment, "HF_TOKEN=actual-value")
    assert _matches(assignment, '"HF_TOKEN": "actual-value"')
    assert _matches(general, "Authorization: Bearer actual-value")
