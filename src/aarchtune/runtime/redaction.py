"""Small, centralized redaction helpers for runtime diagnostics."""

from __future__ import annotations

import re
from collections.abc import Mapping

_SECRET_NAME = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|credential|authorization)", re.IGNORECASE
)
_INLINE_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_-]*)(\s*[=:]\s*)([^\s,;]+)", re.IGNORECASE)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def is_secret_name(name: str) -> bool:
    return _SECRET_NAME.search(name) is not None


def redact_text(text: str) -> str:
    """Redact common inline credential forms without claiming exhaustive secret detection."""

    def replace_assignment(match: re.Match[str]) -> str:
        if is_secret_name(match.group(1)):
            return f"{match.group(1)}{match.group(2)}<redacted>"
        return match.group(0)

    redacted = _INLINE_ASSIGNMENT.sub(replace_assignment, text)
    return _BEARER.sub("Bearer <redacted>", redacted)


def redact_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: "<redacted>" if is_secret_name(name) else redact_text(value)
        for name, value in environment.items()
    }
