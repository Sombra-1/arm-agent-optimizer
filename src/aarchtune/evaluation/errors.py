"""Evaluation input, execution, policy, and artifact errors."""

from aarchtune.errors import AArchTuneError


class EvaluationError(AArchTuneError):
    """Base evaluation failure."""


class EvaluationInputError(EvaluationError):
    """Invalid screening evidence, configuration, or provenance."""


class QualityPolicyError(EvaluationError):
    """Invalid quality policy."""


class EvaluationArtifactError(EvaluationError):
    """Evaluation evidence could not be persisted or validated."""
