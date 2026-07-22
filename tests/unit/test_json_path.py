from __future__ import annotations

import pytest

from aarchtune.workload.errors import JsonPathError
from aarchtune.workload.json_path import parse_json_path, resolve_json_path


@pytest.mark.parametrize(
    ("path", "tokens"),
    [
        ("$", ()),
        ("$.field", ("field",)),
        ("$.nested.field", ("nested", "field")),
        ("$[0]", (0,)),
        ("$.items[0]", ("items", 0)),
        ("$.items[0].name", ("items", 0, "name")),
    ],
)
def test_parse_supported_paths(path: str, tokens: tuple[str | int, ...]) -> None:
    assert parse_json_path(path) == tokens


def test_resolve_root_nested_and_mixed_paths() -> None:
    document = {"items": [{"name": "worker"}], "null_value": None}

    assert resolve_json_path(document, "$").value == document
    assert resolve_json_path(document, "$.items[0].name").value == "worker"


def test_present_null_is_distinct_from_missing() -> None:
    document = {"present": None}

    present = resolve_json_path(document, "$.present")
    missing = resolve_json_path(document, "$.missing")

    assert present.found is True and present.value is None
    assert missing.found is False and "missing" in (missing.reason or "")


@pytest.mark.parametrize(
    ("document", "path", "reason"),
    [
        ({"items": []}, "$.items[0]", "out of range"),
        ({"items": {}}, "$.items[0]", "Expected an array"),
        ({"items": []}, "$.items.name", "Expected an object"),
        ([], "$[0]", "out of range"),
    ],
)
def test_resolution_failures_are_structured(document: object, path: str, reason: str) -> None:
    result = resolve_json_path(document, path)

    assert result.found is False
    assert reason in (result.reason or "")
    assert result.token_index is not None


@pytest.mark.parametrize(
    "path",
    [
        "",
        "field",
        "$.",
        "$.a..b",
        "$[-1]",
        "$[x]",
        "$[0",
        "$.field]",
        "$.*",
        "$..field",
        "$.items[?(@.x)]",
        "$.call()",
        "$['quoted']",
    ],
)
def test_malformed_or_prohibited_paths_are_rejected(path: str) -> None:
    with pytest.raises(JsonPathError):
        parse_json_path(path)
