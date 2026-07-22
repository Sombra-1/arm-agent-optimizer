"""Finalization errors."""

from aarchtune.errors import AArchTuneError


class FinalizationError(AArchTuneError):
    """Base finalization error."""


class FinalizationInputError(FinalizationError):
    """Evaluation input is not eligible for finalization."""


class BundleValidationError(FinalizationError):
    """Generated or supplied bundle failed integrity validation."""
