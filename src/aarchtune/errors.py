"""Application-specific exceptions."""


class AArchTuneError(Exception):
    """Base class for expected AArchTune failures."""


class DetectionError(AArchTuneError):
    """Raised when required host information cannot be detected."""


class OutputError(AArchTuneError):
    """Raised when a requested artifact cannot be written safely."""
