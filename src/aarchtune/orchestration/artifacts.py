"""Atomic optimize manifest updates and stage references."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.orchestration.models import OptimizeManifest


class OptimizeManifestManager:
    def __init__(self, path: Path, manifest: OptimizeManifest) -> None:
        self.path = path
        self.manifest = manifest
        self.write()

    @classmethod
    def load(cls, path: Path) -> OptimizeManifestManager:
        return cls.__new_from_existing(
            path,
            OptimizeManifest.model_validate_json(path.read_text(encoding="utf-8")),
        )

    @classmethod
    def __new_from_existing(cls, path: Path, manifest: OptimizeManifest) -> OptimizeManifestManager:
        instance = object.__new__(cls)
        instance.path = path
        instance.manifest = manifest
        return instance

    def write(self) -> None:
        atomic_write_json(self.path, self.manifest)

    def update(self, **changes: object) -> None:
        changes["updated_at"] = datetime.now(UTC)
        self.manifest = self.manifest.model_copy(update=changes)
        self.write()
