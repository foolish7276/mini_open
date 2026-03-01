"""内置工具系统（高保真版）。"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from langchain_core.tools import BaseTool, tool

from .memory import MemoryStore

MAX_FILE_BYTES = 1_000_000
MAX_TOOL_OUTPUT_CHARS = 30_000


@dataclass
class ToolRuntime:
    """工具运行时上下文。

    说明：
    - 该对象由 Agent 每次 run 前更新当前 session 信息。
    - 工具通过它访问 memory/subagent 能力。
    """

    workspace: Path
    memory: MemoryStore
    session_key: str = "default"
    spawn_subagent: Callable[[str, str | None], Awaitable[dict[str, str]]] | None = None


def _trim(s: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """统一截断工具输出，避免单次结果占满上下文窗口。"""
    if len(s) <= limit:
        return s
    return s[:limit] + "\n...[truncated]"


def _resolve_safe(base_dir: Path, relative_path: str) -> Path:
    """解析安全路径，阻止路径越界。"""
    target = (base_dir / relative_path).resolve()
    base = base_dir.resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("Path escapes workspace")
    return target


def _is_text_file(path: Path) -> bool:
    """粗略判断是否文本文件（出现 NUL 字节通常是二进制）。"""
    try:
        raw = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in raw


def build_builtin_tools(runtime: ToolRuntime) -> list[BaseTool]:
    """构建工具列表。"""

    @tool("list")
    def list_files(relative_dir: str = ".") -> str:
        """列出目录内容。"""
        # 所有路径都必须先通过安全解析。
        directory = _resolve_safe(runtime.workspace, relative_dir)
        if not directory.exists() or not directory.is_dir():
            return f"Directory not found: {relative_dir}"
        items = sorted(p.name for p in directory.iterdir())
        return json.dumps({"dir": relative_dir, "items": items}, ensure_ascii=True)

    @tool("read")
    def read_file(file_path: str, offset: int = 0, limit: int = 500) -> str:
        """读取文本文件，按行分页。"""
        path = _resolve_safe(runtime.workspace, file_path)
        if not path.exists() or not path.is_file():
            return f"File not found: {file_path}"
        # 大文件直接拒绝，避免一次读取导致上下文爆炸。
        if path.stat().st_size > MAX_FILE_BYTES:
            return f"File too large: {file_path}"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = max(0, offset)
        end = min(len(lines), start + max(1, limit))
        body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(start, end))
        return _trim(body)

    @tool("write")
    def write_file(file_path: str, content: str) -> str:
        """写文件（覆盖）。"""
        path = _resolve_safe(runtime.workspace, file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {file_path} ({len(content)} chars)"

    @tool("edit")
    def edit_file(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        """编辑文件，字符串替换。"""
        path = _resolve_safe(runtime.workspace, file_path)
        if not path.exists() or not path.is_file():
            return f"File not found: {file_path}"
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            return "Target string not found"
        # 默认只替换第一个匹配，降低误操作风险。
        if replace_all:
            out = text.replace(old_string, new_string)
        else:
            out = text.replace(old_string, new_string, 1)
        path.write_text(out, encoding="utf-8")
        return f"Edited {file_path}"

    @tool("grep")
    def grep_files(
        pattern: str,
        relative_dir: str = ".",
        glob: str = "*",
        case_sensitive: bool = False,
        max_matches: int = 100,
    ) -> str:
        """在目录中搜索文本模式。"""
        root = _resolve_safe(runtime.workspace, relative_dir)
        if not root.exists() or not root.is_dir():
            return f"Directory not found: {relative_dir}"

        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        # 只在“可读文本文件”里搜索，跳过二进制和超大文件。
        matches: list[dict[str, str | int]] = []
        for path in root.rglob(glob):
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            if not _is_text_file(path):
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue

            for idx, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    rel = str(path.relative_to(runtime.workspace))
                    matches.append({"file": rel, "line": idx, "text": line[:500]})
                    if len(matches) >= max_matches:
                        return _trim(json.dumps({"matches": matches}, ensure_ascii=True))

        return _trim(json.dumps({"matches": matches}, ensure_ascii=True))

    @tool("exec")
    def exec_shell(command: str, timeout_ms: int = 30_000) -> str:
        """执行 shell 命令。"""
        # 通过 `sh -c` 执行，兼容常见命令链写法（例如管道/重定向）。
        try:
            proc = subprocess.run(
                ["sh", "-c", command],
                cwd=str(runtime.workspace),
                capture_output=True,
                text=True,
                timeout=max(1, timeout_ms) / 1000,
            )
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout_ms} ms"
        except Exception as exc:  # noqa: BLE001
            return f"Command failed: {exc}"

        output = f"[exit_code={proc.returncode}]\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        return _trim(output)

    @tool("memory_save")
    def memory_save(text: str) -> str:
        """保存记忆。"""
        mem_id = runtime.memory.add(text=text, source=f"tool:{runtime.session_key}")
        return json.dumps({"saved": True, "id": mem_id}, ensure_ascii=True)

    @tool("memory_search")
    def memory_search(query: str, limit: int = 5) -> str:
        """检索记忆。"""
        hits = runtime.memory.search(query=query, limit=max(1, min(limit, 20)))
        return _trim(json.dumps({"hits": hits}, ensure_ascii=True))

    @tool("memory_get")
    def memory_get(ids: list[str]) -> str:
        """按 ID 读取记忆。"""
        rows = runtime.memory.get(ids)
        return _trim(json.dumps({"items": rows}, ensure_ascii=True))

    @tool("sessions_spawn")
    async def sessions_spawn(task: str, label: str = "") -> str:
        """触发子代理执行任务并返回摘要。"""
        if runtime.spawn_subagent is None:
            return json.dumps({"ok": False, "error": "subagent disabled"}, ensure_ascii=True)
        result = await runtime.spawn_subagent(task, label or None)
        return _trim(json.dumps({"ok": True, **result}, ensure_ascii=True))

    return [
        list_files,
        read_file,
        write_file,
        edit_file,
        grep_files,
        exec_shell,
        memory_save,
        memory_search,
        memory_get,
        sessions_spawn,
    ]


def filter_tools_by_policy(tools: list[BaseTool], policy: str) -> list[BaseTool]:
    """工具策略过滤。"""
    p = (policy or "allow").lower()
    if p == "none":
        return []
    if p == "deny":
        # deny 模式下仅允许非破坏型工具，形成“只读能力集”。
        allow = {"list", "read", "grep", "memory_search", "memory_get"}
        return [t for t in tools if t.name in allow]
    return tools
