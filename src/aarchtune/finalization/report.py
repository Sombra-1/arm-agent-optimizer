"""Self-contained semantic HTML and accessible inline SVG report generation."""

from __future__ import annotations

import html
from pathlib import Path
from string import Template
from typing import Any

from aarchtune.finalization.models import ReportData

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"


def _percent(value: object) -> str:
    return "unavailable" if not isinstance(value, (int, float)) else f"{value * 100:.1f}%"


def _change(value: object, *, lower_is_better: bool = False) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    displayed = -value if lower_is_better else value
    return f"{displayed * 100:+.1f}%"


def _metric(value: object, unit: str, scale: float = 1.0) -> str:
    return (
        "unavailable"
        if not isinstance(value, (int, float))
        else f"{value * scale:,.2f} {unit}".strip()
    )


def _bars(candidates: list[dict[str, Any]], field: str, label: str, unit: str) -> str:
    available = [item for item in candidates if isinstance(item.get(field), (int, float))]
    if not available:
        return '<p class="unavailable">Metric unavailable for all candidates.</p>'
    maximum = max(float(item[field]) for item in available) or 1.0
    rows = []
    for index, item in enumerate(available):
        width = 520 * float(item[field]) / maximum
        css = (
            "selected"
            if item["selected"]
            else "baseline"
            if item["baseline"]
            else ("rejected" if item["quality_status"] != "passed" else "passing")
        )
        y = 28 + index * 34
        rows.append(
            f'<text x="0" y="{y}" class="chart-label">{html.escape(str(item["id"]))}</text>'
            f'<rect x="210" y="{y - 16}" width="{width:.1f}" height="20" class="{css}" />'
            f'<text x="{220 + width:.1f}" y="{y}" class="chart-value">'
            f"{html.escape(_metric(item[field], unit))}</text>"
        )
    height = 50 + len(rows) * 34
    return (
        f'<svg viewBox="0 0 900 {height}" role="img" aria-label="{html.escape(label)}">'
        + "".join(rows)
        + "</svg>"
    )


def _pareto(data: ReportData) -> str:
    records = data.pareto.records
    if not records:
        return '<p class="unavailable">Pareto metrics unavailable.</p>'
    max_rpm = max(item.requests_per_minute for item in records) or 1.0
    max_p95 = max(item.p95_latency_seconds for item in records) or 1.0
    points = []
    for item in records:
        x = 70 + 700 * item.p95_latency_seconds / max_p95
        y = 390 - 320 * item.requests_per_minute / max_rpm
        css = (
            "selected"
            if item.selected
            else "baseline"
            if item.baseline
            else ("dominated" if item.dominated else "passing")
        )
        points.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" class="{css}"><title>'
            f"{html.escape(item.candidate_id)}</title></circle>"
        )
    return (
        '<svg viewBox="0 0 840 430" role="img" aria-label="P95 latency versus service rate">'
        '<line x1="70" y1="390" x2="790" y2="390" class="axis"/>'
        '<line x1="70" y1="40" x2="70" y2="390" class="axis"/>'
        '<text x="380" y="425">P95 latency →</text>'
        '<text x="8" y="220" transform="rotate(-90 8 220)">Requests/minute →</text>'
        + "".join(points)
        + "</svg>"
    )


def _funnel(funnel: dict[str, int]) -> str:
    maximum = max(funnel.values()) if funnel else 1
    rows = []
    for index, (name, value) in enumerate(funnel.items()):
        width = 640 * value / maximum if maximum else 0
        y = 30 + index * 42
        rows.append(
            f'<text x="0" y="{y}">{html.escape(name.replace("_", " ").title())}</text>'
            f'<rect x="220" y="{y - 18}" width="{width:.1f}" height="24" class="passing"/>'
            f'<text x="{230 + width:.1f}" y="{y}">{value}</text>'
        )
    height = 70 + len(rows) * 42
    return (
        f'<svg viewBox="0 0 920 {height}" role="img" aria-label="Stage funnel">'
        + "".join(rows)
        + "</svg>"
    )


def _table(candidates: list[dict[str, Any]]) -> str:
    rows = []
    for item in candidates:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['id']))}</td>"
            f"<td>{html.escape(str(item['execution_status']))}</td>"
            f"<td>{html.escape(str(item['quality_status']))}</td>"
            f"<td>{_metric(item.get('requests_per_minute'), 'req/min')}</td>"
            f"<td>{_metric(item.get('p95_latency_seconds'), 'ms', 1000)}</td>"
            f"<td>{_metric(item.get('peak_rss_bytes'), 'MiB', 1 / 1048576)}</td>"
            f"<td>{_percent(item.get('task_success_rate'))}</td>"
            f"<td>{_percent(item.get('json_validity_rate'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _quality_distribution(candidates: list[dict[str, Any]]) -> str:
    statuses = {
        "Quality passed": sum(item.get("quality_status") == "passed" for item in candidates),
        "Quality rejected": sum(item.get("quality_status") == "failed" for item in candidates),
        "Other / unavailable": sum(
            item.get("quality_status") not in {"passed", "failed"} for item in candidates
        ),
    }
    maximum = max(statuses.values(), default=1) or 1
    rows = []
    for index, (label, value) in enumerate(statuses.items()):
        width = 520 * value / maximum
        y = 30 + index * 38
        css = "passing" if label == "Quality passed" else "rejected"
        rows.append(
            f'<text x="0" y="{y}">{html.escape(label)}</text>'
            f'<rect x="190" y="{y - 18}" width="{width:.1f}" height="24" class="{css}" />'
            f'<text x="{200 + width:.1f}" y="{y}">{value}</text>'
        )
    return (
        '<svg viewBox="0 0 820 160" role="img" '
        'aria-label="Quality passing and rejected candidate counts">' + "".join(rows) + "</svg>"
    )


