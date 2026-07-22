from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aarchtune.hardware import detector
from aarchtune.models import (
    BinaryInspection,
    KleidiAIStatus,
    LlamaCppInspection,
)


def _missing_runtime() -> LlamaCppInspection:
    server = BinaryInspection(name="llama-server", found=False, error="Not found on PATH")
    bench = BinaryInspection(name="llama-bench", found=False, error="Not found on PATH")
    return LlamaCppInspection(
        server=server,
        bench=bench,
        kleidiai_status=KleidiAIStatus.UNKNOWN,
    )


def test_normalize_architecture() -> None:
    assert detector.normalize_architecture("ARM64") == "aarch64"
    assert detector.normalize_architecture("aarch64") == "aarch64"
    assert detector.normalize_architecture("x86_64") == "x86_64"
    assert detector.normalize_architecture("") == "unknown"


def test_parse_cpuinfo_collects_all_feature_lines_and_model() -> None:
    flags, model = detector.parse_cpuinfo(
        """
processor : 0
model name : Neoverse V2
Features : fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics asimddp
processor : 1
Features : fp asimd i8mm sve sme
"""
    )

    assert model == "Neoverse V2"
    assert {"asimd", "asimddp", "i8mm", "sve", "sme"} <= flags


def test_detect_hardware_does_not_label_x86_as_arm(monkeypatch: object) -> None:
    # pytest's MonkeyPatch is intentionally used structurally to keep test dependencies small.
    from pytest import MonkeyPatch

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setattr(detector.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(detector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(detector.platform, "release", lambda: "test-kernel")
    monkeypatch.setattr(detector, "_read_cpuinfo", lambda: ({"avx2", "sse4_2"}, "Test CPU"))
    monkeypatch.setattr(
        detector,
        "_read_lscpu",
        lambda: {"Model name": "Mock x86 CPU", "NUMA node(s)": "2"},
    )
    monkeypatch.setattr(detector.psutil, "cpu_count", lambda logical: 8 if logical else 4)
    monkeypatch.setattr(
        detector.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=16_000, available=8_000),
    )
    monkeypatch.setattr(detector, "discover_llama_cpp", _missing_runtime)

    report = detector.detect_hardware()

    assert report.architecture == "x86_64"
    assert report.is_arm64 is False
    assert report.numa_nodes == 2
    assert report.cpu_model == "Mock x86 CPU"
    assert not any(report.features.model_dump().values())


def test_model_file_inspection(tmp_path: Path) -> None:
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF")

    result = detector.inspect_model_file(model)

    assert result.readable is True
    assert result.size_bytes == 4
    assert result.path == model.resolve()


def test_missing_model_is_reported_without_raising(tmp_path: Path) -> None:
    result = detector.inspect_model_file(tmp_path / "missing.gguf")

    assert result.readable is False
    assert result.size_bytes is None
    assert result.error == "Not a regular file"
