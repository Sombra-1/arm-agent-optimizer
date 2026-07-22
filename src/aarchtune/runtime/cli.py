"""CLI inspection and non-benchmarking smoke lifecycle commands."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.table import Table

from aarchtune.runtime.capabilities import (
    ServerCapabilities,
    inspect_llama_server_capabilities,
)
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.errors import ArtifactWriteError, RuntimeManagementError
from aarchtune.runtime.process import LlamaServerProcess
from aarchtune.runtime.readiness import ReadinessResult

runtime_app = typer.Typer(
    name="runtime",
    help="Inspect and safely smoke-start a local llama-server without benchmarking.",
    no_args_is_help=True,
)
console = Console()


def _json(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True)


def _write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{content.rstrip()}\n", encoding="utf-8")
    except OSError as exc:
        raise ArtifactWriteError(path, str(exc)) from exc


def _render_inspection(capabilities: ServerCapabilities) -> None:
    table = Table(title="AArchTune Runtime Inspection", show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Binary", str(capabilities.binary_path))
    table.add_row("Version", capabilities.version or "unavailable")
    table.add_row("Version probe", "ok" if capabilities.version_probe.successful else "failed")
    table.add_row("Help probe", "ok" if capabilities.help_probe.successful else "failed")
    table.add_row("Supported flags", str(len(capabilities.supported_flags)))
    table.add_row("KleidiAI", capabilities.kleidiai_status.value)
    console.print(table)
    console.print("\n" + " ".join(sorted(capabilities.supported_flags)))


@runtime_app.command("inspect")
def inspect_runtime_command(
    binary: Annotated[
        Path | None,
        typer.Option("--binary", help="Explicit llama-server executable; otherwise use PATH."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable capability inspection JSON.")
    ] = False,
    include_probe_output: Annotated[
        bool,
        typer.Option(
            "--include-probe-output",
            help="Include bounded help/version stdout and stderr in diagnostics.",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Also write runtime-inspection.json."),
    ] = None,
) -> None:
    """Probe --help and --version without inferring capabilities from version numbers."""

    try:
        capabilities = inspect_llama_server_capabilities(
            binary,
            include_probe_output=include_probe_output,
        )
        payload = _json(capabilities)
        if output is not None:
            _write_text(output, payload)
    except (RuntimeManagementError, ValidationError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(payload)
    else:
        _render_inspection(capabilities)
        if output is not None:
            console.print(f"Inspection written to {output}")


def _write_smoke_artifacts(
    output_dir: Path,
    capabilities: ServerCapabilities,
    server: LlamaServerProcess,
    readiness: ReadinessResult | None,
) -> None:
    _write_text(output_dir / "runtime-inspection.json", _json(capabilities))
    if server.command is not None:
        _write_text(output_dir / "server-command.json", _json(server.command))
    _write_text(output_dir / "server-startup.log", server.log_text)
    if readiness is not None:
        _write_text(output_dir / "readiness.json", _json(readiness))
    if server.shutdown_result is not None:
        _write_text(output_dir / "shutdown.json", _json(server.shutdown_result))


@runtime_app.command("smoke-start")
def smoke_start_command(
    binary: Annotated[Path, typer.Option("--binary", help="llama-server executable.")],
    model: Annotated[Path, typer.Option("--model", help="Readable model path.")],
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Write bounded runtime lifecycle artifacts here."),
    ] = None,
    startup_timeout: Annotated[
        float,
        typer.Option("--startup-timeout", help="Maximum readiness wait in seconds."),
    ] = 5.0,
    shutdown_timeout: Annotated[
        float,
        typer.Option("--shutdown-timeout", help="Graceful shutdown wait in seconds."),
    ] = 2.0,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print readiness and shutdown JSON.")
    ] = False,
) -> None:
    """Start, network-check, and stop llama-server without running inference benchmarks."""

    server: LlamaServerProcess | None = None
    readiness: ReadinessResult | None = None
    try:
        capabilities = inspect_llama_server_capabilities(binary)
        config = LlamaServerConfig(
            binary_path=binary,
            model_path=model,
            startup_timeout_seconds=startup_timeout,
            shutdown_timeout_seconds=shutdown_timeout,
        )
        server = LlamaServerProcess(config, capabilities)
        with server:
            readiness = server.wait_until_ready()
        if output_dir is not None:
            _write_smoke_artifacts(output_dir, capabilities, server, readiness)
    except (RuntimeManagementError, ValidationError) as exc:
        if server is not None:
            server.stop()
            if output_dir is not None:
                with suppress(RuntimeManagementError):
                    _write_smoke_artifacts(output_dir, capabilities, server, readiness)
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if server.shutdown_result is None:
        typer.echo("Error: shutdown result is unavailable", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "readiness": readiness.model_dump(mode="json"),
                    "shutdown": server.shutdown_result.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        console.print("[bold green]llama-server smoke start complete[/bold green]")
        console.print(f"Ready:      {readiness.endpoint}")
        console.print(f"Method:     {readiness.method}")
        console.print(f"Attempts:   {readiness.attempts}")
        console.print(f"Stopped:    {'yes' if server.shutdown_result.stopped else 'no'}")
        console.print(f"Forced:     {'yes' if server.shutdown_result.forced else 'no'}")
        if output_dir is not None:
            console.print(f"Artifacts:  {output_dir}")
