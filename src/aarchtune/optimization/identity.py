"""Canonical hashing and stable human-readable candidate identity."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aarchtune.optimization.models import ProfileRuntime, SearchPlan


def canonical_json(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def profile_hash(runtime: ProfileRuntime) -> str:
    return stable_hash(runtime)


def profile_id(label: str, runtime: ProfileRuntime, *, baseline: bool = False) -> str:
    if baseline:
        return "baseline"
    parts = [label]
    if runtime.threads is not None:
        parts.append(f"t{runtime.threads}")
    if runtime.threads_batch is not None:
        parts.append(f"tb{runtime.threads_batch}")
    if runtime.batch_size is not None:
        parts.append(f"b{runtime.batch_size}")
    if runtime.ubatch_size is not None:
        parts.append(f"u{runtime.ubatch_size}")
    if runtime.parallel_slots is not None:
        parts.append(f"p{runtime.parallel_slots}")
    if runtime.context_size is not None:
        parts.append(f"c{runtime.context_size}")
    parts.append("cache" if runtime.prompt_cache else "nocache")
    if not runtime.mmap:
        parts.append("nommap")
    if runtime.numa_mode != "disabled":
        parts.append(f"numa-{runtime.numa_mode}")
    return "-".join(parts)


def plan_hash(plan: SearchPlan) -> str:
    """Hash semantic plan content; creation time and the hash field are non-semantic."""

    payload = plan.model_dump(mode="json", exclude={"plan_hash", "created_at"})
    return stable_hash(payload)
