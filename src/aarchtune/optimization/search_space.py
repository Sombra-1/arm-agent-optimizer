"""Strict bounded YAML search-space loading with exact-source provenance."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aarchtune.optimization.errors import SearchSpaceError
from aarchtune.optimization.models import SearchSpaceConfig, SearchSpaceSource

DEFAULT_SEARCH_SPACE = Path(__file__).resolve().parents[3] / "configs/default-search-space.yaml"
MAX_SEARCH_SPACE_BYTES = 256 * 1024


def load_search_space(path: Path | None = None) -> SearchSpaceSource:
    source_path = (path or DEFAULT_SEARCH_SPACE).expanduser().resolve()
    try:
        size = source_path.stat().st_size
    except OSError as exc:
        raise SearchSpaceError(f"Cannot stat search-space file {source_path}: {exc}") from exc
    if not source_path.is_file():
        raise SearchSpaceError(f"Search-space path is not a file: {source_path}")
    if size > MAX_SEARCH_SPACE_BYTES:
        raise SearchSpaceError(
            f"Search-space file is {size} bytes; maximum is {MAX_SEARCH_SPACE_BYTES}"
        )
    try:
        content = source_path.read_bytes()
        text = content.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise SearchSpaceError(f"Cannot read search-space file {source_path}: {exc}") from exc
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SearchSpaceError(f"Invalid search-space YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SearchSpaceError("Search-space YAML root must be an object")
    try:
        configuration = SearchSpaceConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first["loc"])
        raise SearchSpaceError(
            f"Invalid search-space configuration at {location}: {first['msg']}"
        ) from exc
    return SearchSpaceSource(
        path=source_path,
        sha256=hashlib.sha256(content).hexdigest(),
        configuration=configuration,
    )
