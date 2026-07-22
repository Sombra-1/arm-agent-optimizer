"""Strict quality-policy loading with an exact-source hash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from aarchtune.evaluation.errors import QualityPolicyError
from aarchtune.evaluation.models import QualityPolicy, QualityPolicySource

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "configs/default-quality-policy.yaml"


def load_quality_policy(path: Path | None) -> QualityPolicySource:
    selected = (path or DEFAULT_POLICY_PATH).expanduser().resolve()
    try:
        raw_bytes = selected.read_bytes()
        raw = yaml.safe_load(raw_bytes.decode("utf-8"))
        policy = QualityPolicy.model_validate_json(json.dumps(raw))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise QualityPolicyError(f"Invalid quality policy {selected}: {exc}") from exc
    return QualityPolicySource(
        path=selected,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        policy=policy,
    )
