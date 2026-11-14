"""Run a fixed baseline or diagnose existing evidence before optimization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from aarchtune.baseline.artifacts import prepare_run_directory
from aarchtune.baseline.models import BaselineRunConfig
from aarchtune.baseline.runner import run_baseline
from aarchtune.errors import AArchTuneError
from aarchtune.evaluation.quality_policy import load_quality_policy
from aarchtune.preflight.diagnostics import diagnose_baseline
from aarchtune.preflight.models import PreflightReport
from aarchtune.preflight.report import write_report
from aarchtune.runtime.config import ResponseFormatMode


def render_report(report: PreflightReport) -> None:
    typer.echo(f"AArchTune preflight: {report.outcome}")
    typer.echo(f"Optimization: {'ready for tuning' if report.can_tune else 'blocked'}")
    for check in report.checks:
        observed = "unavailable" if check.observed is None else f"{check.observed:.4g}"
        typer.echo(
            f"  {check.name}: {observed} ({check.comparison} {check.threshold:.4g}) "
            f"{'pass' if check.passed else 'FAIL'}"
        )
    for item in report.evidence_errors + report.recommendations:
        typer.echo(f"- {item}")
    typer.echo(report.deployment_recommendation)
    if report.synthetic:
        typer.echo("Synthetic test evidence — not performance evidence.")


def preflight(
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Diagnostic artifact directory.")
    ],
    baseline_dir: Annotated[
        Path | None,
        typer.Option("--baseline-dir", help="Diagnose an existing baseline without inference."),
    ] = None,
    binary: Annotated[Path | None, typer.Option("--binary")] = None,
    model: Annotated[Path | None, typer.Option("--model")] = None,
    workload: Annotated[Path | None, typer.Option("--workload")] = None,
    quality_policy: Annotated[Path | None, typer.Option("--quality-policy")] = None,
    repetitions: Annotated[
        int | None, typer.Option("--repetitions", min=1, max=100, help="Default: policy minimum.")
    ] = None,
    threads: Annotated[int | None, typer.Option("--threads", min=1)] = None,
    context_size: Annotated[int | None, typer.Option("--context-size", min=1)] = None,
    response_format: Annotated[
        ResponseFormatMode, typer.Option("--response-format")
    ] = ResponseFormatMode.NONE,
    request_timeout: Annotated[float, typer.Option("--request-timeout", min=0.1, max=600)] = 60.0,
    startup_timeout: Annotated[float, typer.Option("--startup-timeout", min=0.1, max=600)] = 30.0,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check model quality and explain whether runtime tuning should proceed.

    Exit 0: quality passed; 4: quality failed; 3: evidence incomplete; 1: input error.
    """

    try:
        source = load_quality_policy(quality_policy)
        if baseline_dir is not None:
            if (
                any(
                    value is not None
                    for value in (binary, model, workload, repetitions, threads, context_size)
                )
                or response_format is not ResponseFormatMode.NONE
            ):
                raise ValueError("--baseline-dir cannot be combined with inference settings")
            existing = baseline_dir.expanduser().resolve()
            output = output_dir.expanduser().resolve()
            if (
                existing == output
                or existing.is_relative_to(output)
                or output.is_relative_to(existing)
            ):
                raise ValueError("Diagnostic output and baseline directories must not overlap")
        elif binary is None or model is None or workload is None:
            raise ValueError("Provide --baseline-dir or all of --binary, --model, and --workload")
        else:
            output = output_dir.expanduser().resolve()
            if any(
                path.expanduser().resolve().is_relative_to(output)
                for path in (binary, model, workload)
            ):
                raise ValueError("Diagnostic output must not contain input files")
        output = prepare_run_directory(output_dir, overwrite=overwrite)
        if baseline_dir is None:
            assert binary is not None and model is not None and workload is not None
            result = run_baseline(
                BaselineRunConfig(
                    binary_path=binary,
                    model_path=model,
                    workload_path=workload,
                    output_dir=output / "baseline",
                    repetitions=repetitions or source.policy.minimum_evidence.repetitions_per_task,
                    threads=threads,
                    context_size=context_size,
                    response_format_mode=response_format,
                    request_timeout_seconds=request_timeout,
                    startup_timeout_seconds=startup_timeout,
                )
            )
            baseline_dir = result.output_dir
        report = diagnose_baseline(baseline_dir, source)
        write_report(report, output)
    except (AArchTuneError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        render_report(report)
        typer.echo(f"Report: {output / 'report.html'}")
    if not report.can_tune:
        raise typer.Exit(code=3 if report.outcome == "evidence_incomplete" else 4)
