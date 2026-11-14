"""Portable diagnostic artifacts without response bodies or external assets."""

from __future__ import annotations

from html import escape
from pathlib import Path

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.preflight.models import PreflightReport


def write_report(report: PreflightReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "preflight.json", report)
    rows = "".join(
        "<tr>"
        f"<th>{escape(check.name)}</th>"
        f"<td>{'unavailable' if check.observed is None else f'{check.observed:.4g}'}</td>"
        f"<td>{escape(check.comparison)} {check.threshold:.4g}</td>"
        f"<td>{'pass' if check.passed else 'fail'}</td></tr>"
        for check in report.checks
    )
    recommendations = "".join(f"<li>{escape(item)}</li>" for item in report.recommendations)
    errors = "".join(f"<li>{escape(item)}</li>" for item in report.evidence_errors)
    task_rows = "".join(
        f"<tr><th>{escape(task.task_id)}</th><td>{task.passed}/{task.attempts}</td>"
        f"<td>{escape(', '.join(f'{key}: {value}' for key, value in task.failures.items()))}"
        "</td></tr>"
        for task in report.tasks
    )
    evidence_label = (
        "Synthetic test evidence — not performance evidence."
        if report.synthetic
        else "Baseline diagnostics; this report establishes no speedup."
    )
    payload = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>AArchTune preflight</title><style>"
        "body{font:17px/1.6 system-ui;max-width:64rem;margin:3rem auto;padding:0 1rem;"
        "color:#152b36;background:#fafcfb}table{border-collapse:collapse;width:100%}"
        "th,td{text-align:left;padding:.6rem;border-bottom:1px solid #cbd5dc}"
        "</style><main><h1>AArchTune — Diagnostic report</h1>"
        f"<p><strong>Optimization: {'Ready for tuning' if report.can_tune else 'Blocked'}</strong>"
        f" · {escape(report.outcome)}</p><p>{escape(evidence_label)}</p>"
        f"<p>{escape(report.deployment_recommendation)}</p>"
        "<h2>Quality policy</h2><p>Rates use 0-1; repetitions and failures are counts.</p>"
        "<table><tr><th>Check</th><th>Observed</th><th>Required</th><th>Result</th></tr>"
        f"{rows}</table><h2>Diagnostic findings</h2><ul>{recommendations}</ul>"
        f"<h2>Evidence issues</h2><ul>{errors}</ul><h2>Task outcomes</h2>"
        "<table><tr><th>Task</th><th>Passed/attempts</th><th>Failure categories</th></tr>"
        f"{task_rows}</table><p>Policy SHA-256: {escape(report.policy_sha256)}</p></main></html>\n"
    )
    (output_dir / "report.html").write_text(payload, encoding="utf-8")