def _quality_rows(values: dict[str, Any]) -> str:
    if not values:
        return '<tr><td colspan="4">Unavailable</td></tr>'
    rows = []
    for name, raw in sorted(values.items()):
        item = raw if isinstance(raw, dict) else {}
        passed = item.get("passed", item.get("pass_count"))
        failed = item.get("failed", item.get("failure_count"))
        rate = item.get("rate", item.get("pass_rate", item.get("success_rate")))
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td><td>{html.escape(str(passed))}</td>"
            f"<td>{html.escape(str(failed))}</td><td>{_percent(rate)}</td></tr>"
        )
    return "".join(rows)


def render_report(data: ReportData) -> str:
    template = Template((TEMPLATE_ROOT / "report.html.j2").read_text(encoding="utf-8"))
    css = (TEMPLATE_ROOT / "report.css").read_text(encoding="utf-8")
    improvements = data.hero.get("improvements", {})
    improvements = improvements if isinstance(improvements, dict) else {}
    synthetic = (
        '<div class="synthetic" role="alert">SYNTHETIC TEST EVIDENCE<br>'
        "Not Arm or model-performance evidence</div>"
        if data.synthetic
        else ""
    )
    fastest = data.fastest_rejected
    rejected_card = ""
    if fastest:
        reasons = fastest.get("rejection_reasons", [])
        reasons = reasons if isinstance(reasons, list) else []
        service_rate = _metric(fastest.get("requests_per_minute"), "req/min")
        reason_items = "".join(
            f"<li>{html.escape(str(item.get('reason', 'Quality policy violation')))}</li>"
            for item in reasons
            if isinstance(item, dict)
        )
        rejected_card = (
            '<article class="card rejected-card"><h2>Fastest measured candidate rejected</h2>'
            f"<p><strong>Candidate:</strong> {html.escape(str(fastest.get('candidate_id')))}</p>"
            f"<p><strong>Service rate:</strong> {service_rate} "
            f"({_change(fastest.get('service_rate_improvement'))})</p>"
            f"<p><strong>Task success:</strong> "
            f"{_percent(fastest.get('baseline_task_success_rate'))} → "
            f"{_percent(fastest.get('task_success_rate'))}</p>"
            f"<p><strong>JSON validity:</strong> "
            f"{_percent(fastest.get('baseline_json_validity_rate'))} → "
            f"{_percent(fastest.get('json_validity_rate'))}</p>"
            f"<ul>{reason_items}</ul><p><strong>Decision:</strong> "
            "Rejected by quality policy</p></article>"
        )
    selected = data.selected_candidate_id or "No deployable profile"
    outcome_title = (
        "Baseline retained"
        if data.outcome == "baseline_retained"
        else "No eligible candidate"
        if data.outcome == "no_eligible_candidate"
        else "Evaluation invalidated by drift"
        if data.outcome == "evaluation_invalidated_by_drift"
        else "Candidate selected"
    )
    selected_settings = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in (data.selected_settings or {}).items()
    )
    hashes = "".join(
        f"<tr><td>{html.escape(item.stage)}</td><td>{html.escape(item.path)}</td>"
        f"<td><code>{item.sha256}</code></td></tr>"
        for item in data.artifact_hashes
    )
    return template.safe_substitute(
        css=css,
        synthetic_banner=synthetic,
        outcome_title=html.escape(outcome_title),
        selected_candidate=html.escape(selected),
        service_improvement=_change(improvements.get("requests_per_minute")),
        p95_improvement=_change(improvements.get("p95_latency_seconds"), lower_is_better=True),
        memory_improvement=_change(
            improvements.get("measured_peak_rss_bytes"), lower_is_better=True
        ),
        quality_preserved="Quality preserved"
        if data.hero.get("quality_preserved")
        else "No deployable quality-passing selection",
        evaluation_id=html.escape(data.evaluation_id),
        passport_id=html.escape(data.passport_id),
        rejected_card=rejected_card,
        funnel_svg=_funnel(data.funnel),
        service_svg=_bars(
            data.candidates, "requests_per_minute", "Candidate service rate", "req/min"
        ),
        latency_svg=_bars(data.candidates, "p95_latency_seconds", "Candidate P95 latency", "s"),
        memory_svg=_bars(data.candidates, "peak_rss_bytes", "Candidate peak RSS", "bytes"),
        pareto_svg=_pareto(data),
        candidate_rows=_table(data.candidates),
        quality_distribution_svg=_quality_distribution(data.candidates),
        category_quality_rows=_quality_rows(data.per_category_quality),
        validator_quality_rows=_quality_rows(data.per_validator_quality),
        drift=html.escape(str(data.drift)),
        hardware=html.escape(str(data.hardware)),
        runtime=html.escape(str(data.runtime)),
        provenance=html.escape(str({"model": data.model, "workload": data.workload})),
        selected_settings=selected_settings or '<tr><td colspan="2">Unavailable</td></tr>',
        reproduction=html.escape(data.reproduction_command or "No deployment command available"),
        methodology=(
            "Fresh baseline replay, deterministic workload execution, absolute and "
            "baseline-relative "
            "quality gates, ending baseline drift sentinel, then goal-specific ranking."
        ),
        limitations="".join(f"<li>{html.escape(item)}</li>" for item in data.limitations),
        hashes=hashes,
        generated_at=html.escape(data.generated_at.isoformat()),
    )
