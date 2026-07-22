"""CLI for final bundle creation, validation, and Passport verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aarchtune.errors import AArchTuneError
from aarchtune.finalization.models import FinalizeConfig
from aarchtune.finalization.passport import verify_passport
from aarchtune.finalization.runner import finalize_evaluation
from aarchtune.finalization.validation import validate_bundle

console = Console()
finalize_app = typer.Typer(
    name="finalize",
    help="Create and validate a reproducible evidence and deployment bundle.",
    invoke_without_command=True,
    no_args_is_help=True,
)
passport_app = typer.Typer(name="passport", help="Verify Optimization Passport integrity.")


@finalize_app.callback(invoke_without_command=True)
def finalize_command(
    context: typer.Context,
    evaluation: Annotated[Path | None, typer.Option("--evaluation")] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    container_image: Annotated[str | None, typer.Option("--container-image")] = None,
    allow_synthetic: Annotated[bool, typer.Option("--allow-synthetic")] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Finalize a validated real-workload evaluation."""

    if context.invoked_subcommand is not None:
        return
    if evaluation is None or output_dir is None:
        typer.echo("Error: --evaluation and --output-dir are required", err=True)
        raise typer.Exit(code=1)
    try:
        result = finalize_evaluation(
            FinalizeConfig(
                evaluation_dir=evaluation,
                output_dir=output_dir,
                allow_synthetic=allow_synthetic,
                container_image=container_image,
                overwrite=overwrite,
            )
        )
    except (AArchTuneError, OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        console.print("[bold green]AArchTune Final Bundle Created[/bold green]\n")
        console.print(f"Bundle ID:           {result.bundle_id}")
        console.print(f"Outcome:             {result.outcome}")
        console.print(f"Selected profile:    {result.selected_profile_id or 'none'}")
        console.print(f"Passport:            {result.passport_id}")
        if result.synthetic:
            console.print(
                "\n[bold yellow]Synthetic test evidence — "
                "not Arm performance evidence[/bold yellow]"
            )
        console.print("\nArtifacts:")
        console.print(f"  {result.output_dir / 'optimization-passport.json'}")
        console.print(f"  {result.output_dir / 'report.html'}")
        if (result.output_dir / "run-optimized.sh").exists():
            console.print(f"  {result.output_dir / 'run-optimized.sh'}")
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)


@finalize_app.command("validate")
def validate_command(
    bundle_dir: Annotated[Path, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate final checksums, Passport, report, and deployment artifacts."""

    result = validate_bundle(bundle_dir)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Final bundle valid[/bold green]")
        console.print(f"Bundle ID: {result.bundle_id}")
    else:
        console.print("[bold red]Final bundle invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)


@passport_app.command("verify")
def passport_verify(
    passport_path: Annotated[Path, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify canonical content and referenced-stage hashes."""

    result = verify_passport(passport_path)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    elif result.valid:
        console.print("[bold green]Optimization Passport valid[/bold green]")
        console.print(f"Passport ID: {result.passport_id}")
    else:
        console.print("[bold red]Optimization Passport invalid[/bold red]")
        for error in result.errors:
            console.print(f"  {error}")
    if not result.valid:
        raise typer.Exit(code=1)
