"""Resolve a client reference to one analyzed service, or leave it unresolved."""

from __future__ import annotations

from urllib.parse import urlparse

from dep_intel.topology.models import relationship, unresolved


def resolve(services: list[dict], calls: list[dict], aliases: list[dict], bindings: list[dict]) -> dict:
    by_alias: dict[str, set[str]] = {}
    for item in aliases:
        by_alias.setdefault(item["alias"].lower(), set()).add(item["service"])
    for service in services:
        by_alias.setdefault(service["service"].lower(), set()).add(service["service"])
    linked = []
    open_refs = []
    for call in calls:
        host, path = _host_and_path(call.get("url") or "")
        method = call.get("method") or ""
        source = call.get("service") or ""
        binding = _binding(bindings, source, call.get("file") or "", call.get("client") or "")
        if host:
            owners = set(by_alias.get(host.lower(), set()))
            named = _unique_service_name(host, services)
            if named and named not in owners:
                owners.add(named)
            if len(owners) == 1:
                target = next(iter(owners))
                if target == source:
                    continue
                reasons = [f"{source} calls {host}", f"{host} matches analyzed service {target}"]
                linked.append(relationship(source, target, method, path, reasons, "host"))
                continue
            snippet = _alias_snippet(host, "")
            open_refs.append(unresolved(
                source,
                method,
                path,
                host,
                f"{source} calls {host}, and Nami could not associate that host with one analyzed service.",
                snippet,
            ))
            continue
        if binding:
            linked.append(relationship(
                source,
                binding,
                method,
                path,
                [f"{source} {method} {path} is bound to {binding} by the compatibility manifest."],
                "manifest",
            ))
            continue
        open_refs.append(unresolved(
            source,
            method,
            path,
            "",
            f"{source} calls {method} {path} without a host. A matching route name is not used to choose a provider.",
            "",
        ))
    seen = set()
    unique = []
    for item in linked:
        key = (item.get("from"), item.get("to"), item.get("method"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"relationships": unique, "unresolved": open_refs}


def _host_and_path(url: str) -> tuple[str, str]:
    if url.startswith("http://") or url.startswith("https://"):
        parsed = urlparse(url)
        return parsed.hostname or "", parsed.path or "/"
    if url.startswith("/"):
        return "", url
    return "", url


def _binding(bindings: list[dict], service: str, file: str, client: str) -> str:
    matches = [
        item["target"]
        for item in bindings
        if item.get("service") == service and item.get("file") == file and (not client or item.get("client") in {"", client})
    ]
    unique = set(matches)
    if len(unique) == 1:
        return next(iter(unique))
    return ""


def _unique_service_name(host: str, services: list[dict]) -> str:
    token = host.lower()
    if token in {"api", "app", "service", "web", "www"}:
        return ""
    matches = []
    for service in services:
        leaf = str(service.get("service") or "").rsplit("/", 1)[-1].lower()
        parts = [part for part in leaf.split("-") if part]
        if token == leaf or token in parts:
            matches.append(service["service"])
    unique = set(matches)
    if len(unique) == 1:
        return next(iter(unique))
    return ""


def _alias_snippet(host: str, service: str) -> str:
    target = service or "service-id"
    return f"aliases:\n  {host}:\n    service: {target}\n"
