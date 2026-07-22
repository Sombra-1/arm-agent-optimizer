"""Atomic lifecycle updates for baseline manifest.json."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.baseline.models import (
    ArtifactReference,
    BaselineManifest,
    RunStage,
    RunStatus,
)


class ManifestManager:
    def __init__(self, output_dir: Path, run_id: str, created_at: datetime) -> None:
        self.path = output_dir / "manifest.json"
        self.manifest = BaselineManifest(
            run_id=run_id,
            created_at=created_at,
            status=RunStatus.INITIALIZING,
            stage=RunStage.INITIALIZING,
            updated_at=created_at,
            output_directory=str(output_dir),
        )
        self.write()

    def write(self) -> None:
        atomic_write_json(self.path, self.manifest)

    def update(
        self,
        *,
        stage: RunStage | None = None,
        status: RunStatus | None = None,
        completed_attempt_count: int | None = None,
        server_stopped: bool | None = None,
        sampler_stopped: bool | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        changes: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if stage is not None:
            changes["stage"] = stage
        if status is not None:
            changes["status"] = status
        if completed_attempt_count is not None:
            changes["completed_attempt_count"] = completed_attempt_count
        if server_stopped is not None:
            changes["server_stopped"] = server_stopped
        if sampler_stopped is not None:
            changes["sampler_stopped"] = sampler_stopped
        if error_type is not None:
            changes["error_type"] = error_type
        if error_message is not None:
            changes["error_message"] = error_message
        self.manifest = self.manifest.model_copy(update=changes)
        self.write()

    def add_artifact(self, name: str, media_type: str, *, required: bool = True) -> None:
        artifacts = self.manifest.artifacts.copy()
        artifacts[name] = ArtifactReference(path=name, media_type=media_type, required=required)
        self.manifest = self.manifest.model_copy(
            update={"artifacts": artifacts, "updated_at": datetime.now(UTC)}
        )
        self.write()
