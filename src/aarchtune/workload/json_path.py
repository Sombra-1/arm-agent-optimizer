"""A deliberately small, non-executable JSON path implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from aarchtune.workload.errors import JsonPathError

PathToken: TypeAlias = str | int


@dataclass(frozen=True)
class PathResolution:
    """Structured path resolution that distinguishes missing from JSON null."""

    found: bool
    value: object | None = None
    reason: str | None = None
    token_index: int | None = None


def parse_json_path(path: str) -> tuple[PathToken, ...]:
    """Parse `$`, `.key`, and `[index]` segments into explicit tokens."""

    if not path or path[0] != "$":
        raise JsonPathError("JSON path must start with '$'")
    if path == "$":
        return ()

    tokens: list[PathToken] = []
    position = 1
    while position < len(path):
        marker = path[position]
        if marker == ".":
            start = position + 1
            position = start
            while position < len(path) and path[position] not in ".[":
                position += 1
            key = path[start:position]
            if not key:
                raise JsonPathError("Dictionary path segments must not be empty")
            if any(character.isspace() for character in key):
                raise JsonPathError("Dictionary path segments must not contain whitespace")
            if any(character in key for character in "*?()@]"):
                raise JsonPathError("Wildcards, expressions, and function syntax are not supported")
            tokens.append(key)
            continue
        if marker == "[":
            closing = path.find("]", position + 1)
            if closing == -1:
                raise JsonPathError("List index is missing a closing ']'")
            index_text = path[position + 1 : closing]
            if not index_text or not index_text.isascii() or not index_text.isdigit():
                raise JsonPathError("List indexes must be non-negative decimal integers")
            tokens.append(int(index_text))
            position = closing + 1
            continue
        raise JsonPathError(f"Unexpected character {marker!r} at offset {position}")
    return tuple(tokens)


def resolve_json_path(document: object, path: str | tuple[PathToken, ...]) -> PathResolution:
    """Resolve tokens without coercion, evaluation, wildcard expansion, or exceptions."""

    tokens = parse_json_path(path) if isinstance(path, str) else path
    current = document
    for token_index, token in enumerate(tokens):
        if isinstance(token, str):
            if not isinstance(current, dict):
                return PathResolution(
                    found=False,
                    reason=f"Expected an object before key {token!r}",
                    token_index=token_index,
                )
            if token not in current:
                return PathResolution(
                    found=False,
                    reason=f"Object key {token!r} is missing",
                    token_index=token_index,
                )
            current = current[token]
        else:
            if not isinstance(current, list):
                return PathResolution(
                    found=False,
                    reason=f"Expected an array before index {token}",
                    token_index=token_index,
                )
            if token >= len(current):
                return PathResolution(
                    found=False,
                    reason=f"Array index {token} is out of range",
                    token_index=token_index,
                )
            current = current[token]
    return PathResolution(found=True, value=current)
