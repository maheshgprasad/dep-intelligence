"""Tree-sitter extraction for Express, Axios, and fetch.

Ordinary regular expressions are not used as the parser. Receiver bindings
decide whether a `.get` call is a route, an HTTP client, or neither.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from tree_sitter import Language, Parser

import tree_sitter_javascript as tsjs

from dep_intel.graph_models import EXTRACTOR_VERSION

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_LANGUAGE = Language(tsjs.language())
_PARSER = Parser(_LANGUAGE)


@dataclass
class Binding:
    kind: str
    value: str = ""
    dynamic: bool = False
    module: str = ""


@dataclass
class RouteHit:
    method: str
    path: str
    file: str
    line: int
    end_line: int
    receiver: str
    handler_name: str
    handler_kind: str
    handler_line: int
    dynamic_path: bool = False


@dataclass
class MountHit:
    file: str
    line: int
    receiver: str
    prefix: str
    target: str
    target_module: str = ""


@dataclass
class CallHit:
    method: str
    url: str
    file: str
    line: int
    client: str
    client_kind: str
    base_url: str
    base_dynamic: bool
    url_dynamic: bool


@dataclass
class FileExtract:
    path: str
    routes: list[RouteHit] = field(default_factory=list)
    mounts: list[MountHit] = field(default_factory=list)
    calls: list[CallHit] = field(default_factory=list)
    exports: dict[str, str] = field(default_factory=dict)
    export_binding: str = ""
    diagnostics: list[str] = field(default_factory=list)
    extractor: str = EXTRACTOR_VERSION


@dataclass
class ResolvedRoute:
    method: str
    path: str
    file: str
    line: int
    end_line: int
    handler_name: str
    handler_kind: str
    handler_line: int
    dynamic_path: bool = False


class _Scope:
    def __init__(self, parent: "_Scope | None" = None) -> None:
        self.parent = parent
        self.bindings: dict[str, Binding] = {}

    def get(self, name: str) -> Binding | None:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent is not None:
            return self.parent.get(name)
        return None

    def put(self, name: str, binding: Binding) -> None:
        self.bindings[name] = binding


def extract_javascript(path: str, source: str) -> FileExtract:
    result = FileExtract(path=path)
    tree = _PARSER.parse(source.encode("utf-8"))
    visit(tree.root_node, _Scope(), result, source)
    return result


def resolve_routes(files: dict[str, FileExtract]) -> tuple[list[ResolvedRoute], list[str]]:
    """Apply same-repository router mounts, including relative require() targets."""
    diagnostics: list[str] = []
    resolved: list[ResolvedRoute] = []
    seen_apps: set[tuple[str, str]] = set()
    for path, extracted in files.items():
        for name, binding in _top_bindings(extracted):
            if binding.kind == "express_app":
                key = (path, name)
                if key in seen_apps:
                    continue
                seen_apps.add(key)
                resolved.extend(_routes_for(files, path, name, "", set(), diagnostics))
    return resolved, diagnostics


def _top_bindings(extracted: FileExtract) -> list[tuple[str, Binding]]:
    # Re-parse is avoided: exports and routes already name the receivers.
    # App bindings are recovered from route/mount receivers plus a fresh walk
    # stored on the extract via route receivers. Callers pass files that were
    # just extracted; we re-read bindings by a second lightweight parse.
    return extracted.__dict__.get("_bindings", [])


def visit(node, scope: _Scope, result: FileExtract, source: str) -> None:
    ntype = node.type
    if ntype in {"function_declaration", "function_expression", "arrow_function", "method_definition"}:
        inner = _Scope(scope)
        for child in node.named_children:
            if child.type != "formal_parameters":
                visit(child, inner, result, source)
        return
    if ntype in {"lexical_declaration", "variable_declaration"}:
        for child in node.named_children:
            if child.type == "variable_declarator":
                _bind_declarator(child, scope, result, source)
        return
    if ntype == "assignment_expression":
        _note_assignment(node, scope, result, source)
    if ntype == "call_expression":
        _note_call(node, scope, result, source)
    for child in node.named_children:
        visit(child, scope, result, source)


def _bind_declarator(node, scope: _Scope, result: FileExtract, source: str) -> None:
    name_node = node.child_by_field_name("name")
    value = node.child_by_field_name("value")
    if name_node is None or name_node.type != "identifier":
        if value is not None:
            visit(value, scope, result, source)
        return
    binding = _interpret(value, scope, result, source) if value is not None else Binding("unknown", dynamic=True)
    scope.put(_text(name_node), binding)
    bindings: list[tuple[str, Binding]] = result.__dict__.setdefault("_bindings", [])
    if scope.parent is None:
        bindings.append((_text(name_node), binding))
    if value is not None:
        visit(value, scope, result, source)


def _interpret(node, scope: _Scope, result: FileExtract, source: str) -> Binding:
    if node is None:
        return Binding("unknown", dynamic=True)
    if node.type == "identifier":
        found = scope.get(_text(node))
        return found if found is not None else Binding("unknown", dynamic=True)
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func is not None and func.type == "identifier" and _text(func) == "require":
            spec, dynamic = _static_arg(node, 0, scope)
            if dynamic or not spec:
                return Binding("unknown", dynamic=True)
            if spec == "express":
                return Binding("express_module", module=spec)
            if spec == "axios":
                return Binding("axios_module", module=spec)
            return Binding("imported", module=spec)
        if func is not None and func.type == "identifier":
            target = scope.get(_text(func))
            if target and target.kind == "express_module":
                return Binding("express_app")
        if func is not None and func.type == "member_expression":
            obj = func.child_by_field_name("object")
            prop = func.child_by_field_name("property")
            prop_name = _text(prop) if prop is not None else ""
            obj_binding = scope.get(_text(obj)) if obj is not None and obj.type == "identifier" else None
            if obj_binding and obj_binding.kind == "express_module" and prop_name == "Router":
                return Binding("express_router")
            if obj_binding and obj_binding.kind == "axios_module" and prop_name == "create":
                base, dynamic = _object_field(node, "baseURL", scope)
                return Binding("axios_instance", value=base or "", dynamic=dynamic)
        return Binding("unknown")
    if node.type in {"string", "template_string"}:
        value, dynamic = _static_expr(node, scope)
        return Binding("string", value=value or "", dynamic=dynamic)
    if node.type == "parenthesized_expression" and node.named_child_count:
        return _interpret(node.named_child(0), scope, result, source)
    return Binding("unknown", dynamic=True)


def _note_assignment(node, scope: _Scope, result: FileExtract, source: str) -> None:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return
    if left.type == "member_expression":
        obj = left.child_by_field_name("object")
        prop = left.child_by_field_name("property")
        if obj is not None and prop is not None and _text(obj) == "module" and _text(prop) == "exports":
            if right.type == "identifier":
                result.export_binding = _text(right)
            elif right.type == "object":
                for child in right.named_children:
                    if child.type == "pair":
                        key = child.child_by_field_name("key")
                        value = child.child_by_field_name("value")
                        if key is not None and value is not None and value.type == "identifier":
                            result.exports[_text(key)] = _text(value)
                    elif child.type == "shorthand_property_identifier":
                        result.exports[_text(child)] = _text(child)
        if obj is not None and _text(obj) == "exports" and prop is not None and right.type == "identifier":
            result.exports[_text(prop)] = _text(right)
    if right.type == "call_expression" or right.type in {"string", "template_string"}:
        if left.type == "identifier":
            scope.put(_text(left), _interpret(right, scope, result, source))


def _note_call(node, scope: _Scope, result: FileExtract, source: str) -> None:
    func = node.child_by_field_name("function")
    if func is None:
        return
    if func.type == "identifier" and _text(func) == "fetch":
        _note_fetch(node, scope, result)
        return
    if func.type != "member_expression":
        return
    obj = func.child_by_field_name("object")
    prop = func.child_by_field_name("property")
    if obj is None or prop is None or obj.type != "identifier":
        return
    method = _text(prop)
    receiver = _text(obj)
    binding = scope.get(receiver)
    if binding is None:
        return
    method_key = method.lower()
    if binding.kind in {"express_app", "express_router"} and method_key in HTTP_METHODS:
        path, dynamic = _static_arg(node, 0, scope)
        handler_name, handler_kind, handler_line = _handler(node)
        if dynamic or not path:
            result.diagnostics.append(f"{result.path}:{_line(node)} dynamic Express path on {receiver}.{method}")
            result.routes.append(
                RouteHit(
                    method=method_key.upper(),
                    path=path or "",
                    file=result.path,
                    line=_line(node),
                    end_line=_end_line(node),
                    receiver=receiver,
                    handler_name=handler_name,
                    handler_kind=handler_kind,
                    handler_line=handler_line,
                    dynamic_path=True,
                )
            )
            return
        result.routes.append(
            RouteHit(
                method=method_key.upper(),
                path=path,
                file=result.path,
                line=_line(node),
                end_line=_end_line(node),
                receiver=receiver,
                handler_name=handler_name,
                handler_kind=handler_kind,
                handler_line=handler_line,
            )
        )
        return
    if binding.kind in {"express_app", "express_router"} and method == "use":
        _note_mount(node, scope, result, receiver)
        return
    if binding.kind in {"axios_module", "axios_instance"} and method_key in HTTP_METHODS:
        url, dynamic = _static_arg(node, 0, scope)
        result.calls.append(
            CallHit(
                method=method_key.upper(),
                url=url or "",
                file=result.path,
                line=_line(node),
                client=receiver,
                client_kind=binding.kind,
                base_url=binding.value if binding.kind == "axios_instance" else "",
                base_dynamic=binding.dynamic if binding.kind == "axios_instance" else False,
                url_dynamic=dynamic or not url,
            )
        )


def _note_fetch(node, scope: _Scope, result: FileExtract) -> None:
    url, dynamic = _static_arg(node, 0, scope)
    method = "GET"
    method_dynamic = False
    init = _arg(node, 1)
    if init is not None:
        found, dyn = _object_field_node(init, "method", scope)
        if dyn:
            method = ""
            method_dynamic = True
        elif found:
            method = found.upper()
    if method_dynamic:
        result.diagnostics.append(f"{result.path}:{_line(node)} fetch method is dynamic")
    result.calls.append(
        CallHit(
            method=method,
            url=url or "",
            file=result.path,
            line=_line(node),
            client="fetch",
            client_kind="fetch",
            base_url="",
            base_dynamic=False,
            url_dynamic=dynamic or not url,
        )
    )


def _note_mount(node, scope: _Scope, result: FileExtract, receiver: str) -> None:
    first = _arg(node, 0)
    second = _arg(node, 1)
    prefix = ""
    target_node = first
    if first is not None and first.type in {"string", "template_string"}:
        value, dynamic = _static_expr(first, scope)
        if dynamic:
            result.diagnostics.append(f"{result.path}:{_line(node)} dynamic router mount prefix")
            return
        prefix = value or ""
        target_node = second
    if target_node is None:
        return
    if target_node.type == "identifier":
        result.mounts.append(MountHit(result.path, _line(node), receiver, prefix, _text(target_node)))
        return
    if target_node.type == "call_expression":
        func = target_node.child_by_field_name("function")
        if func is not None and func.type == "identifier" and _text(func) == "require":
            spec, dynamic = _static_arg(target_node, 0, scope)
            if not dynamic and spec:
                result.mounts.append(MountHit(result.path, _line(node), receiver, prefix, "", spec))


def _handler(node) -> tuple[str, str, int]:
    arg = _arg(node, 1)
    if arg is None:
        return "", "missing", _line(node)
    if arg.type == "identifier":
        return _text(arg), "named", _line(arg)
    if arg.type in {"arrow_function", "function_expression", "function"}:
        name = arg.child_by_field_name("name")
        if name is not None:
            return _text(name), "named", _line(arg)
        return "", "inline", _line(arg)
    return "", "unknown", _line(arg)


def _routes_for(
    files: dict[str, FileExtract],
    path: str,
    binding: str,
    prefix: str,
    stack: set[tuple[str, str]],
    diagnostics: list[str],
) -> list[ResolvedRoute]:
    key = (path, binding)
    if key in stack:
        diagnostics.append(f"{path}: router mount cycle involving {binding}")
        return []
    extracted = files.get(path)
    if extracted is None:
        return []
    stack = set(stack)
    stack.add(key)
    found: list[ResolvedRoute] = []
    for route in extracted.routes:
        if route.receiver != binding or route.dynamic_path:
            continue
        found.append(
            ResolvedRoute(
                method=route.method,
                path=_join(prefix, route.path),
                file=route.file,
                line=route.line,
                end_line=route.end_line,
                handler_name=route.handler_name,
                handler_kind=route.handler_kind,
                handler_line=route.handler_line,
            )
        )
    for mount in extracted.mounts:
        if mount.receiver != binding:
            continue
        next_prefix = _join(prefix, mount.prefix)
        if mount.target:
            target_binding = mount.target
            target_file = path
            imported = _binding_module(extracted, mount.target)
            if imported:
                resolved = _resolve_module(path, imported, files)
                if resolved is None:
                    diagnostics.append(f"{path}:{mount.line} could not resolve router import {imported}")
                    continue
                target_file = resolved
                target_binding = files[resolved].export_binding or mount.target
            found.extend(_routes_for(files, target_file, target_binding, next_prefix, stack, diagnostics))
        elif mount.target_module:
            resolved = _resolve_module(path, mount.target_module, files)
            if resolved is None:
                diagnostics.append(f"{path}:{mount.line} could not resolve router import {mount.target_module}")
                continue
            target_binding = files[resolved].export_binding
            if not target_binding:
                diagnostics.append(f"{resolved} does not export a router binding")
                continue
            found.extend(_routes_for(files, resolved, target_binding, next_prefix, stack, diagnostics))
    return found


def _binding_module(extracted: FileExtract, name: str) -> str:
    for binding_name, binding in extracted.__dict__.get("_bindings", []):
        if binding_name == name and binding.kind == "imported":
            return binding.module
    return ""


def _resolve_module(from_file: str, spec: str, files: dict[str, FileExtract]) -> str | None:
    if not spec.startswith("."):
        return None
    base = PurePosixPath(from_file).parent
    for candidate in (spec, f"{spec}.js", f"{spec}/index.js"):
        raw = str(base / candidate)
        normalized = str(PurePosixPath(raw))
        if normalized in files:
            return normalized
    return None


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path or path == "/":
        return prefix.rstrip("/") + ("/" if path == "/" else "")
    left = prefix.rstrip("/")
    right = path if path.startswith("/") else f"/{path}"
    return left + right


def _static_arg(node, index: int, scope: _Scope) -> tuple[str | None, bool]:
    arg = _arg(node, index)
    if arg is None:
        return None, True
    return _static_expr(arg, scope)


def _static_expr(node, scope: _Scope) -> tuple[str | None, bool]:
    if node.type == "string":
        text = _text(node)
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            return text[1:-1], False
        return text, False
    if node.type == "template_string":
        if any(child.type == "template_substitution" for child in node.named_children):
            return None, True
        fragments = [_text(child) for child in node.named_children if child.type == "string_fragment"]
        return "".join(fragments), False
    if node.type == "identifier":
        binding = scope.get(_text(node))
        if binding and binding.kind == "string" and not binding.dynamic:
            return binding.value, False
        return None, True
    if node.type == "parenthesized_expression" and node.named_child_count:
        return _static_expr(node.named_child(0), scope)
    return None, True


def _object_field(call, field: str, scope: _Scope) -> tuple[str | None, bool]:
    args = call.child_by_field_name("arguments")
    if args is None or args.named_child_count == 0:
        return None, True
    return _object_field_node(args.named_child(0), field, scope)


def _object_field_node(node, field: str, scope: _Scope) -> tuple[str | None, bool]:
    if node.type != "object":
        return None, False
    for child in node.named_children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if key is None or _text(key) != field or value is None:
            continue
        return _static_expr(value, scope)
    return None, False


def _arg(node, index: int):
    args = node.child_by_field_name("arguments")
    if args is None or index >= args.named_child_count:
        return None
    return args.named_child(index)


def _text(node) -> str:
    raw = node.text
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _line(node) -> int:
    return int(node.start_point.row) + 1


def _end_line(node) -> int:
    return int(node.end_point.row) + 1
