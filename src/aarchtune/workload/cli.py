"""CLI commands for workload validation and synthetic fixture evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from aarchtune.workload import (
    evaluate_workload,
    load_response_fixtures,
    load_workload,
    summarize_workload,
)
from aarchtune.workload.errors import WorkloadError
from aarchtune.workload.schema import WorkloadEvaluationSummary, WorkloadValidationSummary

workload_app = typer.Typer(
    name="workload",
    help="Validate deterministic JSONL workloads and synthetic response fixtures.",
    no_args_is_help=True,
)
console = Console()


def _write_output(path: Path, payload: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{payload}\n", encoding="utf-8")
    except OSError as exc:
        raise WorkloadError(f"Could not write output to {path}: {exc}") from exc


def _render_validation(summary: WorkloadValidationSummary) -> None:
    console.print("[bold green]Workload valid[/bold green]\n")
    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Path", str(summary.path))
    table.add_row("SHA-256", summary.sha256)
    table.add_row("Tasks", str(summary.tasks))
    table.add_row("Categories", str(summary.categories))
    table.add_row("Validators", str(summary.validators))
    table.add_row("Deterministic", "yes" if summary.deterministic else "no")
    console.print(table)


def _format_rate(rate: float | None) -> str:
    return "unavailable" if rate is None else f"{rate:.1%}"


def _render_evaluation(summary: WorkloadEvaluationSummary) -> None:
    title = (
        "Evaluation passed" if _all_tasks_pass(summary) else "Evaluation completed with failures"
    )
    style = "bold green" if _all_tasks_pass(summary) else "bold red"
    console.print(f"[{style}]{title}[/{style}]\n")
    table = Table(show_header=False, box=None)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value")
    table.add_row("Tasks evaluated", f"{summary.tasks_evaluated}/{summary.total_tasks}")
    table.add_row("Missing responses", str(summary.tasks_missing_responses))
    table.add_row("Task success", _format_rate(summary.task_success_rate))
    table.add_row("Validator pass", _format_rate(summary.validator_pass_rate))
    table.add_row("JSON validity", _format_rate(summary.json_validity_rate))
    table.add_row("Request success", _format_rate(summary.request_success_rate))
    table.add_row("Timeout rate", _format_rate(summary.timeout_rate))
    console.print(table)

    failures = [result for result in summary.task_results if result.passed is not True]
    if failures:
        console.print("\n[bold]Task failures and missing responses[/bold]")
    for result in failures:
        console.print(f"- {result.task_id}: {result.reason or 'not passed'}")
        for validator in result.validator_results:
            if not validator.passed:
                console.print(f"  - {validator.validator.value}: {validator.reason}")


def _all_tasks_pass(summary: WorkloadEvaluationSummary) -> bool:
    return (
        summary.tasks_evaluated == summary.total_tasks
        and summary.task_pass_count == summary.total_tasks
    )


@workload_app.command("validate")
def validate_workload_command(
    workload: Annotated[Path, typer.Argument(help="JSONL workload to validate.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print a machine-readable validation summary.")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Also write the JSON summary to this path."),
    ] = None,
) -> None:
    """Validate structure, limits, schemas, regexes, and deterministic settings."""

    try:
        loaded = load_workload(workload)
        summary = summarize_workload(loaded)
        payload = json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True)
        if output is not None:
            _write_output(output, payload)
    except WorkloadError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(payload)
    else:
        _render_validation(summary)
        if output is not None:
            console.print(f"JSON summary written to {output}")


@workload_app.command("evaluate")
def evaluate_workload_command(
    workload: Annotated[Path, typer.Argument(help="JSONL workload to evaluate.")],
    responses: Annotated[
        Path,
        typer.Option("--responses", help="Synthetic response fixture JSONL."),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print complete machine-readable evaluation results.")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Also write complete JSON results to this path."),
    ] = None,
) -> None:
    """Evaluate response fixtures with every task's declarative validators."""

    try:
        loaded = load_workload(workload)
        fixture_responses = load_response_fixtures(responses)
        summary = evaluate_workload(loaded, fixture_responses)
        payload = json.dumps(summary.model_dump(mode="json"), indent=2, sort_keys=True)
        if output is not None:
            _write_output(output, payload)
    except WorkloadError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(payload)
    else:
        _render_evaluation(summary)
        if output is not None:
            console.print(f"JSON evaluation written to {output}")
    if not _all_tasks_pass(summary):
        raise typer.Exit(code=2)
