from __future__ import annotations

import json
from pathlib import Path

import pytest

from aarchtune.evaluation.errors import EvaluationInputError
from aarchtune.evaluation.execution_plan import build_evaluation_plan
from aarchtune.evaluation.models import EvaluationConfig
from aarchtune.evaluation.provenance import load_evaluation_input
from aarchtune.evaluation.quality_policy import load_quality_policy


def _config(tmp_path: Path, screening: Path, *, allow: bool = True) -> EvaluationConfig:
    return EvaluationConfig(
        screening_dir=screening,
        output_dir=tmp_path / "evaluation",
        repetitions=2,
        warmup_requests=1,
        allow_synthetic=allow,
    )


def test_valid_screening_goal_order_scores_and_plan_determinism(
    tmp_path: Path, evaluation_screening_dir: Path
) -> None:
    config = _config(tmp_path, evaluation_screening_dir)
    source = load_evaluation_input(config)
    assert source.search_plan.goal.value == "balanced"
    assert source.baseline_profile.baseline
    assert [item.order for item in source.candidates] == list(range(1, len(source.candidates) + 1))
    assert any(item.screening_score is not None for item in source.candidates)
    policy = load_quality_policy(None)
    first = build_evaluation_plan("fixed", config, source, policy)
    second = build_evaluation_plan("fixed", config, source, policy)
    assert first == second
    assert first.execution_order[0] == "baseline-start"
    assert first.execution_order[-1] == "baseline-end"
    assert len(first.execution_order) == len(source.candidates) + 2
    assert first.expected_attempt_count == len(first.task_order) * 2 * len(first.execution_order)


def test_synthetic_requires_explicit_opt_in(tmp_path: Path, evaluation_screening_dir: Path) -> None:
    with pytest.raises(EvaluationInputError, match="allow-synthetic"):
        load_evaluation_input(_config(tmp_path, evaluation_screening_dir, allow=False))


def test_failed_screening_and_tampering_are_rejected(
    tmp_path: Path, evaluation_screening_dir: Path
) -> None:
    manifest_path = evaluation_screening_dir / "screening-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(EvaluationInputError, match="not evaluable"):
        load_evaluation_input(_config(tmp_path, evaluation_screening_dir))


def test_missing_advanced_profile_is_rejected(
    tmp_path: Path, evaluation_screening_dir: Path
) -> None:
    profile = next((evaluation_screening_dir / "advanced-profiles").glob("*.yaml"))
    profile.unlink()
    with pytest.raises(EvaluationInputError, match="integrity"):
        load_evaluation_input(_config(tmp_path, evaluation_screening_dir))


def test_available_memory_change_is_warning(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aarchtune.evaluation import provenance

    original = provenance.load_explicit_input

    def changed(*args: object, **kwargs: object) -> object:
        value = original(*args, **kwargs)
        hardware = value.hardware.model_copy(
            update={"available_memory_bytes": (value.hardware.available_memory_bytes or 0) + 1}
        )
        return value.model_copy(update={"hardware": hardware})

    monkeypatch.setattr(provenance, "load_explicit_input", changed)
    source = load_evaluation_input(_config(tmp_path, evaluation_screening_dir))
    assert any("Available memory" in warning for warning in source.warnings)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("architecture", "Architecture"),
        ("model", "Model hash"),
        ("workload", "Workload hash"),
    ],
)
def test_incompatible_provenance_is_rejected(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    message: str,
) -> None:
    from aarchtune.evaluation import provenance

    original = provenance.load_explicit_input

    def changed(*args: object, **kwargs: object) -> object:
        value = original(*args, **kwargs)
        if field == "architecture":
            hardware = value.hardware.model_copy(update={"architecture": "different"})
            return value.model_copy(update={"hardware": hardware})
        fingerprint = getattr(value, field).model_copy(update={"sha256": "0" * 64})
        return value.model_copy(update={field: fingerprint})

    monkeypatch.setattr(provenance, "load_explicit_input", changed)
    with pytest.raises(EvaluationInputError, match=message):
        load_evaluation_input(_config(tmp_path, evaluation_screening_dir))


def test_runtime_change_requires_and_records_override(
    tmp_path: Path,
    evaluation_screening_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aarchtune.evaluation import provenance

    original = provenance.load_explicit_input

    def changed(*args: object, **kwargs: object) -> object:
        value = original(*args, **kwargs)
        runtime = value.runtime.model_copy(update={"fingerprint_hash": "0" * 64})
        return value.model_copy(update={"runtime": runtime})

    monkeypatch.setattr(provenance, "load_explicit_input", changed)
    with pytest.raises(EvaluationInputError, match="Runtime"):
        load_evaluation_input(_config(tmp_path, evaluation_screening_dir))
    allowed = _config(tmp_path, evaluation_screening_dir).model_copy(
        update={"allow_runtime_change": True}
    )
    source = load_evaluation_input(allowed)
    assert source.runtime_override
    assert any("override recorded" in warning for warning in source.warnings)
