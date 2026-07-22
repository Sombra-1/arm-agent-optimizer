from __future__ import annotations

from pathlib import Path

import pytest

from aarchtune.optimization.models import SearchPlan
from aarchtune.screening.command import build_bench_command
from aarchtune.screening.errors import BenchCommandError
from aarchtune.screening.models import LlamaBenchCapabilities, ScreeningScenario
from aarchtune.screening.signatures import build_signatures


def _plan(path: Path) -> SearchPlan:
    return SearchPlan.model_validate_json((path / "search-plan.json").read_text())


def test_equivalent_server_only_candidates_share_signature_without_identity_loss(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = _plan(screen_plan_dir)
    signatures, memberships = build_signatures(plan.candidates, bench_capabilities)
    assert len(signatures) < len(plan.candidates)
    assert len(memberships) == len(plan.candidates)
    assert {item.candidate_id for item in memberships} == {
        candidate.id for candidate in plan.candidates
    }
    grouped: dict[str, list[str]] = {}
    for item in memberships:
        grouped.setdefault(item.bench_signature_id, []).append(item.candidate_id)
    assert any(len(values) > 1 for values in grouped.values())
    shared = next(values for values in grouped.values() if len(values) > 1)
    fields = {
        mapping.field
        for membership in memberships
        if membership.candidate_id in shared
        for mapping in membership.unscreenable_fields
    }
    assert {"parallel_slots", "prompt_cache"} <= fields


def test_signature_ids_hashes_and_thread_differences_are_stable(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = _plan(screen_plan_dir)
    first, first_memberships = build_signatures(plan.candidates, bench_capabilities)
    second, second_memberships = build_signatures(plan.candidates, bench_capabilities)
    assert first == second
    assert first_memberships == second_memberships
    assert len({item.signature_hash for item in first}) == len(first)
    thread_settings = {item.settings.threads for item in first}
    assert len(thread_settings) > 1
    baseline_membership = next(
        item for item in first_memberships if item.candidate_id == "baseline"
    )
    assert baseline_membership.bench_signature_hash


def test_all_candidate_fields_have_explicit_mapping(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = _plan(screen_plan_dir)
    _, memberships = build_signatures(plan.candidates, bench_capabilities)
    expected = set(type(plan.candidates[0].runtime).model_fields) - {"schema_version"}
    for membership in memberships:
        mapped = {
            item.field for item in [*membership.screenable_fields, *membership.unscreenable_fields]
        }
        assert mapped == expected
        assert all(item.reason for item in membership.unscreenable_fields)


def test_unsupported_required_mapping_marks_signature_incompatible(
    screen_plan_dir: Path, bench_capabilities: LlamaBenchCapabilities
) -> None:
    plan = _plan(screen_plan_dir)
    limited = bench_capabilities.model_copy(
        update={
            "mappings": {
                **bench_capabilities.mappings,
                "threads": bench_capabilities.mappings["threads"].model_copy(
                    update={"supported": False, "selected_flag": None}
                ),
            }
        }
    )
    signatures, _ = build_signatures(plan.candidates, limited)
    assert any(not item.compatible for item in signatures)
    assert any(
        "threads" in reason for item in signatures for reason in item.incompatibility_reasons
    )


def test_exact_argument_list_and_repetition(
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    plan = _plan(screen_plan_dir)
    signatures, _ = build_signatures(plan.candidates, bench_capabilities)
    signature = next(item for item in signatures if item.settings.threads is not None)
    scenario = ScreeningScenario(id="mixed", prompt_tokens=64, generation_tokens=16)
    command = build_bench_command(bench_capabilities, fake_model, signature, scenario, repetition=3)
    assert command.arguments[0] == str(bench_capabilities.binary_path)
    assert command.arguments[1:3] == ["-m", str(fake_model)]
    assert "-t" in command.arguments
    assert command.arguments[-2:] == ["-o", "jsonl"]
    assert command.repetition == 3
    assert command.mapped_flags["output_format"] == "-o"


def test_paths_with_spaces_remain_one_argument(
    tmp_path: Path,
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    model = tmp_path / "model with spaces.gguf"
    model.write_bytes(fake_model.read_bytes())
    plan = _plan(screen_plan_dir)
    signature = build_signatures(plan.candidates, bench_capabilities)[0][0]
    command = build_bench_command(
        bench_capabilities,
        model,
        signature,
        ScreeningScenario(id="decode", prompt_tokens=0, generation_tokens=8),
        1,
    )
    assert str(model) in command.arguments
    assert all("'" not in item for item in command.arguments)


def test_command_rejects_incompatible_signature(
    screen_plan_dir: Path,
    bench_capabilities: LlamaBenchCapabilities,
    fake_model: Path,
) -> None:
    plan = _plan(screen_plan_dir)
    signature = build_signatures(plan.candidates, bench_capabilities)[0][0].model_copy(
        update={"compatible": False, "incompatibility_reasons": ["unsupported setting"]}
    )
    with pytest.raises(BenchCommandError, match="unsupported setting"):
        build_bench_command(
            bench_capabilities,
            fake_model,
            signature,
            ScreeningScenario(id="decode", prompt_tokens=0, generation_tokens=8),
            1,
        )
