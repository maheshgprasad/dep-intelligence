"""Client for the code-review-graph MCP server.

Nami Trace does not recompute graph results. It starts `code-review-graph serve`
as a child process and returns that server's tool payloads.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from dep_intel.config import PROJECT_ROOT

# Read tools the dashboard may call. Mutations and wiki generation stay out.
QUERY_TOOLS = {
    "semantic_search_nodes_tool",
    "query_graph_tool",
    "traverse_graph_tool",
    "get_impact_radius_tool",
    "get_affected_flows_tool",
    "get_flow_tool",
    "get_community_tool",
    "detect_changes_tool",
    "get_review_context_tool",
    "list_flows_tool",
    "list_communities_tool",
    "get_hub_nodes_tool",
    "get_bridge_nodes_tool",
    "get_architecture_overview_tool",
    "find_large_functions_tool",
    "get_knowledge_gaps_tool",
    "get_surprising_connections_tool",
    "get_suggested_questions_tool",
    "list_graph_stats_tool",
    "get_minimal_context_tool",
    "refactor_tool",
}

BUILD_TOOLS = QUERY_TOOLS | {"build_or_update_graph_tool", "run_postprocess_tool"}


def prepare_call(tool: str, repo_root: str, arguments: dict[str, Any] | None, *, allow_build: bool = False) -> dict[str, Any]:
    allowed = BUILD_TOOLS if allow_build else QUERY_TOOLS
    if tool not in allowed:
        raise ValueError(f"{tool} is not a graph query this dashboard can run.")
    args = dict(arguments or {})
    if tool == "refactor_tool" and args.get("mode", "rename") not in {"dead_code", "suggest"}:
        raise ValueError("Only dead-code and suggestion refactors are available from the dashboard.")
    args["repo_root"] = repo_root
    return args


def call_tool(tool: str, repo_root: str, arguments: dict[str, Any] | None = None, *, allow_build: bool = False) -> dict[str, Any]:
    args = prepare_call(tool, repo_root, arguments, allow_build=allow_build)
    return _bridge.call(tool, args)


def crg_diagnostics() -> dict[str, Any]:
    """Presence and version of the external code-review-graph command."""
    import subprocess

    from dep_intel.crg_adapter import SUPPORTED_PACKAGE, SUPPORTED_SCHEMA_VERSIONS, crg_package_version

    command = os.environ.get("CRG_BIN") or shutil.which("code-review-graph") or ""
    version = crg_package_version()
    if command and not version:
        completed = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=15, check=False)
        version = (completed.stdout or completed.stderr or "").strip().split()[-1] if completed.returncode == 0 else ""
    supported = version.startswith("2.3.") if version else False
    return {
        "available": bool(command),
        "command": command,
        "version": version,
        "supported_package": SUPPORTED_PACKAGE,
        "supported": supported,
        "schema_versions": sorted(SUPPORTED_SCHEMA_VERSIONS),
    }


class _Bridge:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._call_lock: asyncio.Lock | None = None

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_thread()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._call(tool, arguments), self._loop)
        return future.result(timeout=900)

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="nami-crg", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("The code-review-graph MCP client did not start.")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._call_lock is None:
            self._call_lock = asyncio.Lock()
        async with self._call_lock:
            session = await _Session.instance().get()
            try:
                result = await session.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=600))
            except Exception:
                await _Session.instance().reset()
                session = await _Session.instance().get()
                result = await session.call_tool(tool, arguments, read_timeout_seconds=timedelta(seconds=600))
            return _unpack(result)


class _Session:
    _singleton: _Session | None = None

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._lock: asyncio.Lock | None = None

    @classmethod
    def instance(cls) -> _Session:
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton

    async def get(self) -> ClientSession:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._session is not None:
                return self._session
            command = os.environ.get("CRG_BIN") or shutil.which("code-review-graph")
            if not command:
                raise RuntimeError("code-review-graph is not on PATH. Install it, or set CRG_BIN.")
            stack = AsyncExitStack()
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=command,
                        args=["serve"],
                        cwd=str(PROJECT_ROOT),
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._stack = stack
            self._session = session
            return session

    async def reset(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            session = self._session
            stack = self._stack
            self._session = None
            self._stack = None
            if stack is not None:
                try:
                    await stack.aclose()
                except Exception:
                    if session is not None:
                        return


def _unpack(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        payload = dict(structured)
    else:
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"text": text}
            payload = parsed if isinstance(parsed, dict) else {"value": parsed}
            break
    if getattr(result, "isError", False):
        message = payload.get("text") or payload.get("summary") or payload.get("error") or "code-review-graph tool failed"
        raise RuntimeError(str(message))
    return payload


_bridge = _Bridge()
