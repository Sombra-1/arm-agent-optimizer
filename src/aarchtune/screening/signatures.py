"""Map complete planned candidates into deduplicated low-level signatures."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from aarchtune.optimization.identity import stable_hash
from aarchtune.optimization.models import CandidateProfile
from aarchtune.screening.models import (
    BenchSignature,
    BenchSignatureSettings,
    CandidateFieldMapping,
    LlamaBenchCapabilities,
    SignatureMembership,
)

_SCREENABLE = {
    "threads": "threads",
    "threads_batch": "threads_batch",
    "batch_size": "batch_size",
    "ubatch_size": "ubatch_size",
    "mmap": "mmap",
    "numa_mode": "numa_mode",
}
_UNSCREENABLE_REASONS = {
    "backend_label": "Backend label is provenance; this run uses one inspected bench binary",
    "binary_path": "Server binary path is not a llama-bench option",
    "context_size": "Context capacity is not used as a low-level scenario dimension",
    "parallel_slots": "llama-bench does not expose server parallel-slot behavior",
    "prompt_cache": "llama-bench does not expose server prompt-cache behavior",
    "cpu_affinity_policy": "No safe direct llama-bench affinity mapping is enabled",
}


def _json_value(value: Any) -> Any:
    return str(value) if hasattr(value, "__fspath__") else value


def _signature_id(settings: BenchSignatureSettings) -> str:
    parts = ["bench"]
    for label, value in (
        ("t", settings.threads),
        ("tb", settings.threads_batch),
        ("b", settings.batch_size),
        ("u", settings.ubatch_size),
    ):
        if value is not None:
            parts.append(f"{label}{value}")
    if settings.mmap is not None:
        parts.append("mmap" if settings.mmap else "nommap")
    if settings.numa_mode not in {None, "disabled"}:
        parts.append(f"numa-{settings.numa_mode}")
    return "-".join(parts)


def build_signatures(
    candidates: list[CandidateProfile], capabilities: LlamaBenchCapabilities
) -> tuple[list[BenchSignature], list[SignatureMembership]]:
    signatures_by_hash: dict[str, BenchSignature] = {}
    memberships: list[SignatureMembership] = []
    used_ids: dict[str, str] = {}
    for candidate in candidates:
        screenable: list[CandidateFieldMapping] = []
        unscreenable: list[CandidateFieldMapping] = []
        settings_data: dict[str, Any] = {}
        incompatibilities: list[str] = []
        runtime_data = candidate.runtime.model_dump(mode="python", exclude={"schema_version"})
        for field, value in runtime_data.items():
            if field in _SCREENABLE:
                capability_name = _SCREENABLE[field]
                capability = capabilities.mappings[capability_name]
                supported = (
                    capability.represents_boolean(value)
                    if field == "mmap" and isinstance(value, bool)
                    else capability.supported
                )
                reason = (
                    "Mapped through a value form observed in llama-bench help"
                    if supported
                    else (
                        f"Planned Boolean value cannot be represented by "
                        f"llama-bench form {capability.boolean_form.value}"
                        if field == "mmap" and capability.boolean_form is not None
                        else "Planned value cannot be represented by this llama-bench"
                    )
                )
                mapping = CandidateFieldMapping(
                    field=field,
                    screenable=supported,
                    value=_json_value(value),
                    reason=reason,
                )
                if supported:
                    screenable.append(mapping)
                    settings_data[field] = value
                else:
                    unscreenable.append(mapping)
                    if value not in {None, "disabled"}:
                        incompatibilities.append(f"{field}: {reason}")
            else:
                unscreenable.append(
                    CandidateFieldMapping(
                        field=field,
                        screenable=False,
                        value=_json_value(value),
                        reason=_UNSCREENABLE_REASONS[field],
                    )
                )
        settings = BenchSignatureSettings(**settings_data)
        digest = stable_hash(settings)
        signature_id = _signature_id(settings)
        prior_hash = used_ids.get(signature_id)
        if prior_hash is not None and prior_hash != digest:
            signature_id = f"{signature_id}-{digest[:8]}"
        used_ids[signature_id] = digest
        if digest not in signatures_by_hash:
            signatures_by_hash[digest] = BenchSignature(
                id=signature_id,
                signature_hash=digest,
                settings=settings,
                compatible=not incompatibilities,
                incompatibility_reasons=incompatibilities,
            )
        memberships.append(
            SignatureMembership(
                candidate_id=candidate.id,
                candidate_hash=candidate.profile_hash,
                bench_signature_id=signatures_by_hash[digest].id,
                bench_signature_hash=digest,
                screenable_fields=screenable,
                unscreenable_fields=unscreenable,
            )
        )
    members_by_hash: dict[str, list[SignatureMembership]] = defaultdict(list)
    for membership in memberships:
        members_by_hash[membership.bench_signature_hash].append(membership)
    del members_by_hash
    return sorted(signatures_by_hash.values(), key=lambda item: item.id), memberships
