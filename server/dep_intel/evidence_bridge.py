"""Align structural graph nodes with repository-relative file identities.

Temporal lookup must not join on absolute filesystem paths or basenames.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from dep_intel.graph_models import (
    EVENT_CONTRACT,
    GRPC_CONTRACT,
    HTTP_CONTRACT,
    UNRESOLVED,
    GraphNode,
    PathStep,
    Snapshot,
)

CONTRACT_CATEGORIES = {HTTP_CONTRACT, GRPC_CONTRACT, EVENT_CONTRACT, UNRESOLVED}


@dataclass(frozen=True)
class CanonicalFileRef:
    service_id: str
    repository: str
    relative_file_path: str

    def token(self) -> tuple[str, str, str]:
        return (self.service_id, self.repository, self.relative_file_path)

    def label(self) -> str:
        return f"{self.service_id}:{self.relative_file_path}"


def normalize_relative_path(value: str, *, repo_root: str | Path | None = None, repository: str = "") -> str:
    """Return a stable repository-relative POSIX path, or empty if none.

    Never reduces a path to its basename for matching.
    """
    if not value or not str(value).strip():
        return ""
    text = str(value).strip().replace("\\", "/")
    if not text:
        return ""
    windows = PureWindowsPath(text)
    if windows.drive or text.startswith("//") or (len(text) > 1 and text[1] == ":"):
        text = windows.as_posix()
        if windows.drive:
            text = text.split(":", 1)[-1]
            if not text.startswith("/"):
                text = "/" + text
    text = text.replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if repo_root is not None:
        stripped = _strip_root(text, Path(repo_root))
        if stripped:
            text = stripped
    if repository:
        stripped = _strip_repository_suffix(text, repository)
        if stripped:
            text = stripped
    posix = PurePosixPath(text)
    parts: list[str] = []
    for part in posix.parts:
        if part in {"/", ".", ""}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def canonical_file_ref(
    node: GraphNode,
    snapshot: Snapshot,
    *,
    repo_root: str | Path | None = None,
) -> CanonicalFileRef | None:
    if node.category in CONTRACT_CATEGORIES:
        return None
    path = normalize_relative_path(
        node.file_path,
        repo_root=repo_root,
        repository=_repository_for(snapshot, node.service_id),
    )
    if not path:
        return None
    return CanonicalFileRef(
        service_id=node.service_id,
        repository=_repository_for(snapshot, node.service_id),
        relative_file_path=path,
    )


def file_backed_chain(
    snapshot: Snapshot,
    root_key: str,
    path: list[PathStep],
    *,
    repo_root: str | Path | None = None,
) -> list[tuple[GraphNode, CanonicalFileRef]]:
    """Compress a structural path to consecutive file-backed nodes.

    Contract nodes stay out of the file chain; they remain on the structural path.
    """
    keys = [root_key, *[step.target for step in path]]
    chain: list[tuple[GraphNode, CanonicalFileRef]] = []
    last: tuple[str, str] | None = None
    for key in keys:
        node = snapshot.nodes.get(key)
        if node is None:
            continue
        ref = canonical_file_ref(node, snapshot, repo_root=repo_root)
        if ref is None:
            continue
        ident = (ref.service_id, ref.relative_file_path)
        if ident == last:
            continue
        chain.append((node, ref))
        last = ident
    return chain


def file_segments(chain: list[tuple[GraphNode, CanonicalFileRef]]) -> list[tuple[CanonicalFileRef, CanonicalFileRef]]:
    return [(chain[index][1], chain[index + 1][1]) for index in range(len(chain) - 1)]


def refs_equal(left: CanonicalFileRef, right: CanonicalFileRef) -> bool:
    return left.service_id == right.service_id and left.relative_file_path == right.relative_file_path


def lookup_file_nodes(snapshot: Snapshot, ref: CanonicalFileRef) -> list[GraphNode]:
    """Resolve a canonical file to snapshot nodes. Full relative path only."""
    wanted = ref.relative_file_path
    matches: list[GraphNode] = []
    for node in snapshot.nodes.values():
        if node.service_id != ref.service_id:
            continue
        path = normalize_relative_path(node.file_path, repository=_repository_for(snapshot, node.service_id))
        if path == wanted:
            matches.append(node)
    matches.sort(key=lambda node: (node.category != "file_anchor", node.kind != "File", node.key))
    return matches


def _repository_for(snapshot: Snapshot, service_id: str) -> str:
    part = snapshot.partitions.get(service_id)
    if part is not None and part.repository:
        return part.repository
    return service_id


def _strip_root(text: str, root: Path) -> str:
    prefix = root.resolve().as_posix().rstrip("/") + "/"
    candidate = text
    if candidate.startswith(prefix):
        return candidate[len(prefix) :]
    try:
        return Path(text).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def _strip_repository_suffix(text: str, repository: str) -> str:
    marker = normalize_relative_path(repository.replace("\\", "/"))
    if not marker or not text.startswith("/"):
        return ""
    needle = "/" + marker.strip("/") + "/"
    index = text.find(needle)
    if index < 0:
        alt = "/" + marker.strip("/")
        if text.endswith(alt):
            return ""
        return ""
    return text[index + len(needle) :]
