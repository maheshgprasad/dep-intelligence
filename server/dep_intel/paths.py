"""Path ancestry checks that do not treat sibling prefixes as children."""

from __future__ import annotations

import os
from pathlib import Path


def contained(child: Path, parent: Path) -> bool:
    """True when ``child`` is ``parent`` or a path inside it.

    Both sides are resolved so symlinks and ``..`` cannot escape, and a
    sibling such as ``dist-extra`` is not treated as inside ``dist``.
    """
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def bounded_child(root: Path, name: str) -> Path | None:
    """Join ``name`` under ``root`` only when normalization cannot escape it.

    The normalized candidate is accepted only when it sits strictly inside
    ``root``. Callers use that result instead of the original request value.
    """
    base = os.path.abspath(root)
    candidate = os.path.normpath(os.path.join(base, name))
    prefix = base if base.endswith(os.sep) else base + os.sep
    if candidate.startswith(prefix):
        return Path(candidate)
    return None


def relative_posix(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
