"""Expected search-planning and validation failures."""

from aarchtune.errors import AArchTuneError


class PlanningError(AArchTuneError):
    """Base error for deterministic plan creation."""


class SearchSpaceError(PlanningError):
    """The strict YAML search-space definition is invalid."""


class BaselineReferenceError(PlanningError):
    """A baseline is incomplete, unsupported, or unsafe for planning."""


class ProvenanceMismatchError(PlanningError):
    """Current inputs are incompatible with recorded baseline provenance."""


class PlanArtifactError(PlanningError):
    """Plan artifacts could not be persisted or validated."""
