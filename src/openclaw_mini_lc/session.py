"""会话存储（JSONL）。"""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from .types import utc_now_iso

_ALLOWED = re.compile(r"[^a-zA-Z0-9_.-]+")


def normalize_session_key(session_key: str) -> str:
    """将会话键转换为安全文件名。"""
    cleaned = _ALLOWED.sub("_", session_key.strip())
    return cleaned[:80] or "default"


class SessionStore:
    """JSONL 持久化。

    文件中的记录结构：
    - message: 用户/助手/工具消息
    - compaction: 上下文压缩摘要
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # 每个 session 一个独立写锁，避免并发写把 JSONL 行打碎。
        self._locks: dict[str, threading.Lock] = {}

    def _path(self, session_key: str) -> Path:
        return self.base_dir / f"{normalize_session_key(session_key)}.jsonl"

    def _lock(self, session_key: str) -> threading.Lock:
        if session_key not in self._locks:
            self._locks[session_key] = threading.Lock()
        return self._locks[session_key]

    def append_message(
        self,
        session_key: str,
        role: str,
        content: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """追加消息记录，返回 entry_id。"""
        # entry_id 用于后续追踪（例如工具结果关联、调试定位）。
        entry_id = f"msg_{uuid.uuid4().hex}"
        payload: dict[str, Any] = {
            "type": "message",
            "id": entry_id,
            "ts": utc_now_iso(),
            "role": role,
            "content": content,
        }
        if extra:
            payload["extra"] = extra

        path = self._path(session_key)
        with self._lock(session_key):
            # JSONL 采用“每行一条记录”，追加写性能和容错都更好。
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")

        return entry_id

    def append_compaction(
        self,
        session_key: str,
        summary: str,
        dropped_messages: int,
        tokens_before: int,
        tokens_after: int,
    ) -> str:
        """追加 compaction 元信息记录。"""
        # compaction 作为独立记录落盘，方便观察上下文剪裁行为。
        entry_id = f"cmp_{uuid.uuid4().hex}"
        payload = {
            "type": "compaction",
            "id": entry_id,
            "ts": utc_now_iso(),
            "summary": summary,
            "dropped_messages": dropped_messages,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        }

        path = self._path(session_key)
        with self._lock(session_key):
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")

        return entry_id

    def read_entries(self, session_key: str) -> list[dict[str, Any]]:
        """读取原始记录（message + compaction）。"""
        path = self._path(session_key)
        if not path.exists():
            return []

        rows: list[dict[str, Any]] = []
        with self._lock(session_key):
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        # 跳过损坏行，保证整体可用。
                        # 这是 JSONL 相对单个 JSON 文档的一个重要优势。
                        continue
        return rows

    def read_messages(self, session_key: str, limit: int | None = None) -> list[dict[str, Any]]:
        """只读取 message 记录，按时间顺序返回。"""
        entries = [e for e in self.read_entries(session_key) if e.get("type") == "message"]
        if limit is None:
            return entries
        return entries[-limit:]
