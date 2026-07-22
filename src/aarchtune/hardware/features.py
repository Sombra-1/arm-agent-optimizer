"""CPU feature normalization helpers."""

from __future__ import annotations

from collections.abc import Iterable

from aarchtune.models import CPUFeatures


def normalize_cpu_flags(flags: Iterable[str]) -> set[str]:
    """Return lowercase CPU flags with whitespace and duplicates removed."""

    return {flag.strip().lower() for flag in flags if flag.strip()}


def arm_features_from_flags(flags: Iterable[str]) -> CPUFeatures:
    """Map Linux feature aliases to the stable AArchTune feature model."""

    normalized = normalize_cpu_flags(flags)
    return CPUFeatures(
        asimd=bool({"asimd", "neon"} & normalized),
        dotprod=bool({"asimddp", "dotprod"} & normalized),
        i8mm="i8mm" in normalized,
        sve=bool({"sve", "sve2"} & normalized),
        sme=any(flag == "sme" or flag.startswith("sme_") for flag in normalized),
    )
