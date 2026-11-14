"""Response-free, self-contained screening decision reports."""

from __future__ import annotations

import html
import json
from pathlib import Path

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.screening.diagnostics import ScreeningDiagnosis


def _text(value: object) -> str:
    return html.escape("unavailable" if value is None else str(value))


def _json(value: object) -> str:
    return html.escape(json.dumps(value, indent=2, sort_keys=True))


def _items(values: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{_text(value)}</li>" for value in values) + "</ul>"


def render_screening_details(report: ScreeningDiagnosis) -> str:
    coverage = "".join(
        f"<tr><th>{_text(key.replace('_', ' '))}</th><td>{_text(value)}</td></tr>"
        for key, value in report.coverage.items()
    )
    candidates = "".join(
        "<tr>"
        f"<th>{_text(item['id'])}</th>"
        f"<td><details><summary>Settings</summary><pre>{_json(item['settings'])}</pre>"
        f"</details>{_text(item['signature_id'])}<br>Shared with: "
        f"{_text(item['shares_signature_with'])}<br>Server-only fields: "
        f"{_text(item['server_only_fields'])}</td>"
        f"<td>{_text(item['attempted_signature_invocations'])}</td>"
        f"<td>{_text(item['decision'])}<br>{_text(item['reason_code'])}<br>"
        f"{_text(item['reason'])}<br>Signature: {_text(item['signature_status'])}<br>"
        f"Reasons: {_text(item['screening_reasons'])}<br>Failures: {_text(item['failure_codes'])}"
        f"<br>Next steps: {_text(item['next_steps'])}"
        f"<details><summary>Scenario measurements and stability</summary>"
        f"<pre>{_json(item['scenario_results'])}</pre></details></td>"
        f"<td>{_text(item['workload_execution_status'])}</td></tr>"
        for item in report.candidates
    )
    invocations = "".join(
        f"<tr><th>{_text(item['invocation_id'])}</th>"
        f"<td>{_text(item['signature_id'])} / {_text(item['scenario_id'])} / "
        f"{_text(item['repetition'])}</td><td>{_text(item['status'])}</td>"
        f"<td>{_text(item['exit_code'])}</td><td>{_text(item['failure_codes'])}"
        f"<details><summary>Measurement checks</summary>"
        f"<pre>{_json(item['measurement_checks'])}</pre></details></td></tr>"
        for item in report.invocations
    )
    return (
        f"<p>Screening: {_text(report.screening_id)} · Status: {_text(report.status)}</p>"
        "<h3>Evidence availability</h3>"
        + (
            _items(report.evidence_errors)
            if report.evidence_errors
            else "<p>Metadata available.</p>"
        )
        + f"<h3>Coverage</h3><table>{coverage}</table>"
        "<p>Attempt counts refer to shared low-level signatures. Advancement is not completed "
        "workload evaluation. Unavailable counts are not zero.</p>"
        "<h3>Planned configurations in available evidence</h3><table><thead><tr>"
        "<th>Profile</th><th>Settings and signature reuse</th><th>Signature attempts</th>"
        "<th>Screening decision and reasons</th><th>Workload execution</th>"
        f"</tr></thead><tbody>{candidates}</tbody></table>"
        "<h3>Excluded during planning</h3>"
        f"<pre>{_json(report.planning_exclusions)}</pre>"
        "<h3>Every planned benchmark invocation</h3><table><thead><tr>"
        "<th>Invocation</th><th>Signature / scenario / repetition</th><th>Status</th>"
        f"<th>Exit</th><th>Failure and measurement checks</th></tr></thead><tbody>{invocations}"
        "</tbody></table><h3>Next steps</h3>"
        + _items(report.next_steps)
        + "<h3>Diagnostic limits</h3>"
        + _items(report.limitations)
    )


def write_screening_report(report: ScreeningDiagnosis, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "screening-diagnostics.json", report)
    css = (Path(__file__).resolve().parents[1] / "templates/report.css").read_text()
    synthetic = (
        "<p>SYNTHETIC TEST EVIDENCE — not native performance evidence.</p>"
        if report.synthetic
        else ""
    )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>AArchTune Screening Diagnosis</title><style>{css}</style></head><body>"
        f"<header>{synthetic}<h1>Screening decisions and coverage</h1></header><main>"
        f"{render_screening_details(report)}</main></body></html>"
    )
    (output_dir / "screening-report.html").write_text(page, encoding="utf-8")
