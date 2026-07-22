"""Expected screening failures safe to present without tracebacks."""

from aarchtune.errors import AArchTuneError


class ScreeningError(AArchTuneError):
    """Base error for low-level screening."""


class BenchDiscoveryError(ScreeningError):
    """A usable llama-bench executable could not be resolved."""


class BenchCapabilityError(ScreeningError):
    """The inspected executable lacks required machine-readable capabilities."""


class ScenarioError(ScreeningError):
    """A scenario definition is invalid or cannot be represented."""


class BenchCommandError(ScreeningError):
    """A benchmark signature cannot be mapped without losing settings."""


class BenchParseError(ScreeningError):
    """Machine-readable benchmark output is invalid or inconsistent."""


class ScreeningArtifactError(ScreeningError):
    """Screening artifacts are missing, invalid, or tampered."""
