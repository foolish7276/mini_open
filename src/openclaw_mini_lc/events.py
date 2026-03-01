"""异步事件总线。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .types import AgentEvent


class EventStream:
    """进程内多订阅者事件流。

    设计点：
    - 每个订阅者独立队列，互不干扰。
    - 发布时不阻塞主流程；慢订阅者队列满了会被清理。
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[AgentEvent]] = set()

    async def emit(self, event: AgentEvent) -> None:
        dead: list[asyncio.Queue[AgentEvent]] = []
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(queue)

        for queue in dead:
            self._queues.discard(queue)

    async def subscribe(self) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=256)
        self._queues.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.discard(queue)
