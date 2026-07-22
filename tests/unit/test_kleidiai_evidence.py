from collections.abc import Callable

import pytest

from aarchtune.models import KleidiAIStatus
from aarchtune.runtime.capabilities import ServerCapabilities, analyze_kleidiai_evidence
from aarchtune.runtime.config import LlamaServerConfig
from aarchtune.runtime.process import LlamaServerProcess


def test_positive_kleidiai_evidence() -> None:
    result = analyze_kleidiai_evidence("backend: KleidiAI enabled")

    assert result.status is KleidiAIStatus.VERIFIED
    assert result.evidence == ["backend: KleidiAI enabled"]


def test_affirmative_negative_kleidiai_evidence() -> None:
    result = analyze_kleidiai_evidence("KleidiAI: disabled by build configuration")

    assert result.status is KleidiAIStatus.NOT_DETECTED
    assert result.evidence


def test_ambiguous_or_absent_evidence_remains_unknown() -> None:
    ambiguous = analyze_kleidiai_evidence("KleidiAI build setting was inspected")
    absent = analyze_kleidiai_evidence("generic CPU backend")

    assert ambiguous.status is KleidiAIStatus.UNKNOWN
    assert absent.status is KleidiAIStatus.UNKNOWN
    assert ambiguous.evidence == []


def test_kleidiai_evidence_redacts_inline_secrets() -> None:
    result = analyze_kleidiai_evidence("backend: KleidiAI enabled API_TOKEN=very-secret")

    assert result.status is KleidiAIStatus.VERIFIED
    assert "very-secret" not in result.evidence[0]
    assert "<redacted>" in result.evidence[0]


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("kleidiai-positive", KleidiAIStatus.VERIFIED),
        ("kleidiai-negative", KleidiAIStatus.NOT_DETECTED),
        ("kleidiai-ambiguous", KleidiAIStatus.UNKNOWN),
    ],
)
def test_startup_log_evidence_classification(
    config_factory: Callable[..., LlamaServerConfig],
    server_capabilities: ServerCapabilities,
    scenario: str,
    expected: KleidiAIStatus,
) -> None:
    config = config_factory(extra_environment={"FAKE_LLAMA_SCENARIO": scenario})
    server = LlamaServerProcess(config, server_capabilities)

    with server:
        server.wait_until_ready()

    assert server.kleidiai_evidence().status is expected
