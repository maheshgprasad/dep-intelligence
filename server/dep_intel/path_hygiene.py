"""Remove local checkout paths from text that can leave the machine."""

from __future__ import annotations

import re
from typing import Any

_POSIX = re.compile(r"(?:/(?:home|Users|tmp|var|opt|private)/)[^\s\"'`<>|,;)]+")
_WINDOWS = re.compile(r"[A-Za-z]:\\(?:Users|home)\\[^\s\"'`<>|,;)]+")


def scrub_text(value: str) -> str:
    text = _WINDOWS.sub(_windows_tail, value or "")
    return _POSIX.sub(_posix_tail, text)


def scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    return value


def _posix_tail(match: re.Match[str]) -> str:
    return _tail(match.group(0).split("/"))


def _windows_tail(match: re.Match[str]) -> str:
    return _tail(re.split(r"[\\/]", match.group(0)))


def _tail(parts: list[str]) -> str:
    kept = [part for part in parts if part and not re.fullmatch(r"[A-Za-z]:", part)]
    if "checkouts" in kept:
        kept = kept[kept.index("checkouts") + 1 :]
    elif len(kept) >= 3:
        kept = kept[-2:]
    return "/".join(kept)
