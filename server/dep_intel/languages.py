"""Language detection from manifests and source extensions."""

from __future__ import annotations

from pathlib import Path

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

MANIFEST_LANGUAGE = {
    "go.mod": ("Go", "go"),
    "go.sum": ("Go", "go"),
    "tsconfig.json": ("TypeScript", "typescript"),
    "requirements.txt": ("Python", "python"),
    "setup.py": ("Python", "python"),
    "pyproject.toml": ("Python", "python"),
    "pom.xml": ("Java", "java"),
    "build.gradle": ("Java", "java"),
    "Gemfile": ("Ruby", "ruby"),
    "composer.json": ("PHP", "php"),
    "Cargo.toml": ("Rust", "rust"),
    "CMakeLists.txt": ("C++", "cpp"),
    "package.json": ("JavaScript", "javascript"),
}

EXTENSIONS = {
    ".py": ("Python", "python"),
    ".js": ("JavaScript", "javascript"),
    ".jsx": ("JavaScript", "javascript"),
    ".mjs": ("JavaScript", "javascript"),
    ".ts": ("TypeScript", "typescript"),
    ".tsx": ("TypeScript", "typescript"),
    ".go": ("Go", "go"),
    ".java": ("Java", "java"),
    ".rb": ("Ruby", "ruby"),
    ".php": ("PHP", "php"),
    ".rs": ("Rust", "rust"),
    ".cs": ("C#", "csharp"),
    ".cpp": ("C++", "cpp"),
    ".cc": ("C++", "cpp"),
    ".h": ("C++", "cpp"),
    ".hpp": ("C++", "cpp"),
}

COLORS = {
    "python": "#3572A5",
    "javascript": "#f1e05a",
    "typescript": "#3178c6",
    "go": "#00ADD8",
    "java": "#b07219",
    "ruby": "#701516",
    "php": "#4F5D95",
    "rust": "#dea584",
    "csharp": "#178600",
    "cpp": "#f34b7d",
}


def detect(workspaces: list[Workspace], settings: Settings) -> dict:
    repositories = {}
    for workspace in workspaces:
        repositories[workspace.ref.name] = _detect_one(workspace)
    payload = {
        "meta": {
            "generated_at": now(),
            "source": "dep-intel",
            "repos_file": str(settings.repos_file),
            "total_repos": len(workspaces),
            "detection_stats": {
                "local": sum(1 for item in repositories.values() if item["detection_method"] == "local"),
                "github": sum(1 for item in repositories.values() if item["detection_method"] == "github"),
            },
        },
        "repositories": repositories,
    }
    write_json(settings.output_dir, "language_detection.json", payload)
    known = sum(1 for item in repositories.values() if item["language_key"] != "unknown")
    return {
        "success": True,
        "message": f"Detected languages for {known} of {len(workspaces)} repositories.",
    }


def _detect_one(workspace: Workspace) -> dict:
    scores: dict[str, int] = {}
    labels: dict[str, str] = {}
    manifests: list[str] = []
    extensions: dict[str, int] = {}
    for path in workspace.files:
        filename = Path(path).name
        suffix = Path(path).suffix.lower()
        if filename in MANIFEST_LANGUAGE:
            label, key = MANIFEST_LANGUAGE[filename]
            # TypeScript wins over a bare package.json when .ts files exist.
            weight = 50 if filename != "package.json" else 20
            scores[key] = scores.get(key, 0) + weight
            labels[key] = label
            manifests.append(filename)
        if suffix in EXTENSIONS:
            label, key = EXTENSIONS[suffix]
            extensions[suffix] = extensions.get(suffix, 0) + 1
            scores[key] = scores.get(key, 0) + 1
            labels[key] = label
    if ".ts" in extensions or ".tsx" in extensions:
        scores["typescript"] = scores.get("typescript", 0) + 30
        labels["typescript"] = "TypeScript"
    if not scores:
        primary, key = "Unknown", "unknown"
    else:
        key = max(scores, key=lambda item: scores[item])
        primary = labels[key]
    return {
        "primary_language": primary,
        "language_key": key,
        "color": COLORS.get(key, "#6f6f6f"),
        "all_languages": [labels[item] for item in sorted(scores, key=lambda item: scores[item], reverse=True)],
        "manifests": sorted(set(manifests)),
        "extensions": extensions,
        "total_files": len(workspace.files),
        "detection_method": workspace.ref.kind,
        "confidence": "high" if manifests else "medium" if extensions else "low",
        "timestamp": now(),
    }
