"""Strict parsers for llama-bench machine-readable output only."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from aarchtune.screening.errors import BenchParseError
from aarchtune.screening.models import OutputFormat, RawBenchRecord

MAX_RAW_OUTPUT_BYTES = 64 * 1024 * 1024


def _object(value: Any, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise BenchParseError(f"{label} is not a JSON object")
    return cast(dict[str, JsonValue], value)


def parse_bench_output(
    path: Path, output_format: OutputFormat, invocation_id: str
) -> list[RawBenchRecord]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BenchParseError(f"Cannot stat raw benchmark output: {exc}") from exc
    if size > MAX_RAW_OUTPUT_BYTES:
        raise BenchParseError(f"Raw benchmark output exceeds {MAX_RAW_OUTPUT_BYTES} bytes")
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise BenchParseError(f"Cannot read UTF-8 benchmark output: {exc}") from exc
    rows: list[dict[str, JsonValue]] = []
    try:
        if output_format is OutputFormat.JSONL:
            for line_number, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    rows.append(_object(json.loads(line), f"JSONL line {line_number}"))
        elif output_format is OutputFormat.JSON:
            value: Any = json.loads(text)
            if isinstance(value, dict) and isinstance(value.get("results"), list):
                rows = [
                    _object(item, f"JSON results row {index}")
                    for index, item in enumerate(value["results"])
                ]
            elif isinstance(value, list):
                rows = [_object(item, f"JSON row {index}") for index, item in enumerate(value)]
            else:
                rows = [_object(value, "JSON root")]
        else:
            reader = csv.DictReader(text.splitlines())
            if reader.fieldnames is None:
                raise BenchParseError("CSV output has no header")
            rows = [cast(dict[str, JsonValue], dict(row)) for row in reader]
    except (json.JSONDecodeError, csv.Error) as exc:
        raise BenchParseError(f"Invalid {output_format.value} benchmark output: {exc}") from exc
    if not rows:
        raise BenchParseError("Machine-readable benchmark output contained no rows")
    return [
        RawBenchRecord(invocation_id=invocation_id, row_index=index, raw=row)
        for index, row in enumerate(rows)
    ]
