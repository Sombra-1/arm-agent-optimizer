"""Expected baseline configuration, artifact, and execution failures."""

from aarchtune.errors import AArchTuneError


class BaselineError(AArchTuneError):
    """Base class for baseline failures safe to show without a traceback."""


class BaselineInputError(BaselineError):
    """A path or requested fixed configuration is invalid."""


class BaselineArtifactError(BaselineError):
    """Run artifacts could not be persisted safely."""


class BaselineRuntimeError(BaselineError):
    """The fixed baseline could not complete due to runtime infrastructure."""
