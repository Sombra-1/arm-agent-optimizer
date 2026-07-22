"""Expected failures in measurement and process sampling."""

from aarchtune.errors import AArchTuneError


class BenchmarkError(AArchTuneError):
    """Base class for one fixed-configuration measurement failure."""


class ProcessSamplingError(BenchmarkError):
    """Process metrics could not be sampled or persisted safely."""
