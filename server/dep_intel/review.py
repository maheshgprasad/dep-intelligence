"""Static review: unused functions, and undefined names in Python."""

from __future__ import annotations

import ast
import builtins
import re

from dep_intel.config import Settings
from dep_intel.sources import Workspace
from dep_intel.store import now, write_json

_BUILTINS = set(dir(builtins))


def review(workspaces: list[Workspace], settings: Settings) -> dict:
    dead: list[dict] = []
    undefined: list[dict] = []
    for workspace in workspaces:
        imported_elsewhere = _imported_names(workspace)
        for path, text in workspace.files.items():
            if path.endswith(".py"):
                file_dead, file_undefined = _python(text)
            elif path.endswith((".js", ".jsx", ".mjs")):
                file_dead, file_undefined = _javascript(text), []
            else:
                continue
            file_dead = [(name, line) for name, line in file_dead if name not in imported_elsewhere]
            for name, line in file_dead:
                dead.append(
                    {
                        "repo": workspace.ref.name,
                        "file": path,
                        "name": name,
                        "line": line,
                        "message": f"{name} is defined and never referenced in this file.",
                    }
                )
            for name, line in file_undefined:
                undefined.append(
                    {
                        "repo": workspace.ref.name,
                        "file": path,
                        "name": name,
                        "line": line,
                        "message": f"{name} is used and not defined in this file.",
                    }
                )
    payload = {
        "meta": {"generated_at": now(), "source": "dep-intel", "total_findings": len(dead) + len(undefined)},
        "findings": {"dead_code": dead, "undefined_variables": undefined},
        "summary": {"total": len(dead) + len(undefined), "dead_code": len(dead), "undefined_variables": len(undefined)},
    }
    write_json(settings.output_dir, "code_review.json", payload)
    return {
        "success": True,
        "message": f"Found {payload['summary']['total']} issues: {len(dead)} dead code, {len(undefined)} undefined variables.",
        "summary": payload["summary"],
    }


def _imported_names(workspace: Workspace) -> set[str]:
    names: set[str] = set()
    for path, text in workspace.files.items():
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.name)
    return names


def _python(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    defined: set[str] = set()
    assigned: set[str] = set()
    imported: set[str] = set()
    loaded: dict[str, int] = {}
    functions: list[tuple[str, int]] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            functions.append((node.name, node.lineno))
            defined.add(node.name)
            for arg in [*node.args.args, *node.args.kwonlyargs]:
                assigned.add(arg.arg)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                imported.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                imported.add(alias.asname or alias.name)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                loaded.setdefault(node.id, node.lineno)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                assigned.add(node.name)
            self.generic_visit(node)

    Visitor().visit(tree)
    known = defined | assigned | imported | _BUILTINS
    undefined = [(name, line) for name, line in loaded.items() if name not in known]
    dead = [
        (name, line)
        for name, line in functions
        if name not in loaded and not name.startswith(("_", "test"))
    ]
    return dead, undefined


def _javascript(text: str) -> list[tuple[str, int]]:
    dead = []
    for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\(", text):
        name = match.group(1)
        line = text[: match.start()].count("\n") + 1
        if len(re.findall(rf"\b{re.escape(name)}\b", text)) == 1:
            dead.append((name, line))
    return dead
