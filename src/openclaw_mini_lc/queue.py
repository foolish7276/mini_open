"""运行调度器（session 串行 + global 并发上限）。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CommandScheduler:
    """调度策略：

    - 同 session：严格串行（防止会话日志交错）。
    - 跨 session：受全局信号量限制（防止过量并发）。
    """

    def __init__(self, max_global_concurrency: int = 4) -> None:
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._global_sem = asyncio.Semaphore(max(1, max_global_concurrency))

    def _session_lock(self, session_key: str) -> asyncio.Lock:
        if session_key not in self._session_locks:
            self._session_locks[session_key] = asyncio.Lock()
        return self._session_locks[session_key]

    async def run(self, session_key: str, job_factory: Callable[[], Awaitable[T]]) -> T:
        """执行一个任务，并套用两层并发控制。"""
        # 全局信号量先获取：先控制“总体并发”。
        async with self._global_sem:
            # 再获取 session 锁：同会话内严格串行。
            async with self._session_lock(session_key):
                return await job_factory()
