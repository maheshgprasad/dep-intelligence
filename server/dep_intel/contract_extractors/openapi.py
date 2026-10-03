"""Bounded OpenAPI JSON/YAML parsing.

Remote $ref URLs are reported and not fetched. Handler links are not invented
from operationId.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import yaml

Loader = Callable[[str], str | None]


@dataclass
class Operation:
    method: str
    path: str
    operation_id: str = ""
    version: str = ""
    required_request_fields: list[str] = field(default_factory=list)
    response_fields: dict[str, list[str]] = field(default_factory=dict)
    handler: str = ""
    file: str = ""

    def identity(self) -> tuple[str, str, str]:
        return (self.method.upper(), self.path, self.version)


@dataclass
class OpenAPIDocument:
    file: str
    operations: list[Operation] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    version: str = ""


def parse_openapi(text: str, path: str, loader: Loader | None = None) -> OpenAPIDocument:
    document = OpenAPIDocument(file=path)
    try:
        payload = _load(text, path)
    except Exception as exc:
        document.diagnostics.append(f"{path}: OpenAPI document did not parse ({exc})")
        return document
    if not isinstance(payload, dict):
        document.diagnostics.append(f"{path}: OpenAPI document must be a mapping")
        return document
    document.version = str(payload.get("openapi") or payload.get("swagger") or "")
    _note_remote_refs(payload, document, path)
    if "openapi" not in payload and "swagger" not in payload:
        document.diagnostics.append(f"{path}: missing openapi or swagger version; paths are still read when present")
    schemas = _schemas(payload)
    paths = payload.get("paths") or {}
    if not isinstance(paths, dict):
        document.diagnostics.append(f"{path}: paths is not an object")
        return document
    for route, body in paths.items():
        if not isinstance(body, dict):
            continue
        for method, operation in body.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if not isinstance(operation, dict):
                continue
            op = Operation(method=method.upper(), path=str(route), file=path)
            op.operation_id = str(operation.get("operationId") or "")
            handler = operation.get("x-nami-handler")
            if isinstance(handler, str):
                op.handler = handler
            op.required_request_fields = _request_fields(operation, schemas, document, loader, path)
            op.response_fields = _response_fields(operation, schemas, document, loader, path)
            document.operations.append(op)
    return document


def compare_operations(old: list[Operation], new: list[Operation]) -> list[dict[str, str]]:
    """Bounded compatibility. Unrecognized schema edits stay unknown."""
    old_map = {item.identity(): item for item in old}
    new_map = {item.identity(): item for item in new}
    findings: list[dict[str, str]] = []
    for identity, operation in old_map.items():
        if identity not in new_map:
            findings.append(
                {
                    "kind": "removed_operation",
                    "method": operation.method,
                    "path": operation.path,
                    "message": f"Removed {operation.method} {operation.path}",
                }
            )
            continue
        current = new_map[identity]
        added = sorted(set(current.required_request_fields) - set(operation.required_request_fields))
        for name in added:
            findings.append(
                {
                    "kind": "required_request_field",
                    "method": operation.method,
                    "path": operation.path,
                    "field": name,
                    "message": f"{operation.method} {operation.path} now requires request field {name}",
                }
            )
        for status, fields in operation.response_fields.items():
            current_fields = current.response_fields.get(status)
            if current_fields is None:
                findings.append(
                    {
                        "kind": "unknown",
                        "method": operation.method,
                        "path": operation.path,
                        "message": f"Response {status} for {operation.method} {operation.path} changed in an unrecognized way",
                    }
                )
                continue
            for name in sorted(set(fields) - set(current_fields)):
                findings.append(
                    {
                        "kind": "removed_response_field",
                        "method": operation.method,
                        "path": operation.path,
                        "field": name,
                        "message": f"{operation.method} {operation.path} removed response field {name}",
                    }
                )
    return findings


def _note_remote_refs(value: Any, document: OpenAPIDocument, path: str) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and (ref.startswith("http://") or ref.startswith("https://")):
            document.diagnostics.append(f"{path}: remote $ref {ref} is unsupported and was not fetched")
        for child in value.values():
            _note_remote_refs(child, document, path)
    elif isinstance(value, list):
        for child in value:
            _note_remote_refs(child, document, path)


def _load(text: str, path: str) -> Any:
    stripped = text.lstrip()
    if path.endswith(".json") or stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if path.endswith(".json"):
                raise
    return yaml.safe_load(text)


def _schemas(payload: dict[str, Any]) -> dict[str, Any]:
    components = payload.get("components") or {}
    if isinstance(components, dict) and isinstance(components.get("schemas"), dict):
        return components["schemas"]
    definitions = payload.get("definitions") or {}
    return definitions if isinstance(definitions, dict) else {}


def _request_fields(operation: dict, schemas: dict, document: OpenAPIDocument, loader: Loader | None, path: str) -> list[str]:
    body = operation.get("requestBody") or {}
    if not isinstance(body, dict):
        return []
    content = body.get("content") or {}
    if not isinstance(content, dict):
        return []
    schema = (content.get("application/json") or {}).get("schema") if isinstance(content.get("application/json"), dict) else None
    if schema is None:
        parameters = operation.get("parameters") or []
        required = []
        if isinstance(parameters, list):
            for item in parameters:
                if isinstance(item, dict) and item.get("required") and item.get("name"):
                    required.append(str(item["name"]))
        return required
    resolved = _resolve_schema(schema, schemas, document, loader, path, set())
    required = resolved.get("required") if isinstance(resolved, dict) else None
    if isinstance(required, list):
        return [str(item) for item in required]
    return []


def _response_fields(operation: dict, schemas: dict, document: OpenAPIDocument, loader: Loader | None, path: str) -> dict[str, list[str]]:
    responses = operation.get("responses") or {}
    found: dict[str, list[str]] = {}
    if not isinstance(responses, dict):
        return found
    for status, body in responses.items():
        if not isinstance(body, dict):
            continue
        content = body.get("content") or {}
        schema = None
        if isinstance(content, dict):
            app = content.get("application/json")
            if isinstance(app, dict):
                schema = app.get("schema")
        if schema is None and isinstance(body.get("schema"), dict):
            schema = body.get("schema")
        if not isinstance(schema, dict):
            continue
        resolved = _resolve_schema(schema, schemas, document, loader, path, set())
        properties = resolved.get("properties") if isinstance(resolved, dict) else None
        if isinstance(properties, dict):
            found[str(status)] = sorted(str(name) for name in properties)
    return found


def _resolve_schema(
    schema: dict,
    schemas: dict,
    document: OpenAPIDocument,
    loader: Loader | None,
    path: str,
    stack: set[str],
) -> dict:
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        if any(key in schema for key in ("allOf", "anyOf", "oneOf")):
            document.diagnostics.append(f"{path}: combinators are not expanded; compatibility for this schema is unknown")
        return schema
    if ref.startswith("http://") or ref.startswith("https://"):
        document.diagnostics.append(f"{path}: remote $ref {ref} is unsupported and was not fetched")
        return {}
    if ref in stack:
        document.diagnostics.append(f"{path}: cyclic $ref {ref}")
        return {}
    if ref.startswith("#/components/schemas/") or ref.startswith("#/definitions/"):
        name = ref.rsplit("/", 1)[-1]
        target = schemas.get(name)
        if not isinstance(target, dict):
            document.diagnostics.append(f"{path}: unresolved local $ref {ref}")
            return {}
        return _resolve_schema(target, schemas, document, loader, path, stack | {ref})
    if ref.startswith("#/"):
        document.diagnostics.append(f"{path}: unsupported document pointer {ref}")
        return {}
    if loader is None:
        document.diagnostics.append(f"{path}: relative $ref {ref} has no file loader")
        return {}
    text = loader(ref.split("#", 1)[0])
    if text is None:
        document.diagnostics.append(f"{path}: relative $ref {ref} was not found")
        return {}
    try:
        loaded = _load(text, ref)
    except Exception as exc:
        document.diagnostics.append(f"{path}: relative $ref {ref} did not parse ({exc})")
        return {}
    if not isinstance(loaded, dict):
        return {}
    pointer = ref.split("#", 1)[1] if "#" in ref else ""
    if pointer.startswith("/components/schemas/"):
        name = pointer.rsplit("/", 1)[-1]
        nested = ((loaded.get("components") or {}).get("schemas") or {}).get(name)
        if isinstance(nested, dict):
            return _resolve_schema(nested, schemas, document, loader, path, stack | {ref})
    document.diagnostics.append(f"{path}: relative $ref {ref} is outside the supported local pattern")
    return {}
