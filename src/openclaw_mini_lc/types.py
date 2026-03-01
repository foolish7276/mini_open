"""项目核心类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# 统一事件类型集合，便于调用方按字符串分支处理。
EventType = Literal[
    "agent_start",
    "agent_end",
    "agent_error",
    "run_started",
    "run_finished",
    "turn_start",
    "turn_end",
    "message_start",
    "message_delta",
    "message_end",
    "thinking_delta",
    "tool_execution_start",
    "tool_execution_end",
    "tool_call",
    "tool_result",
    "tool_skipped",
    "steering",
    "compaction",
    "retry",
    "subagent_start",
    "subagent_end",
    "subagent_error",
]

ToolPolicy = Literal["allow", "deny", "none"]


def utc_now_iso() -> str:
    """返回 UTC 时间（ISO8601）。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentEvent:
    """事件流中的一条事件。"""

    type: EventType | str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class RunRequest:
    """一次 `agent.run()` 请求。"""

    session_key: str
    user_text: str
    max_turns: int = 6
    tool_policy: ToolPolicy = "allow"


@dataclass
class RunResult:
    """一次 `agent.run()` 的结果。"""

    run_id: str
    session_key: str
    final_text: str
    turns_used: int
    tool_calls: int
