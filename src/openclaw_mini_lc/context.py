"""Token 感知的上下文构建与压缩。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .session import SessionStore

# 经验值：英文约 4 字符/Token，中文会更短；这里作为保守估算。
CHARS_PER_TOKEN_EST = 4

# 摘要提示词（尽量结构化，方便模型续写任务）。
COMPACTION_SYSTEM = """你是上下文压缩助手。请把历史对话压缩成可续作的结构化摘要。"""
COMPACTION_USER = """请总结以下历史对话，保留：
1) 用户目标与约束
2) 已完成改动
3) 进行中的工作
4) 下一步动作
5) 关键文件路径/函数名/错误信息

历史对话：
{history}
"""


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。"""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_EST)


@dataclass
class ContextBuildResult:
    """上下文构建结果。"""

    text: str
    tokens_before: int
    tokens_after: int
    dropped_messages: int
    compacted_summary: str | None


class ContextManager:
    """按 token 预算构建可喂给模型的上下文。"""

    def __init__(self, workspace: Path, session_store: SessionStore, max_context_chars: int) -> None:
        self.workspace = workspace
        self.session_store = session_store
        self.max_context_chars = max_context_chars

    def _bootstrap_files(self) -> list[str]:
        """读取启动上下文文件。"""
        names = [
            "AGENTS.md",
            "README.md",
            "CLAUDE.md",
            "BOOTSTRAP.md",
            "TOOLS.md",
            "MEMORY.md",
            "HEARTBEAT.md",
            "IDENTITY.md",
            "USER.md",
            "SOUL.md",
        ]
        chunks: list[str] = []
        for name in names:
            path = self.workspace / name
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
                chunks.append(f"# {name}\n{text}")
        return chunks

    def _history_lines(self, session_key: str, limit: int = 200) -> list[str]:
        """读取最近 message 历史并转成可读行。"""
        entries = self.session_store.read_messages(session_key, limit=limit)
        lines: list[str] = []
        for item in entries:
            role = item.get("role", "unknown")
            content = str(item.get("content", ""))
            lines.append(f"[{role}] {content}")
        return lines

    async def build(
        self,
        session_key: str,
        *,
        token_budget: int,
        summarize_fn: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> ContextBuildResult:
        """构建上下文。

        算法：
        1) 拼接 bootstrap + 历史。
        2) 若超预算，优先保留最近历史。
        3) 被裁掉历史可选做摘要，再插回上下文。
        """
        # A. 读取 bootstrap（规则文档）和历史消息。
        bootstrap_parts = self._bootstrap_files()
        history_lines = self._history_lines(session_key)

        bootstrap_text = "\n\n".join(bootstrap_parts)
        history_text = "\n".join(history_lines)
        full_text = "\n\n".join([bootstrap_text, history_text]).strip()

        # B. 先应用字符级硬上限（兜底保护，避免极端输入直接炸 prompt）。
        if len(full_text) > self.max_context_chars:
            full_text = full_text[-self.max_context_chars :]

        tokens_before = estimate_tokens(full_text)
        if tokens_before <= token_budget:
            return ContextBuildResult(
                text=full_text,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                dropped_messages=0,
                compacted_summary=None,
            )

        # C. token 超预算时：
        # 先保留 bootstrap，再从“最近历史”开始逆向装入，保证新信息优先。
        keep_lines: list[str] = []
        dropped_lines: list[str] = []

        base_tokens = estimate_tokens(bootstrap_text)
        remaining_budget = max(1, token_budget - base_tokens)

        used = 0
        for line in reversed(history_lines):
            cost = estimate_tokens(line + "\n")
            if used + cost <= remaining_budget:
                keep_lines.append(line)
                used += cost
            else:
                dropped_lines.append(line)

        keep_lines.reverse()
        dropped_lines.reverse()

        # D. 对被裁掉的旧历史做可选摘要，作为 compaction summary 回插。
        summary_text: str | None = None
        if dropped_lines and summarize_fn is not None:
            dropped_blob = "\n".join(dropped_lines)
            try:
                summary_text = await summarize_fn(COMPACTION_SYSTEM, COMPACTION_USER.format(history=dropped_blob))
            except Exception:
                # 摘要失败时回退为“无摘要”路径，不能让主流程失败。
                summary_text = None

        # E. 组合最终上下文：bootstrap + 摘要 + 保留历史。
        pieces: list[str] = []
        if bootstrap_text:
            pieces.append(bootstrap_text)
        if summary_text:
            pieces.append("[COMPACTION SUMMARY]\n" + summary_text)
        if keep_lines:
            pieces.append("\n".join(keep_lines))

        compacted = "\n\n".join(pieces).strip()
        tokens_after = estimate_tokens(compacted)

        return ContextBuildResult(
            text=compacted,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            dropped_messages=len(dropped_lines),
            compacted_summary=summary_text,
        )
