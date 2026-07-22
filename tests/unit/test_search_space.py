from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aarchtune.optimization.errors import SearchSpaceError
from aarchtune.optimization.search_space import load_search_space


def _raw_default() -> dict[str, object]:
    path = Path(__file__).resolve().parents[2] / "configs/default-search-space.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _write(tmp_path: Path, raw: dict[str, object]) -> Path:
    path = tmp_path / "space.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
    return path


def test_default_search_space_is_valid_and_hash_is_stable() -> None:
    first = load_search_space()
    second = load_search_space()
    assert first.sha256 == second.sha256
    assert first.configuration.limits.maximum_profiles == 24
    assert first.configuration.schema_version == "1.0"


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    raw = _raw_default()
    raw["unexpected"] = True
    with pytest.raises(SearchSpaceError, match="unexpected"):
        load_search_space(_write(tmp_path, raw))


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0, 10), (10, 0), (20, 10), (1, 65), (-1, 10)],
)
def test_invalid_profile_limits_are_rejected(tmp_path: Path, minimum: int, maximum: int) -> None:
    raw = _raw_default()
    raw["limits"] = {"minimum_profiles": minimum, "maximum_profiles": maximum}
    with pytest.raises(SearchSpaceError):
        load_search_space(_write(tmp_path, raw))


@pytest.mark.parametrize("field", ["batch_sizes", "ubatch_sizes", "parallel_slots"])
def test_zero_and_negative_values_are_rejected(tmp_path: Path, field: str) -> None:
    raw = _raw_default()
    raw[field] = [0, -1]
    with pytest.raises(SearchSpaceError):
        load_search_space(_write(tmp_path, raw))


def test_duplicate_values_are_rejected(tmp_path: Path) -> None:
    raw = _raw_default()
    raw["batch_sizes"] = [128, 128]
    with pytest.raises(SearchSpaceError, match="unique"):
        load_search_space(_write(tmp_path, raw))


def test_no_valid_batch_microbatch_relationship_is_rejected(tmp_path: Path) -> None:
    raw = _raw_default()
    raw["batch_sizes"] = [64]
    raw["ubatch_sizes"] = [128]
    with pytest.raises(SearchSpaceError, match="ubatch_size"):
        load_search_space(_write(tmp_path, raw))
