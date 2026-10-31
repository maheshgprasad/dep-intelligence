"""Docker Compose service names are aliases."""

from __future__ import annotations

from pathlib import Path

import yaml


def service_aliases(root: Path, service: str) -> list[dict[str, str]]:
    rows = []
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        path = root / name
        if not path.is_file():
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        services = document.get("services") if isinstance(document, dict) else None
        if not isinstance(services, dict):
            continue
        leaf = service.rsplit("/", 1)[-1].lower()
        for key in services:
            token = str(key).lower()
            if token != leaf and token not in leaf.split("-"):
                continue
            rows.append({"alias": str(key), "service": service, "source": "docker_compose", "file": name})
    return rows
