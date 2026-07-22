"""CLI for deterministic plan creation and offline integrity validation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aarchtune.errors import AArchTuneError
from aarchtune.optimization.artifacts import validate_plan_directory
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.optimization.planner import create_search_plan

console = Console()
plan_app = typer.Typer(
    name="plan",
    help="Create or validate a deterministic candidate configuration plan without execution.",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _values(values: Sequence[object]) -> str:
    return ", ".join(str(value).lower() for value in values) if values else "baseline default"


@plan_app.callback(invoke_without_command=True)
def plan_command(
    context: typer.Context,
    baseline: Annotated[Path | None, typer.Option("--baseline")] = None,
    binary: Annotated[Path | None, typer.Option("--binary")] = None,
    model: Annotated[Path | None, typer.Option("--model")] = None,
    workload: Annotated[Path | None, typer.Option("--workload")] = None,
    goal: Annotated[OptimizationGoal | None, typer.Option("--goal")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    maximum_profiles: Annotated[int | None, typer.Option("--max-profiles", min=1, max=64)] = None,
    search_space: Annotated[Path | None, typer.Option("--search-space")] = None,
    allow_synthetic: Annotated[bool, typer.Option("--allow-synthetic")] = False,
    allow_runtime_change: Annotated[bool, typer.Option("--allow-runtime-change")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate a bounded plan from a completed baseline or explicit local inputs."""

    if context.invoked_subcommand is not None:
        return
    if goal is None or output_dir is None:
        typer.echo("Error: --goal and --output-dir are required for plan creation", err=True)
        raise typer.Exit(code=1)
    try:
        plan, root = create_search_plan(
            goal=goal,
            output_dir=output_dir,
            baseline_dir=baseline,
            binary=binary,
            model=model,
            workload=workload,
            search_space_path=search_space,
            maximum_profiles=maximum_profiles,
            allow_synthetic=allow_synthetic,
            allow_runtime_change=allow_runtime_change,
            overwrite=overwrite,
        )
    except (AArchTuneError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    summary = plan.summary
    console.print("[bold green]AArchTune Search Plan Created[/bold green]\n")
    console.print(f"Plan ID:                {plan.plan_id}")
    console.print(f"Goal:                   {plan.goal.value}")
    console.print(f"Platform:               {plan.input.hardware.architecture}")
    console.print(f"Physical cores:         {plan.input.hardware.physical_cores or 'unavailable'}")
    console.print(f"Runtime:                {plan.input.runtime.binary_path.name}")
    console.print(f"Baseline available:     {'yes' if plan.input.baseline else 'no'}")
    console.print(f"Profiles generated:     {summary.generated_profiles}")
    console.print(f"Profiles compatible:    {summary.compatible_profiles}")
    console.print(f"Profiles excluded:      {summary.excluded_possibilities}")
    console.print(f"Maximum profiles:       {summary.maximum_profiles}")
    console.print("\nCoverage:")
    console.print(f"  Thread counts:        {_values(summary.thread_counts)}")
    console.print(f"  Batch sizes:          {_values(summary.batch_sizes)}")
    console.print(f"  Micro-batches:        {_values(summary.ubatch_sizes)}")
    console.print(f"  Parallel slots:       {_values(summary.parallel_slots)}")
    console.print(f"  Prompt cache:         {_values(summary.prompt_cache_values)}")
    if plan.warnings:
        console.print("\nImportant warnings:")
        for warning in plan.warnings:
            console.print(f"  {warning.message}")
    if summary.synthetic_fixture:
        console.print(
            "\n[yellow]Synthetic planning fixture — not Arm performance evidence[/yellow]"
        )
    console.print("\nNo candidates were executed.")
    console.print("No performance conclusions were produced.")
    console.print("\nArtifacts:")
    console.print(f"  {root / 'search-plan.json'}")
    console.print(f"  {root / 'candidates.jsonl'}")


@plan_app.command("validate")
def validate_command(
    plan_dir: Annotated[Path, typer.Argument(help="Search-plan artifact directory.")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate hashes, schemas, profiles, and the no-benchmark artifact boundary."""

    result = validate_plan_directory(plan_dir)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Search plan valid[/bold green]\n")
        console.print(f"Plan ID:          {result.plan_id}")
        console.print(f"Profiles:         {result.profile_count}")
        console.print("Plan hash:        valid")
        console.print("Candidate hashes: valid")
        console.print("Benchmark files:  absent")
    else:
        console.print("[bold red]Search plan invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)
