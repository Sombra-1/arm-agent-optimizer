"""CLI for the complete validated optimization workflow."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aarchtune.errors import AArchTuneError
from aarchtune.optimization.models import OptimizationGoal
from aarchtune.orchestration.models import OptimizeConfig
from aarchtune.orchestration.runner import run_optimization
from aarchtune.orchestration.validation import validate_optimization

console = Console()
optimize_app = typer.Typer(
    name="optimize",
    help="Run doctor, baseline, planning, screening, evaluation, and finalization.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@optimize_app.callback(invoke_without_command=True)
def optimize_command(
    context: typer.Context,
    server_binary: Annotated[Path | None, typer.Option("--server-binary")] = None,
    bench_binary: Annotated[Path | None, typer.Option("--bench-binary")] = None,
    model: Annotated[Path | None, typer.Option("--model")] = None,
    workload: Annotated[Path | None, typer.Option("--workload")] = None,
    goal: Annotated[OptimizationGoal, typer.Option("--goal")] = OptimizationGoal.BALANCED,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    search_space: Annotated[Path | None, typer.Option("--search-space")] = None,
    screening_scenarios: Annotated[Path | None, typer.Option("--screening-scenarios")] = None,
    quality_policy: Annotated[Path | None, typer.Option("--quality-policy")] = None,
    container_image: Annotated[str | None, typer.Option("--container-image")] = None,
    baseline_repetitions: Annotated[
        int, typer.Option("--baseline-repetitions", min=1, max=100)
    ] = 2,
    evaluation_repetitions: Annotated[
        int, typer.Option("--evaluation-repetitions", min=1, max=100)
    ] = 3,
    warmup_requests: Annotated[int, typer.Option("--warmup-requests", min=0, max=100)] = 1,
    advance_count: Annotated[int, typer.Option("--advance-count", min=1, max=24)] = 6,
    max_profiles: Annotated[int | None, typer.Option("--max-profiles", min=1, max=64)] = None,
    screening_repetitions: Annotated[
        int, typer.Option("--screening-repetitions", min=1, max=20)
    ] = 3,
    request_timeout: Annotated[float, typer.Option("--request-timeout", min=0.1, max=600)] = 60.0,
    startup_timeout: Annotated[float, typer.Option("--startup-timeout", min=0.1, max=600)] = 30.0,
    sample_interval: Annotated[float, typer.Option("--sample-interval", min=0.05, max=5)] = 0.1,
    maximum_total_duration: Annotated[
        float, typer.Option("--maximum-total-duration", min=1, max=86400)
    ] = 7200.0,
    allow_synthetic: Annotated[bool, typer.Option("--allow-synthetic")] = False,
    allow_non_arm_development: Annotated[bool, typer.Option("--allow-non-arm-development")] = False,
    allow_runtime_change: Annotated[bool, typer.Option("--allow-runtime-change")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the complete local quality-constrained AArchTune pipeline."""

    if context.invoked_subcommand is not None:
        return
    required = {
        "--server-binary": server_binary,
        "--bench-binary": bench_binary,
        "--model": model,
        "--workload": workload,
        "--output-dir": output_dir,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        typer.echo(f"Error: required options missing: {', '.join(missing)}", err=True)
        raise typer.Exit(code=1)
    assert server_binary and bench_binary and model and workload and output_dir
    config = OptimizeConfig(
        server_binary=server_binary,
        bench_binary=bench_binary,
        model=model,
        workload=workload,
        goal=goal,
        output_dir=output_dir,
        search_space=search_space,
        screening_scenarios=screening_scenarios,
        quality_policy=quality_policy,
        container_image=container_image,
        baseline_repetitions=baseline_repetitions,
        evaluation_repetitions=evaluation_repetitions,
        warmup_requests=warmup_requests,
        advance_count=advance_count,
        max_profiles=max_profiles,
        screening_repetitions=screening_repetitions,
        request_timeout_seconds=request_timeout,
        startup_timeout_seconds=startup_timeout,
        sample_interval_seconds=sample_interval,
        maximum_total_duration_seconds=maximum_total_duration,
        allow_synthetic=allow_synthetic,
        allow_non_arm_development=allow_non_arm_development,
        allow_runtime_change=allow_runtime_change,
        overwrite=overwrite,
        resume=resume,
    )
    try:
        result = run_optimization(config)
    except (AArchTuneError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        console.print("[bold green]AArchTune Optimization Complete[/bold green]\n")
        console.print(f"Outcome:             {result.outcome or result.status.value}")
        console.print(f"Selected profile:    {result.selected_profile_id or 'none'}")
        console.print(f"Stages reused:       {len(result.reused_stages)}")
        if result.final_dir:
            report_data_path = result.final_dir / "report-data.json"
            if report_data_path.is_file():
                data = json.loads(report_data_path.read_text())
                improvements = data.get("hero", {}).get("improvements", {})
                console.print("\nCompared with fresh baseline:")
                for label, key, lower_is_better in (
                    ("Sequential service rate", "requests_per_minute", False),
                    ("Median latency", "median_latency_seconds", True),
                    ("P95 latency", "p95_latency_seconds", True),
                    ("Peak measured RSS", "measured_peak_rss_bytes", True),
                ):
                    value = improvements.get(key)
                    display = -value if value is not None and lower_is_better else value
                    console.print(
                        f"  {label + ':':27} "
                        + ("unavailable" if display is None else f"{display * 100:+.1f}%")
                    )
                if data.get("synthetic"):
                    console.print(
                        "\n[bold yellow]Synthetic test evidence — "
                        "not Arm performance evidence[/bold yellow]"
                    )
            run_script = result.final_dir / "run-optimized.sh"
            if run_script.is_file():
                console.print("\nDeployment:")
                console.print(f"  {run_script}")
            console.print("Report:")
            console.print(f"  {result.final_dir / 'report.html'}")
        console.print("\nImportant:")
        console.print("  Selection is specific to the recorded machine, model, workload,")
        console.print("  runtime binary, generation settings, and quality policy.")
        if result.status.value == "interrupted":
            args = ["aarchtune", "optimize", "--output-dir", str(result.output_dir), "--resume"]
            console.print(f"Safe resume: {shlex.join(args)} with the original input options")
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@optimize_app.command("validate")
def validate_command(
    optimize_dir: Annotated[Path, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate every native stage and the final bundle."""

    result = validate_optimization(optimize_dir)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Optimization workflow valid[/bold green]")
        console.print(f"Optimize ID: {result.optimize_id}")
        console.print("All native stages and final evidence validated.")
    else:
        console.print("[bold red]Optimization workflow invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)
