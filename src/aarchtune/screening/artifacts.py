"""Atomic screening manifests and deterministic artifact persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.screening.models import ScreeningManifest, ScreeningStatus


class ScreeningManifestManager:
    def __init__(self, root: Path, screening_id: str, config: Any) -> None:
        now = datetime.now(UTC)
        self.path = root / "screening-manifest.json"
        self.manifest = ScreeningManifest(
            screening_id=screening_id,
            created_at=now,
            updated_at=now,
            status=ScreeningStatus.INITIALIZING,
            stage=ScreeningStatus.INITIALIZING,
            output_directory=root,
            screening_configuration=config,
        )
        self.write()

    def write(self) -> None:
        atomic_write_json(self.path, self.manifest)

    def update(self, **changes: object) -> None:
        changes["updated_at"] = datetime.now(UTC)
        self.manifest = self.manifest.model_copy(update=changes)
        self.write()


def screening_id(plan_hash: str) -> str:
    now = datetime.now(UTC)
    return f"{now:%Y%m%dT%H%M%SZ}-{plan_hash[:6]}"
