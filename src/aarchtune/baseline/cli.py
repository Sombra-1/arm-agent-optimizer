"""CLI entry point for one reproducible fixed-configuration baseline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console

from aarchtune.baseline.models import BaselineRunConfig, BaselineRunResult
from aarchtune.baseline.runner import run_baseline
from aarchtune.errors import AArchTuneError

console = Console()


def _rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 100:.1f}%"


def _latency(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 1000:.1f} ms"


def _memory(value: int | float | None) -> str:
    return "unavailable" if value is None else f"{float(value) / (1024**2):.1f} MiB"


def _render(result: BaselineRunResult) -> None:
    if result.summary is None:
        console.print(f"[red]AArchTune Baseline {result.status.value.title()}[/red]")
        console.print(f"Run ID:              {result.run_id}")
        console.print(f"Artifacts:           {result.output_dir}")
        if result.failure:
            console.print(f"Failure:             {result.failure.message}")
        return
    summary = result.summary
    benchmark = summary.benchmark
    quality = summary.quality
    process = summary.process
    console.print("[bold green]AArchTune Baseline Complete[/bold green]\n")
    console.print(f"Run ID:              {summary.run_id}")
    console.print(f"Platform:            {summary.platform_architecture}")
    console.print(f"Arm64 verified:      {'yes' if summary.is_arm64 else 'no'}")
    runtime = summary.runtime_version or "unavailable"
    if summary.synthetic_fixture:
        runtime += " (synthetic fixture — not performance evidence)"
    console.print(f"Runtime:             {runtime}")
    console.print(f"KleidiAI:            {summary.kleidiai_status}")
    console.print(f"Tasks:               {summary.workload_task_count}")
    console.print(f"Repetitions:         {summary.repetitions}")
    console.print(
        "Measured attempts:   "
        f"{benchmark.measured_attempts_completed}/{benchmark.total_configured_attempts}"
    )
    console.print(f"Request success:     {_rate(quality.request_success_rate)}")
    console.print(f"Task success:        {_rate(quality.task_attempt_success_rate)}")
    console.print(f"Median latency:      {_latency(benchmark.latency_seconds.median)}")
    console.print(f"P95 latency:         {_latency(benchmark.latency_seconds.p95)}")
    console.print(f"Peak measured RSS:   {_memory(process.measured_phase_peak_rss_bytes.value)}")
    console.print("TTFT:                unavailable — non-streaming client")
    console.print("\nArtifacts:")
    for name in ("manifest.json", "baseline-summary.json", "quality-summary.json"):
        console.print(f"  {result.output_dir / name}")


def baseline(
    binary: Annotated[Path, typer.Option("--binary", help="llama-server executable.")],
    model: Annotated[Path, typer.Option("--model", help="Readable local GGUF model path.")],
    workload: Annotated[Path, typer.Option("--workload", help="Validated workload JSONL.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Dedicated baseline artifact directory.")
    ],
    repetitions: Annotated[int, typer.Option("--repetitions", min=1, max=100)] = 1,
    warmup_requests: Annotated[int, typer.Option("--warmup-requests", min=0, max=100)] = 1,
    threads: Annotated[int | None, typer.Option("--threads", min=1)] = None,
    threads_batch: Annotated[int | None, typer.Option("--threads-batch", min=1)] = None,
    batch_size: Annotated[int | None, typer.Option("--batch-size", min=1)] = None,
    ubatch_size: Annotated[int | None, typer.Option("--ubatch-size", min=1)] = None,
    context_size: Annotated[int | None, typer.Option("--context-size", min=1)] = None,
    parallel_slots: Annotated[int | None, typer.Option("--parallel-slots", min=1)] = None,
    prompt_cache: Annotated[bool, typer.Option("--prompt-cache/--no-prompt-cache")] = False,
    mmap: Annotated[bool, typer.Option("--mmap/--no-mmap")] = True,
    request_timeout: Annotated[float, typer.Option("--request-timeout", min=0.1, max=600.0)] = 60.0,
    startup_timeout: Annotated[float, typer.Option("--startup-timeout", min=0.1, max=600.0)] = 30.0,
    sample_interval: Annotated[float, typer.Option("--sample-interval", min=0.05, max=5.0)] = 0.1,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Measure one fixed llama-server configuration and persist a reproducible baseline."""

    try:
        config = BaselineRunConfig(
            binary_path=binary,
            model_path=model,
            workload_path=workload,
            output_dir=output_dir,
            repetitions=repetitions,
            warmup_requests=warmup_requests,
            threads=threads,
            threads_batch=threads_batch,
            batch_size=batch_size,
            ubatch_size=ubatch_size,
            context_size=context_size,
            parallel_slots=parallel_slots,
            prompt_cache=prompt_cache,
            mmap=mmap,
            request_timeout_seconds=request_timeout,
            startup_timeout_seconds=startup_timeout,
            sample_interval_seconds=sample_interval,
            overwrite=overwrite,
        )
        result = run_baseline(config)
    except (ValidationError, AArchTuneError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        _render(result)
    if result.exit_code:
        raise typer.Exit(code=result.exit_code)
