"""Safe run-directory preparation and atomic UTF-8 artifact persistence."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aarchtune.baseline.errors import BaselineArtifactError, BaselineInputError

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _dangerous_directory(path: Path) -> bool:
    resolved = path.resolve()
    return resolved in {Path("/"), Path.home().resolve(), PROJECT_ROOT.resolve()}


def prepare_run_directory(path: Path, *, overwrite: bool) -> Path:
    resolved = path.expanduser().resolve()
    if _dangerous_directory(resolved):
        raise BaselineInputError(f"Refusing to use dangerous output directory: {resolved}")
    if resolved.exists() and not resolved.is_dir():
        raise BaselineInputError(f"Output path is not a directory: {resolved}")
    if resolved.exists() and any(resolved.iterdir()):
        if not overwrite:
            raise BaselineInputError(
                f"Output directory is not empty: {resolved}; pass --overwrite explicitly"
            )
        for child in resolved.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                raise BaselineArtifactError(f"Could not clear {child}: {exc}") from exc
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BaselineArtifactError(f"Could not create run directory {resolved}: {exc}") from exc
    return resolved


def _serialized(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def atomic_write_json(path: Path, value: BaseModel | dict[str, Any]) -> None:
    """Write, flush, fsync, and atomically replace an important JSON artifact."""

    payload = json.dumps(_serialized(value), indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise BaselineArtifactError(f"Could not atomically write {path}: {exc}") from exc


class JsonlArtifactWriter:
    """Append one bounded record at a time and flush before the next attempt."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._output = path.open("a", encoding="utf-8")

    def append(self, value: BaseModel | dict[str, Any]) -> None:
        try:
            self._output.write(json.dumps(_serialized(value), sort_keys=True) + "\n")
            self._output.flush()
        except OSError as exc:
            raise BaselineArtifactError(f"Could not append to {self.path}: {exc}") from exc

    def close(self) -> None:
        self._output.close()

    def __enter__(self) -> JsonlArtifactWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
