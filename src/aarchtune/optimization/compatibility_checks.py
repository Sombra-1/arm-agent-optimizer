"""Per-candidate mapping checks against proven llama-server option tokens."""

from __future__ import annotations

from typing import Any

from aarchtune.optimization.models import (
    CandidateCompatibility,
    CandidateCompatibilityClass,
    CompatibilityDetail,
    ProfileRuntime,
)
from aarchtune.runtime.capabilities import ServerCapabilities

_ALIASES: dict[str, tuple[str, ...]] = {
    "threads": ("--threads",),
    "threads_batch": ("--threads-batch", "--threads_batch"),
    "batch_size": ("--batch-size", "--batch_size"),
    "ubatch_size": ("--ubatch-size", "--ubatch_size"),
    "context_size": ("--ctx-size", "--context-size", "--n-ctx"),
    "parallel_slots": ("--parallel",),
    "prompt_cache": ("--cache-prompt",),
    "numa_mode": ("--numa",),
}


def _supported(capabilities: ServerCapabilities, flags: tuple[str, ...]) -> bool:
    return any(flag in capabilities.raw_option_tokens for flag in flags)


def check_candidate_compatibility(
    runtime: ProfileRuntime, capabilities: ServerCapabilities
) -> CandidateCompatibility:
    details: list[CompatibilityDetail] = []
    unsupported: list[str] = []
    warnings: list[str] = []

    def require(field: str, value: Any, flags: tuple[str, ...]) -> None:
        supported = _supported(capabilities, flags)
        required = " or ".join(flags)
        details.append(
            CompatibilityDetail(
                field=field,
                requested_value=value,
                required_flag=required,
                supported=supported,
                reason=(
                    "Requested setting maps to a proven runtime flag"
                    if supported
                    else "Requested non-default setting cannot be represented by this binary"
                ),
            )
        )
        if not supported:
            unsupported.extend(flags)

    for field in (
        "threads",
        "threads_batch",
        "batch_size",
        "ubatch_size",
        "context_size",
        "parallel_slots",
    ):
        value = getattr(runtime, field)
        if value is not None:
            require(field, value, _ALIASES[field])
    if runtime.prompt_cache:
        require("prompt_cache", True, _ALIASES["prompt_cache"])
    if runtime.mmap:
        mmap_supported = _supported(capabilities, ("--mmap", "--no-mmap"))
        details.append(
            CompatibilityDetail(
                field="mmap",
                requested_value=True,
                required_flag="--mmap or proven default via --no-mmap",
                supported=mmap_supported,
                reason=(
                    "Enabled mmap is representable explicitly or as a proven runtime default"
                    if mmap_supported
                    else "Runtime help exposes neither mmap polarity"
                ),
            )
        )
        if not mmap_supported:
            unsupported.extend(("--mmap", "--no-mmap"))
        elif "--mmap" not in capabilities.raw_option_tokens:
            warnings.append("mmap=true relies on the default proven by --no-mmap availability")
    else:
        require("mmap", False, ("--no-mmap",))
    if runtime.numa_mode != "disabled":
        require("numa_mode", runtime.numa_mode, _ALIASES["numa_mode"])
    if runtime.cpu_affinity_policy != "none":
        details.append(
            CompatibilityDetail(
                field="cpu_affinity_policy",
                requested_value=runtime.cpu_affinity_policy,
                required_flag=None,
                supported=False,
                reason="No safe runtime affinity mapping exists in v1; taskset is not used",
            )
        )
        unsupported.append("safe affinity mapping")
    if unsupported:
        classification = CandidateCompatibilityClass.INCOMPATIBLE
    elif warnings:
        classification = CandidateCompatibilityClass.COMPATIBLE_WITH_WARNINGS
    else:
        classification = CandidateCompatibilityClass.COMPATIBLE
    return CandidateCompatibility(
        classification=classification,
        compatible=not unsupported,
        unsupported_flags=sorted(set(unsupported)),
        warnings=warnings,
        details=details,
    )
