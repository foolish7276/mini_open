"""最小示例：跑一次 agent。"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from openclaw_mini_lc import OpenClawMiniAgent, RunRequest, Settings


async def main() -> None:
    load_dotenv()
    agent = OpenClawMiniAgent(Settings())
    result = await agent.run(
        RunRequest(
            session_key="demo",
            user_text="请读取 README.md 并总结前三条要点",
            max_turns=6,
            tool_policy="allow",
        )
    )

    print("run_id:", result.run_id)
    print("session:", result.session_key)
    print("turns:", result.turns_used)
    print("tool_calls:", result.tool_calls)
    print("final_text:\n", result.final_text)


if __name__ == "__main__":
    asyncio.run(main())
