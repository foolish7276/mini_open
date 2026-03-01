"""Heartbeat（主动唤醒）调度。

当前实现是轻量版：
- 每个 session 记录上次触发时间。
- 到达间隔后才允许再次触发。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class HeartbeatPolicy:
    """Heartbeat 配置。"""

    every_minutes: int = 30


class HeartbeatScheduler:
    """按 session 控制心跳触发频率。"""

    def __init__(self, policy: HeartbeatPolicy) -> None:
        self.policy = policy
        self._last_by_session: dict[str, datetime] = {}

    def should_trigger(self, session_key: str) -> bool:
        """判断某 session 当前是否应触发心跳。"""
        now = datetime.now(timezone.utc)
        last = self._last_by_session.get(session_key)

        if last is None or now - last >= timedelta(minutes=self.policy.every_minutes):
            self._last_by_session[session_key] = now
            return True

        return False
