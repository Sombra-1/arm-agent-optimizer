"""CLI for real-workload candidate evaluation and artifact validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aarchtune.errors import AArchTuneError
from aarchtune.evaluation.models import (
    CandidateExecutionResult,
    EvaluationConfig,
    QualityDecision,
    QualityGateStatus,
)
from aarchtune.evaluation.runner import run_evaluation
from aarchtune.evaluation.validation import validate_evaluation_directory

console = Console()
evaluate_app = typer.Typer(
    name="evaluate",
    help="Evaluate advanced profiles with the real workload and quality policy.",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _read_jsonl(
    path: Path, model: type[CandidateExecutionResult]
) -> list[CandidateExecutionResult]:
    return [model.model_validate_json(line) for line in path.read_text().splitlines() if line]


def _percent(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 100:+.1f}%"


@evaluate_app.callback(invoke_without_command=True)
def evaluate_command(
    context: typer.Context,
    screening: Annotated[Path | None, typer.Option("--screening")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    repetitions: Annotated[int, typer.Option("--repetitions", min=1, max=100)] = 3,
    warmup_requests: Annotated[int, typer.Option("--warmup-requests", min=0, max=100)] = 1,
    quality_policy: Annotated[Path | None, typer.Option("--quality-policy")] = None,
    request_timeout: Annotated[float, typer.Option("--request-timeout", min=0.1, max=600.0)] = 60.0,
    startup_timeout: Annotated[float, typer.Option("--startup-timeout", min=0.1, max=600.0)] = 30.0,
    sample_interval: Annotated[float, typer.Option("--sample-interval", min=0.05, max=5.0)] = 0.1,
    settling_delay: Annotated[float, typer.Option("--settling-delay", min=0.0, max=60.0)] = 0.0,
    allow_synthetic: Annotated[bool, typer.Option("--allow-synthetic")] = False,
    allow_runtime_change: Annotated[bool, typer.Option("--allow-runtime-change")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run quality-constrained real-workload evaluation."""

    if context.invoked_subcommand is not None:
        return
    if screening is None or output_dir is None:
        typer.echo("Error: --screening and --output-dir are required", err=True)
        raise typer.Exit(code=1)
    try:
        result = run_evaluation(
            EvaluationConfig(
                screening_dir=screening,
                output_dir=output_dir,
                repetitions=repetitions,
                warmup_requests=warmup_requests,
                quality_policy_path=quality_policy,
                request_timeout_seconds=request_timeout,
                startup_timeout_seconds=startup_timeout,
                sample_interval_seconds=sample_interval,
                settling_delay_seconds=settling_delay,
                allow_synthetic=allow_synthetic,
                allow_runtime_change=allow_runtime_change,
                overwrite=overwrite,
            )
        )
    except (AArchTuneError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        summary = result.summary
        title = (
            "AArchTune Real-Workload Evaluation Complete"
            if summary is not None
            else "AArchTune Real-Workload Evaluation Failed"
        )
        console.print(f"[bold green]{title}[/bold green]\n")
        console.print(f"Evaluation ID:          {result.evaluation_id}")
        if summary is not None:
            console.print(f"Goal:                   {summary.goal.value}")
            console.print(f"Advanced candidates:    {summary.advanced_candidates}")
            console.print(f"Candidates completed:   {summary.candidates_completed}")
            console.print(f"Candidates failed:      {summary.candidates_failed}")
            console.print(f"Quality passed:         {summary.quality_passed}")
            console.print(f"Quality rejected:       {summary.quality_rejected}")
            console.print(f"Baseline drift:         {summary.drift.value}")
            if summary.synthetic_fixture:
                console.print(
                    "\n[yellow]Synthetic real-workload measurements — "
                    "not Arm performance evidence[/yellow]"
                )
            if result.selection and result.selection.selected_candidate_id:
                console.print("\nSelected profile:")
                console.print(f"  {result.selection.selected_candidate_id}")
                console.print(f"  Outcome: {result.selection.outcome.value}")
                console.print(
                    f"  Practical improvement: {_percent(result.selection.applicable_improvement)}"
                )
                if result.selection.outcome.value == "baseline_retained":
                    console.print(f"  Reason: {result.selection.reason}")
            elif result.selection:
                console.print(f"\nSelection: {result.selection.outcome.value}")
                console.print(f"Reason: {result.selection.reason}")
            result_path = result.output_dir / "candidate-results.jsonl"
            decision_path = result.output_dir / "quality-decisions.jsonl"
            if result_path.is_file() and decision_path.is_file():
                executions = _read_jsonl(result_path, CandidateExecutionResult)
                decisions = {
                    item.candidate_id: item
                    for line in decision_path.read_text().splitlines()
                    if line
                    for item in [QualityDecision.model_validate_json(line)]
                }
                measured = [
                    item
                    for item in executions
                    if item.performance and item.performance.requests_per_minute is not None
                ]
                if measured:
                    fastest = max(
                        measured,
                        key=lambda item: (
                            (item.performance.requests_per_minute or 0.0)
                            if item.performance
                            else 0.0
                        ),
                    )
                    decision = decisions[fastest.candidate_id]
                    if decision.status is not QualityGateStatus.PASSED:
                        regression = next(
                            (
                                violation
                                for violation in decision.violations
                                if violation.code == "quality_regression"
                            ),
                            None,
                        )
                        reason = (
                            regression.reason
                            if regression is not None
                            else decision.violations[0].reason
                            if decision.violations
                            else "failed quality evaluation"
                        )
                        console.print("\nFastest measured candidate rejected:")
                        console.print(f"  {fastest.candidate_id}")
                        console.print(f"  Reason: {reason}")
                        if (
                            result.selection is not None
                            and result.selection.selected_candidate_id is not None
                            and result.selection.selected_candidate_id != fastest.candidate_id
                        ):
                            console.print("  A slower quality-passing candidate was selected.")
        console.print("\nImportant:")
        console.print("  Selection is specific to the recorded machine, model, workload,")
        console.print("  runtime version, and evaluation policy.")
        console.print("  Requests-per-minute is sequential service rate, not concurrency.")
        console.print("\nArtifacts:")
        console.print(f"  {result.output_dir / 'selection.json'}")
        console.print(f"  {result.output_dir / 'quality-decisions.jsonl'}")
        if (result.output_dir / "selected-profile.yaml").exists():
            console.print(f"  {result.output_dir / 'selected-profile.yaml'}")
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@evaluate_app.command("validate")
def validate_command(
    evaluation_dir: Annotated[Path, typer.Argument(help="Evaluation artifact directory.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate evaluation evidence, quality decisions, ranking, selection, and cleanup."""

    result = validate_evaluation_directory(evaluation_dir)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Evaluation artifacts valid[/bold green]\n")
        console.print(f"Evaluation ID: {result.evaluation_id}")
        console.print("Screening provenance: valid")
        console.print("Quality and ranking: valid")
        console.print("Selection evidence: valid")
        console.print("Cleanup: confirmed")
    else:
        console.print("[bold red]Evaluation artifacts invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)
