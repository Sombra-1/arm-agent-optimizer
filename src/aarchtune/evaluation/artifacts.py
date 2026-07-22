"""Atomic evaluation manifest management and deterministic identifiers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from aarchtune.baseline.artifacts import atomic_write_json
from aarchtune.evaluation.models import EvaluationConfig, EvaluationManifest, EvaluationStatus


def generate_evaluation_id(now: datetime | None = None) -> str:
    observed = now or datetime.now(UTC)
    return f"{observed:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


class EvaluationManifestManager:
    def __init__(
        self, root: Path, evaluation_id: str, created_at: datetime, config: EvaluationConfig
    ) -> None:
        self.path = root / "evaluation-manifest.json"
        self.manifest = EvaluationManifest(
            evaluation_id=evaluation_id,
            created_at=created_at,
            updated_at=created_at,
            status=EvaluationStatus.INITIALIZING,
            stage=EvaluationStatus.INITIALIZING,
            output_directory=root,
            configuration=config,
        )
        self.write()

    def write(self) -> None:
        atomic_write_json(self.path, self.manifest)

    def update(self, **changes: object) -> None:
        changes["updated_at"] = datetime.now(UTC)
        self.manifest = self.manifest.model_copy(update=changes)
        self.write()
