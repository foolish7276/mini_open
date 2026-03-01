"""命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys

from dotenv import load_dotenv

from .agent import OpenClawMiniAgent
from .config import Settings
from .types import RunRequest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenClaw-Mini (LangChain)")
    p.add_argument("prompt", nargs="?", default="请总结当前目录结构")
    p.add_argument("--session", default="default")
    p.add_argument("--max-turns", type=int, default=6)
    p.add_argument("--tool-policy", choices=["allow", "deny", "none"], default="allow")
    p.add_argument("--provider", choices=["openai", "anthropic"])
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--temperature", type=float)
    p.add_argument("--stream-events", action="store_true")
    return p.parse_args()


async def _event_printer(agent: OpenClawMiniAgent) -> None:
    async for event in agent.events.subscribe():
        et = event.type
        if et == "message_delta":
            print(event.payload.get("delta", ""), end="", flush=True)
            continue
        if et == "message_end":
            print()
            continue
        # 非文本事件打印一行摘要
        print(f"\n[event] {et}: {event.payload}")


async def _main() -> None:
    load_dotenv()
    args = parse_args()

    # CLI 参数优先级高于 .env。
    if args.provider:
        os.environ["MODEL_PROVIDER"] = args.provider
    if args.model:
        os.environ["MODEL_NAME"] = args.model
    if args.base_url:
        os.environ["MODEL_BASE_URL"] = args.base_url
    if args.temperature is not None:
        os.environ["MODEL_TEMPERATURE"] = str(args.temperature)

    settings = Settings()
    if settings.model_provider == "openai":
        effective_key = settings.model_api_key or os.getenv("OPENAI_API_KEY", "")
        if not effective_key:
            print(
                "缺少 OpenAI API Key。请在 .env 设置 MODEL_API_KEY 或 OPENAI_API_KEY。",
                file=sys.stderr,
            )
            raise SystemExit(2)
    if settings.model_provider == "anthropic":
        effective_key = settings.model_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not effective_key:
            print(
                "缺少 Anthropic API Key。请在 .env 设置 MODEL_API_KEY 或 ANTHROPIC_API_KEY。",
                file=sys.stderr,
            )
            raise SystemExit(2)

    agent = OpenClawMiniAgent(settings)

    printer_task: asyncio.Task[None] | None = None
    if args.stream_events:
        printer_task = asyncio.create_task(_event_printer(agent))

    try:
        req = RunRequest(
            session_key=args.session,
            user_text=args.prompt,
            max_turns=args.max_turns,
            tool_policy=args.tool_policy,
        )
        result = await agent.run(req)

        if not args.stream_events:
            text = result.final_text if result.final_text.strip() else "模型未返回可见文本。"
            print(text)
        else:
            print(f"\n\n[run_id] {result.run_id}")
            print(f"[session] {result.session_key}")
            print(f"[turns] {result.turns_used}")
            print(f"[tool_calls] {result.tool_calls}")
    finally:
        if printer_task is not None:
            printer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await printer_task


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
