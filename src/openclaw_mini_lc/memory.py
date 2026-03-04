"""长期记忆仓库。"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 简单分词规则：提取长度>=2的英文/数字/下划线/短横线 token。
# 用于关键词重叠检索（对中文语义召回能力较弱）。
TOKEN = re.compile(r"[A-Za-z0-9_\-]{2,}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """JSON 文件版记忆系统。

    每条记忆结构：
    - id: 唯一 ID
    - ts: 时间
    - source: 来源
    - text: 内容
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        normalized: list[dict[str, Any]] = []
        for row in rows:
            # 兼容历史/异常数据：若缺少 id，补一个可追踪 ID，保证后续 get/search 可用。
            if "id" not in row:
                row["id"] = f"mem_{uuid.uuid4().hex[:12]}"
            normalized.append(row)
        return normalized

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")

    def add(self, text: str, source: str) -> str:
        rows = self._load()
        mem_id = f"mem_{uuid.uuid4().hex[:12]}"
        rows.append({"id": mem_id, "ts": _utc_now().isoformat(), "source": source, "text": text})
        # 内存上限控制：仅保留最近 1000 条，防止 memory.json 无限增长。
        self._save(rows[-1000:])
        return mem_id

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._load()
        if not rows:
            return []

        # query token 计数，用于与每条记忆做“词频重叠”打分。
        q_counter = Counter(TOKEN.findall(query.lower()))
        now = _utc_now()
        scored: list[tuple[float, dict[str, Any]]] = []

        for row in rows:
            text = str(row.get("text", ""))
            c = Counter(TOKEN.findall(text.lower()))
            # 关键词重叠分：每个 token 按 min(query_count, text_count) 累加。
            overlap = sum(min(q_counter[k], c[k]) for k in q_counter)
            if overlap <= 0:
                continue

            try:
                ts = datetime.fromisoformat(str(row["ts"]))
            except Exception:
                ts = now

            age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
            # 时间衰减：越新的记忆分数越高，30 天为一个衰减尺度。
            decay = math.exp(-age_days / 30.0)
            score = overlap * decay

            hit = dict(row)
            hit["score"] = round(score, 6)
            scored.append((score, hit))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def get(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = self._load()
        idset = set(ids)
        return [row for row in rows if str(row.get("id")) in idset]

    def recall(self, query: str, limit: int = 5) -> list[str]:
        return [str(item.get("text", "")) for item in self.search(query, limit=limit)]
