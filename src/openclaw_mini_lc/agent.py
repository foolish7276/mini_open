"""Agent 主流程。

这个文件是整个项目的核心，基本对齐 openclaw-mini 的运行骨架：
1) 外层循环：处理 follow-up / steering 注入。
2) 内层循环：处理当前轮模型输出和工具调用。
3) 每个工具调用后都检查 steering，必要时跳过后续工具。
4) tool_result 回灌给模型，再进入下一轮推理。

说明：
- 这里用 LangChain 的 `AIMessage.tool_calls` 做工具调度。
- 事件流以 `AgentEvent` 广播，CLI 或上层可订阅。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from .config import Settings
from .context import ContextManager
from .events import EventStream
from .heartbeat import HeartbeatPolicy, HeartbeatScheduler
from .memory import MemoryStore
from .provider import build_chat_model, complete_text
from .queue import CommandScheduler
from .session import SessionStore
from .skills import SkillLoader
from .tools import ToolRuntime, build_builtin_tools, filter_tools_by_policy
from .types import AgentEvent, RunRequest, RunResult

SYSTEM_PROMPT = """You are OpenClaw-Mini-LC, a coding assistant.
- Be concise and action oriented.
- Read before edit.
- Use tools when needed.
- If a tool fails, explain and continue.
"""

RATE_LIMIT_HINTS = ["rate limit", "too many requests", "429", "quota", "resource exhausted"]


@dataclass
class _RunEnv:
    """单次 run 的运行时封装。

    为什么单独抽一层：
    - 避免 `_run_core` 局部变量过多。
    - 便于后续插拔更多运行态对象（例如 tracing、metrics）。
    """

    run_id: str
    tools: list[BaseTool]
    llm_with_tools: object
    tool_map: dict[str, BaseTool]


class OpenClawMiniAgent:
    """LangChain 版 OpenClaw-Mini 高保真实现。

    关键能力：
    - session 串行 + 全局并发控制
    - token 预算上下文 compaction
    - 工具调用、steering 中断、子代理
    - 事件流（turn/message/tool/subagent/retry）
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()

        self.events = EventStream()
        self.session_store = SessionStore(self.settings.sessions_dir)
        self.context = ContextManager(
            workspace=self.settings.project_root,
            session_store=self.session_store,
            max_context_chars=self.settings.max_context_chars,
        )
        self.memory = MemoryStore(self.settings.memory_dir / "memory.json")
        self.heartbeat = HeartbeatScheduler(HeartbeatPolicy(self.settings.heartbeat_minutes))
        self.skills = SkillLoader(self.settings.project_root)

        self.model = build_chat_model(
            provider=self.settings.model_provider,
            model=self.settings.model_name,
            temperature=self.settings.temperature,
            base_url=self.settings.model_base_url,
            api_key=self.settings.model_api_key,
        )

        self.scheduler = CommandScheduler(max_global_concurrency=self.settings.max_concurrent_runs)
        self._steering_queues: dict[str, list[str]] = {}

    def steer(self, session_key: str, user_text: str) -> None:
        """插入 steering 消息（工具执行期间可打断后续工具）。"""
        if session_key not in self._steering_queues:
            self._steering_queues[session_key] = []
        self._steering_queues[session_key].append(user_text)

    def _drain_steering(self, session_key: str) -> list[str]:
        """取出并清空某个 session 的 steering 队列。"""
        queue = self._steering_queues.get(session_key)
        if not queue:
            return []
        drained = queue[:]
        queue.clear()
        return drained

    async def _emit(self, event_type: str, **payload: object) -> None:
        """统一事件发射入口，避免重复构造 AgentEvent。"""
        await self.events.emit(AgentEvent(type=event_type, payload=dict(payload)))

    @staticmethod
    def _to_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content)

    @staticmethod
    def _normalize_final_text(text: str) -> str:
        """将最终输出规范化。

        某些模型在工具循环中可能返回仅空白字符，CLI 看起来像“无输出”。
        这里统一兜底为可读提示，避免静默空结果。
        """
        if text.strip():
            return text
        return "模型未返回可见文本（可能持续在工具模式中），可加大 max-turns 或切换模型。"

    async def _emit_deltas(self, text: str, chunk_size: int = 120) -> None:
        """发送 message_delta 事件（按块切分）。"""
        if not text:
            return
        for i in range(0, len(text), chunk_size):
            await self._emit("message_delta", delta=text[i : i + chunk_size])

    async def _summarize_for_compaction(self, system: str, user: str) -> str:
        """给 ContextManager 传入的摘要回调。"""
        return await complete_text(self.model, system=system, user=user)

    def _build_system_prompt(self, req: RunRequest, tools: list[BaseTool], context_text: str) -> str:
        """构建系统提示，注入技能/工具/上下文信息。"""
        tool_names = ", ".join(sorted(t.name for t in tools)) or "(none)"
        skills = self.skills.relevant_skills(req.user_text)

        parts = [SYSTEM_PROMPT]
        parts.append(f"Available tools: {tool_names}")

        if skills:
            skill_names = ", ".join(name for name, _ in skills)
            parts.append(f"Matched skills: {skill_names}. Read and follow the most relevant one.")
            for name, content in skills[:2]:
                parts.append(f"[SKILL:{name}]\n{content}")

        parts.append("[PROJECT CONTEXT]\n" + context_text)
        return "\n\n".join(parts)

    async def _invoke_with_retry(self, llm: object, messages: list[object], run_id: str) -> AIMessage:
        """带限速重试的模型调用。"""
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                out = await llm.ainvoke(messages)  # type: ignore[attr-defined]
                if isinstance(out, AIMessage):
                    return out
                return AIMessage(content=str(out))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                text = str(exc).lower()
                retryable = any(h in text for h in RATE_LIMIT_HINTS)
                # 只有“看起来像限速/配额问题”才做指数退避重试。
                if (not retryable) or attempt >= 3:
                    break
                delay = 0.3 * (2 ** (attempt - 1))
                await self._emit("retry", run_id=run_id, attempt=attempt, delay=delay, error=str(exc))
                await asyncio.sleep(delay)

        raise RuntimeError(str(last_error) if last_error else "LLM call failed")

    async def run(self, req: RunRequest) -> RunResult:
        """公开入口：套上 session/global 调度。"""
        return await self.scheduler.run(req.session_key, lambda: self._run_core(req, depth=0))

    async def _run_core(self, req: RunRequest, depth: int) -> RunResult:
        """核心执行函数。

        depth > 0 表示当前是子代理调用链的一部分。
        """
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        await self._emit("run_started", run_id=run_id, session_key=req.session_key)
        await self._emit(
            "agent_start",
            run_id=run_id,
            session_key=req.session_key,
            model=self.settings.model_name,
            depth=depth,
        )

        # 记录首条用户输入到 session + memory。
        self.session_store.append_message(req.session_key, "user", req.user_text)
        self.memory.add(req.user_text, source=f"user:{req.session_key}")

        async def spawn_subagent(task: str, label: str | None) -> dict[str, str]:
            """子代理触发器。

            这里直接复用 `_run_core`，只是切换到独立 child session。
            """
            child_session = f"{req.session_key}.sub.{uuid.uuid4().hex[:8]}"
            await self._emit(
                "subagent_start",
                parent_run_id=run_id,
                child_session=child_session,
                label=label or "",
            )

            # 子代理默认限制更紧，避免递归爆炸和长尾成本。
            child_req = RunRequest(
                session_key=child_session,
                user_text=task,
                max_turns=max(2, min(req.max_turns, 4)),
                tool_policy=req.tool_policy,
            )

            try:
                child_result = await self._run_core(child_req, depth=depth + 1)
                await self._emit(
                    "subagent_end",
                    parent_run_id=run_id,
                    child_session=child_session,
                    label=label or "",
                    final_text=child_result.final_text,
                )
                return {
                    "session_key": child_session,
                    "run_id": child_result.run_id,
                    "summary": child_result.final_text[:1200],
                }
            except Exception as exc:  # noqa: BLE001
                await self._emit(
                    "subagent_error",
                    parent_run_id=run_id,
                    child_session=child_session,
                    label=label or "",
                    error=str(exc),
                )
                raise

        # 每次 run 都创建一份 ToolRuntime，确保 session_key 正确注入工具层。
        runtime = ToolRuntime(
            workspace=self.settings.project_root,
            memory=self.memory,
            session_key=req.session_key,
            spawn_subagent=spawn_subagent if depth < self.settings.max_subagent_depth else None,
        )

        # 先构建全量工具，再按 tool_policy 过滤成运行态工具集。
        all_tools = build_builtin_tools(runtime)
        tools = filter_tools_by_policy(all_tools, req.tool_policy)
        llm_with_tools = self.model.bind_tools(tools) if tools else self.model
        env = _RunEnv(
            run_id=run_id,
            tools=tools,
            llm_with_tools=llm_with_tools,
            tool_map={t.name: t for t in tools},
        )

        # context 构建 + compaction
        # 若触发 compaction，会写入单独的 compaction entry，便于排查上下文问题。
        context_result = await self.context.build(
            req.session_key,
            token_budget=self.settings.context_window_tokens,
            summarize_fn=self._summarize_for_compaction,
        )
        if context_result.dropped_messages > 0:
            self.session_store.append_compaction(
                req.session_key,
                summary=context_result.compacted_summary or "",
                dropped_messages=context_result.dropped_messages,
                tokens_before=context_result.tokens_before,
                tokens_after=context_result.tokens_after,
            )
            await self._emit(
                "compaction",
                run_id=run_id,
                dropped_messages=context_result.dropped_messages,
                tokens_before=context_result.tokens_before,
                tokens_after=context_result.tokens_after,
            )

        # 把 memory 检索结果附加到系统上下文，提升连续对话一致性。
        recalled = self.memory.search(req.user_text, limit=4)
        memory_text = json.dumps({"memory_hits": recalled}, ensure_ascii=True)
        system_prompt = self._build_system_prompt(req, tools, context_result.text + "\n\n" + memory_text)

        messages: list[object] = [SystemMessage(content=system_prompt)]

        # outer-loop 状态：
        # - pending_user_texts: 下一轮要注入的 user/steering 消息
        # - need_followup_turn: 工具执行后需要继续让模型“消化 tool_result”
        pending_user_texts = [req.user_text]
        need_followup_turn = False
        turns_used = 0
        tool_calls = 0
        final_text = ""

        while turns_used < req.max_turns:
            # 外层循环退出条件：没有待处理用户消息，且不需要 follow-up。
            if not pending_user_texts and not need_followup_turn:
                break

            # 1) 注入本轮要处理的 user 消息（含 steering）。
            for user_text in pending_user_texts:
                if user_text:
                    messages.append(HumanMessage(content=user_text))
                    if user_text != req.user_text:
                        self.session_store.append_message(
                            req.session_key,
                            "user",
                            user_text,
                            extra={"source": "steering"},
                        )
                        self.memory.add(user_text, source=f"steering:{req.session_key}")

            pending_user_texts = []
            need_followup_turn = False

            turns_used += 1
            await self._emit("turn_start", run_id=run_id, turn=turns_used)
            await self._emit("message_start", run_id=run_id, turn=turns_used)

            # 2) 模型推理。
            ai = await self._invoke_with_retry(env.llm_with_tools, messages, run_id=run_id)
            messages.append(ai)

            ai_text = self._to_text(ai.content)
            self.session_store.append_message(req.session_key, "assistant", ai_text)
            await self._emit_deltas(ai_text)
            await self._emit("message_end", run_id=run_id, turn=turns_used, text=ai_text)
            final_text = ai_text

            # 3) 解析 tool_calls。没有工具调用则可能直接收敛。
            tool_calls_in_turn = list(getattr(ai, "tool_calls", []) or [])
            if not tool_calls_in_turn:
                steering = self._drain_steering(req.session_key)
                if steering:
                    pending_user_texts.extend(steering)
                    await self._emit("steering", run_id=run_id, pending=len(steering))
                    await self._emit("turn_end", run_id=run_id, turn=turns_used)
                    continue

                await self._emit("turn_end", run_id=run_id, turn=turns_used)
                await self._emit("agent_end", run_id=run_id, session_key=req.session_key)
                final_text = self._normalize_final_text(final_text)
                await self._emit("run_finished", run_id=run_id, final=final_text)
                return RunResult(
                    run_id=run_id,
                    session_key=req.session_key,
                    final_text=final_text,
                    turns_used=turns_used,
                    tool_calls=tool_calls,
                )

            # 4) 内层工具循环：按模型给定顺序串行执行。
            aborted_by_steering = False
            for index, call in enumerate(tool_calls_in_turn):
                name = str(call.get("name", ""))
                args = call.get("args", {})
                call_id = str(call.get("id", f"tool_{turns_used}_{index}"))

                await self._emit("tool_call", run_id=run_id, turn=turns_used, id=call_id, name=name, args=args)
                await self._emit(
                    "tool_execution_start",
                    run_id=run_id,
                    turn=turns_used,
                    id=call_id,
                    name=name,
                    args=args,
                )

                # 4.1 执行工具
                tool = env.tool_map.get(name)
                if tool is None:
                    result = f"Tool not found: {name}"
                    is_error = True
                else:
                    try:
                        tool_out = await tool.ainvoke(args)
                        if isinstance(tool_out, str):
                            result = tool_out
                        else:
                            result = json.dumps(tool_out, ensure_ascii=True)
                        is_error = False
                    except Exception as exc:  # noqa: BLE001
                        result = f"Tool {name} failed: {exc}"
                        is_error = True

                tool_calls += 1
                result = result[:30_000]

                # 4.2 将 tool_result 回灌到消息序列，供下一次 LLM 推理使用。
                messages.append(
                    ToolMessage(
                        content=json.dumps({"result": result}, ensure_ascii=True),
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                self.session_store.append_message(
                    req.session_key,
                    "tool",
                    result,
                    extra={"tool": name, "id": call_id, "is_error": is_error},
                )
                await self._emit(
                    "tool_result",
                    run_id=run_id,
                    turn=turns_used,
                    id=call_id,
                    name=name,
                    is_error=is_error,
                )
                await self._emit(
                    "tool_execution_end",
                    run_id=run_id,
                    turn=turns_used,
                    id=call_id,
                    name=name,
                    is_error=is_error,
                )

                # 4.3 每次工具后检查 steering。
                # 若有新用户消息，跳过本轮剩余工具并立刻切回 outer-loop。
                steering = self._drain_steering(req.session_key)
                if steering:
                    pending_user_texts.extend(steering)
                    aborted_by_steering = True
                    await self._emit("steering", run_id=run_id, pending=len(steering))
                    for skipped in tool_calls_in_turn[index + 1 :]:
                        await self._emit(
                            "tool_skipped",
                            run_id=run_id,
                            turn=turns_used,
                            id=str(skipped.get("id", "")),
                            name=str(skipped.get("name", "")),
                            reason="steering_message_queued",
                        )
                    break

            await self._emit("turn_end", run_id=run_id, turn=turns_used)

            if aborted_by_steering:
                continue

            # 5) 本轮工具跑完，开启 follow-up turn，让模型基于 tool_result 继续。
            need_followup_turn = True

        # 达到 max_turns 仍未收敛，返回截断结果。
        await self._emit("agent_end", run_id=run_id, session_key=req.session_key)
        final_text = self._normalize_final_text(final_text)
        await self._emit("run_finished", run_id=run_id, final=final_text, truncated=True)
        return RunResult(
            run_id=run_id,
            session_key=req.session_key,
            final_text=final_text,
            turns_used=turns_used,
            tool_calls=tool_calls,
        )

    def heartbeat_prompt(self, session_key: str) -> str | None:
        if not self.heartbeat.should_trigger(session_key):
            return None
        return "Please proactively check if there are pending tasks worth continuing."
