"""CLI for bounded low-level screening and offline validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aarchtune.errors import AArchTuneError
from aarchtune.screening.models import ScreeningConfig
from aarchtune.screening.runner import run_screening
from aarchtune.screening.validation import validate_screening_directory

console = Console()
screen_app = typer.Typer(
    name="screen",
    help="Run bounded llama-bench screening without real-workload candidate evaluation.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@screen_app.callback(invoke_without_command=True)
def screen_command(
    context: typer.Context,
    plan: Annotated[Path | None, typer.Option("--plan")] = None,
    bench_binary: Annotated[Path | None, typer.Option("--bench-binary")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    scenarios: Annotated[Path | None, typer.Option("--scenarios")] = None,
    advance_count: Annotated[int, typer.Option("--advance-count", min=1, max=24)] = 6,
    repetitions: Annotated[int, typer.Option("--repetitions", min=1, max=20)] = 3,
    invocation_timeout: Annotated[
        float, typer.Option("--invocation-timeout", min=0.1, max=3600.0)
    ] = 120.0,
    total_timeout: Annotated[float, typer.Option("--total-timeout", min=1.0, max=86400.0)] = 3600.0,
    sample_interval: Annotated[float, typer.Option("--sample-interval", min=0.05, max=5.0)] = 0.1,
    allow_synthetic: Annotated[bool, typer.Option("--allow-synthetic")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Screen planned candidates using only low-level machine-readable benchmarks."""

    if context.invoked_subcommand is not None:
        return
    if plan is None or output_dir is None:
        typer.echo("Error: --plan and --output-dir are required", err=True)
        raise typer.Exit(code=1)
    try:
        result = run_screening(
            ScreeningConfig(
                plan_dir=plan,
                bench_binary=bench_binary,
                output_dir=output_dir,
                scenario_path=scenarios,
                advance_count=advance_count,
                repetitions=repetitions,
                invocation_timeout_seconds=invocation_timeout,
                total_timeout_seconds=total_timeout,
                sample_interval_seconds=sample_interval,
                allow_synthetic=allow_synthetic,
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
            "AArchTune Low-Level Screening Complete"
            if summary is not None
            else "AArchTune Low-Level Screening Interrupted"
        )
        console.print(f"[bold green]{title}[/bold green]\n")
        console.print(f"Screening ID:           {result.screening_id}")
        if summary is not None:
            console.print(f"Plan profiles:          {summary.plan_profiles}")
            console.print(f"Bench signatures:       {summary.bench_signatures}")
            console.print(f"Scenarios:              {summary.scenarios}")
            console.print(f"Successful signatures:  {summary.successful_signatures}")
            console.print(f"Partial signatures:     {summary.partial_signatures}")
            console.print(f"Failed signatures:      {summary.failed_signatures}")
            console.print(f"Candidates advanced:    {summary.advanced_candidates}")
            if summary.synthetic_fixture:
                console.print(
                    "\n[yellow]Synthetic low-level measurements — "
                    "not Arm performance evidence[/yellow]"
                )
            advanced_path = result.output_dir / "advanced-candidates.jsonl"
            if advanced_path.is_file():
                console.print("\nAdvanced for real-workload evaluation:")
                for line in advanced_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        console.print(f"  {json.loads(line)['id']}")
        console.print("\nImportant:")
        console.print("  These are low-level screening results.")
        console.print("  Agent quality and end-to-end server behavior have not been evaluated.")
        console.print("  No final winner has been selected.")
        console.print("\nArtifacts:")
        console.print(f"  {result.output_dir / 'screening-summary.json'}")
        console.print(f"  {result.output_dir / 'advanced-candidates.jsonl'}")
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@screen_app.command("validate")
def validate_command(
    screening_dir: Annotated[Path, typer.Argument(help="Screening artifact directory.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate screening schemas, hashes, references, decisions, and phase boundaries."""

    result = validate_screening_directory(screening_dir)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Screening artifacts valid[/bold green]\n")
        console.print(f"Screening ID:    {result.screening_id}")
        console.print("Plan reference:  valid")
        console.print("Measurements:    referenced")
        console.print("Advanced profiles: valid")
        console.print("Final artifacts: absent")
    else:
        console.print("[bold red]Screening artifacts invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)
