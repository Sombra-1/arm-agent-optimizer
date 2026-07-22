"""Typed application models shared by CLI and runtime components."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AppModel(BaseModel):
    """Strict base model for persisted AArchTune data."""

    model_config = ConfigDict(extra="forbid")


class KleidiAIStatus(StrEnum):
    """Confidence state for KleidiAI backend detection."""

    VERIFIED = "verified"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class CPUFeatures(AppModel):
    """Arm CPU features relevant to llama.cpp CPU inference."""

    asimd: bool = False
    dotprod: bool = False
    i8mm: bool = False
    sve: bool = False
    sme: bool = False


class BinaryInspection(AppModel):
    """Discovery and version result for one local executable."""

    name: str
    path: Path | None = None
    found: bool
    version: str | None = None
    error: str | None = None


class LlamaCppInspection(AppModel):
    """Installed llama.cpp tools and conservative backend evidence."""

    server_path: Path | None = None
    bench_path: Path | None = None
    version: str | None = None
    server: BinaryInspection
    bench: BinaryInspection
    kleidiai_status: KleidiAIStatus = KleidiAIStatus.UNKNOWN
    kleidiai_evidence: list[str] = Field(default_factory=list)


class ModelFileInspection(AppModel):
    """Readability result for an optional model passed to doctor."""

    path: Path
    readable: bool
    size_bytes: int | None = None
    error: str | None = None


class HardwareReport(AppModel):
    """Serializable host and llama.cpp environment report."""

    architecture: str
    is_arm64: bool
    operating_system: str
    kernel: str
    cpu_model: str | None = None
    logical_cores: int | None = None
    physical_cores: int | None = None
    memory_bytes: int | None = None
    memory_available_bytes: int | None = None
    numa_nodes: int | None = None
    features: CPUFeatures
    cpu_flags: list[str] = Field(default_factory=list)
    lscpu: dict[str, str] | None = None
    llama_cpp: LlamaCppInspection
    model: ModelFileInspection | None = None
