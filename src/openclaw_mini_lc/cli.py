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


async def _event_printer(agent: OpenClawMiniAgent, state: dict[str, object]) -> None:
    async for event in agent.events.subscribe():
        et = event.type
        if et == "message_delta":
            state["saw_delta"] = True
            print(event.payload.get("delta", ""), end="", flush=True)
            continue
        if et == "message_end":
            state["last_message_text"] = str(event.payload.get("text", ""))
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
#      asyncio 是 Python 标准库里的“异步并发”框架（不是第三方包）。

#   你这行：

#   printer_task: asyncio.Task[None] | None = None

#   意思是：

#   - printer_task 变量类型是“一个异步任务”或 None
#   - 初始先设为 None
#   - 后面会用 asyncio.create_task(...) 启动后台任务（比如流式事件打印）然后赋值给它。
    stream_state: dict[str, object] = {"saw_delta": False, "last_message_text": ""}
    if args.stream_events:
        printer_task = asyncio.create_task(_event_printer(agent, stream_state))

    try:
        req = RunRequest(
            session_key=args.session,
            user_text=args.prompt,
            max_turns=args.max_turns,
            tool_policy=args.tool_policy,
        )
        # 防止模型网关长时间无响应时 CLI 看起来“卡住”。
        run_timeout_seconds = int(os.getenv("RUN_TIMEOUT_SECONDS", "180"))
        result = await asyncio.wait_for(agent.run(req), timeout=run_timeout_seconds)
        # 这个if是看是不是流式输出，流式输出就输出用了什么工具，第几轮调用
        if not args.stream_events:
            text = result.final_text if result.final_text.strip() else "模型未返回可见文本。"
            print(text)
        else:
            # 若由于订阅时序未拿到最后一条 message_end 文本，回退打印最终文本。
            # 例如：看到工具事件和 run_id，但正文缺失。
            last_message_text = str(stream_state.get("last_message_text", ""))
            text = result.final_text if result.final_text.strip() else "模型未返回可见文本。"
            if (not bool(stream_state.get("saw_delta", False))) or (text != last_message_text):
                text = result.final_text if result.final_text.strip() else "模型未返回可见文本。"
                print(text)
            print(f"\n\n[run_id] {result.run_id}")
            print(f"[session] {result.session_key}")
            print(f"[turns] {result.turns_used}")
            print(f"[tool_calls] {result.tool_calls}")
    except TimeoutError:
        print(
            "运行超时。可重试并加 --stream-events 观察进度；"
            "也可增大 RUN_TIMEOUT_SECONDS（默认 180）。",
            file=sys.stderr,
        )
        raise SystemExit(3)
    except RuntimeError as exc:
        err = str(exc)
        if "Connection error" in err:
            print(
                "模型连接失败（Connection error）。请检查：\n"
                "1) MODEL_BASE_URL 是否可达\n"
                "2) API Key 是否有效\n"
                "3) 代理/DNS 是否正常\n"
                "建议先运行：openclaw-mini-lc \"只回复ok\" --tool-policy none --max-turns 1 --stream-events",
                file=sys.stderr,
            )
            raise SystemExit(4)
        raise
    finally:
        if printer_task is not None:
            printer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await printer_task


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
