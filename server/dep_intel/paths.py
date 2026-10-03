"""Path ancestry checks that do not treat sibling prefixes as children."""

from __future__ import annotations

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


def relative_posix(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
