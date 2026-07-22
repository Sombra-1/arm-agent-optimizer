"""AArchTune command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from aarchtune import __version__
from aarchtune.baseline.cli import baseline
from aarchtune.errors import OutputError
from aarchtune.evaluation.cli import evaluate_app
from aarchtune.finalization.cli import finalize_app, passport_app
from aarchtune.hardware import detect_hardware
from aarchtune.logging_config import configure_logging
from aarchtune.models import HardwareReport
from aarchtune.optimization.cli import plan_app
from aarchtune.orchestration.cli import optimize_app
from aarchtune.runtime.cli import runtime_app
from aarchtune.screening.cli import screen_app
from aarchtune.workload.cli import workload_app

app = typer.Typer(
    name="aarchtune",
    help="Quality-constrained llama.cpp autotuning for Linux Arm64.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()
app.add_typer(workload_app)
app.add_typer(runtime_app)
app.add_typer(plan_app)
app.add_typer(screen_app)
app.add_typer(evaluate_app)
app.add_typer(optimize_app)
app.add_typer(finalize_app)
app.add_typer(passport_app)
app.command()(baseline)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aarchtune {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logs.")] = False,
) -> None:
    """Inspect and tune local llama.cpp CPU inference safely."""

    del version
    configure_logging(verbose=verbose)


def _gib(value: int | None) -> str:
    return "unavailable" if value is None else f"{value / (1024**3):.2f} GiB"


def _path(value: Path | None) -> str:
    return str(value) if value else "not found"


def _render_human_report(report: HardwareReport) -> None:
    table = Table(title="AArchTune Doctor", show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Architecture", report.architecture)
    table.add_row("Arm64 target", "yes" if report.is_arm64 else "no")
    table.add_row("OS / kernel", f"{report.operating_system} / {report.kernel}")
    table.add_row("CPU model", report.cpu_model or "unavailable")
    table.add_row("Logical / physical cores", f"{report.logical_cores} / {report.physical_cores}")
    memory_summary = f"{_gib(report.memory_bytes)} / {_gib(report.memory_available_bytes)}"
    table.add_row("Memory total / available", memory_summary)
    enabled = [name for name, present in report.features.model_dump().items() if present]
    table.add_row("Relevant CPU features", ", ".join(enabled) if enabled else "none detected")
    table.add_row("llama-server", _path(report.llama_cpp.server_path))
    table.add_row("llama-bench", _path(report.llama_cpp.bench_path))
    table.add_row("llama.cpp version", report.llama_cpp.version or "unavailable")
    table.add_row("KleidiAI", report.llama_cpp.kleidiai_status.value)
    if report.model:
        model_state = "readable" if report.model.readable else f"unreadable: {report.model.error}"
        table.add_row("Model", f"{report.model.path} ({model_state})")
    console.print(table)
    if not report.is_arm64:
        console.print(
            "[yellow]This machine is not AArch64. Development and dry-run features are available,\n"
            "but real Arm optimization results cannot be produced here.[/yellow]"
        )


def _write_json(path: Path, payload: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{payload}\n", encoding="utf-8")
    except OSError as exc:
        raise OutputError(f"Could not write hardware report to {path}: {exc}") from exc


@app.command()
def doctor(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the complete report as machine-readable JSON."),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Also write the JSON report to this path."),
    ] = None,
    model: Annotated[
        Path | None,
        typer.Option("--model", help="Optionally check whether a GGUF model file is readable."),
    ] = None,
) -> None:
    """Inspect hardware, CPU capabilities, and the local llama.cpp installation."""

    report = detect_hardware(model_path=model)
    payload = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)

    if output is not None:
        try:
            _write_json(output, payload)
        except OutputError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(payload)
    else:
        _render_human_report(report)
        if output is not None:
            console.print(f"JSON report written to {output}")


if __name__ == "__main__":
    app()
