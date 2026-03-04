# Interview Guide: OpenClaw-Mini (LangChain, Python)

## 1. 项目一句话定位
这是一个用 Python + LangChain 复现 openclaw-mini 架构的 AI Coding Agent，重点实现了双层 Agent Loop、工具调用闭环、会话持久化、上下文压缩、记忆检索和并发调度。

## 2. 你在面试里先讲什么（2分钟版本）
我做了一个可运行的 Agent 框架，不是简单的 chat demo。它有三层能力。
第一层是核心执行层：主循环分 outer/inner 两层，outer 处理 follow-up，inner 处理 tool calls 和 steering 中断。
第二层是扩展能力：长期记忆、技能触发、heartbeat。
第三层是工程保护：session 串行、全局并发限制、路径安全、输出截断和上下文预算。
这个项目的重点不是模型 prompt，而是 Agent 运行时系统设计。

## 3. 架构总览
### 3.1 核心层
- Agent 主入口: `src/openclaw_mini_lc/agent.py`
- Loop 策略: 同文件内 outer/inner 双层循环
- EventStream: `src/openclaw_mini_lc/events.py`
- Session Store(JSONL): `src/openclaw_mini_lc/session.py`
- Context(bootstrap + pruning + compaction): `src/openclaw_mini_lc/context.py`
- Provider 适配: `src/openclaw_mini_lc/provider.py`
- Tools: `src/openclaw_mini_lc/tools.py`

### 3.2 扩展层
- Memory: `src/openclaw_mini_lc/memory.py`
- Skills: `src/openclaw_mini_lc/skills.py`
- Heartbeat: `src/openclaw_mini_lc/heartbeat.py`

### 3.3 工程层
- 调度器（session 串行 + global 并发）: `src/openclaw_mini_lc/queue.py`
- 类型契约与事件枚举: `src/openclaw_mini_lc/types.py`
- CLI 与运行入口: `src/openclaw_mini_lc/cli.py`

## 4. 一次请求的完整时序（必须会讲）
1. CLI 读 `.env` 和参数，构造 `Settings`、`RunRequest`。
2. `Agent.run()` 进入 `CommandScheduler`。
3. 记录用户输入到 session 和 memory。
4. `ContextManager.build()` 拼接 bootstrap + 历史；超预算则裁剪并可选摘要压缩。
5. 构建工具集并按 `tool_policy` 过滤。
6. 模型推理（带重试）。
7. 如果有 `tool_calls`，按顺序执行工具，把 `ToolMessage` 回灌给模型。
8. 每次工具后检查 steering，若有新用户消息，跳过后续工具并切回 outer loop。
9. 无工具调用时返回 final_text；达到上限时返回 truncation 结果。
10. 全程事件通过 EventStream 对外广播。

## 5. 关键设计点（面试加分）
### 5.1 为什么要双层循环
- Outer loop 负责对“新用户插话/后续消息”的可中断处理。
- Inner loop 负责“单轮内模型 + 工具”的执行闭环。
- 这样可以在 tool-heavy 场景保持可控性和可解释性。

### 5.2 为什么 session 要 JSONL
- 追加写简单，吞吐稳定。
- 单行损坏可容忍，恢复成本低。
- 便于审计和回放。

### 5.3 为什么要 token 预算 compaction
- 长会话必然超窗。
- 先保留最近历史，再把旧历史摘要化，能平衡连续性和成本。

### 5.4 为什么要 session 串行 + global 并发
- 同 session 并发会导致历史错序。
- 完全串行又浪费机器并行能力。
- 两层并发控制是工程上常见折中。

### 5.5 为什么工具要统一安全层
- 所有路径都先走 `_resolve_safe`。
- 所有输出都有统一截断上限。
- `exec` 有 timeout，防止悬挂。

## 6. 当前工具集与策略
工具名：`list/read/write/edit/grep/exec/memory_save/memory_search/memory_get/sessions_spawn`

`tool_policy`:
- `allow`: 全工具可用。
- `deny`: 仅只读工具（`list/read/grep/memory_search/memory_get`）。
- `none`: 禁用工具，仅文本推理。

## 7. 已解决的坑（可以当“排障经验”讲）
1. `.env` 读取时机 bug。
- 问题：配置在 import 时读取，导致 `load_dotenv()` 后无效。
- 解决：`Settings` 改成 `default_factory`，实例化时读取 env。

2. `base_url` 适配不一致。
- 问题：不同 LangChain 版本参数名差异。
- 解决：provider 层兼容 `base_url/openai_api_base` 等参数分支。

3. 模型空白输出。
- 问题：工具回合后某些模型返回空白文本。
- 解决：增加 final text normalize fallback，避免 CLI 空打印。

## 8. 已知限制（主动说更专业）
1. `message_delta` 是块级，不是 provider 原生逐 token streaming。
2. token 估算用字符近似（`chars/4`），不是精确 tokenizer。
3. Skills 触发是轻量文本匹配，不是完整 frontmatter policy 引擎。
4. 不是源码逐行 1:1 翻译，而是架构高保真复现。

## 9. 如果面试官问“还能怎么优化”
1. 引入精确 tokenizer（如 tiktoken）替代粗估。
2. provider 层做真正 token-level streaming 统一抽象。
3. 增加 tracing/metrics（每 turn token、工具耗时、重试次数）。
4. tool result 去重与 dead-loop 检测，提升收敛稳定性。
5. Memory 升级到向量索引/BM25 混合检索。
6. 单测覆盖核心流程：loop、context、tools、安全边界。

## 10. 高频面试问答模板
Q: 你这个项目和普通 ChatBot 最大区别是什么？
A: 我实现的是 Agent 运行时，不只是 prompt。它有工具调用闭环、上下文压缩、会话持久化、并发调度和事件总线。

Q: 你如何保证工具调用安全？
A: 路径强制在 workspace 内，`exec` 超时限制，输出截断，工具策略可切只读或禁用。

Q: 为什么不用单轮 ReAct，而要双层循环？
A: 单轮很难处理“工具执行中用户插话”。双层 loop 能在每个工具后检查 steering 并中断后续调用。

Q: 长上下文如何处理？
A: token 预算裁剪，优先保留最近历史，旧历史摘要化并回插。压缩元信息单独落盘可审计。

Q: 如何避免并发写坏会话？
A: session 级锁 + JSONL 逐行写入；跨 session 用 global semaphore 控并发。

## 11. 你要背的文件清单
- `src/openclaw_mini_lc/agent.py`
- `src/openclaw_mini_lc/context.py`
- `src/openclaw_mini_lc/tools.py`
- `src/openclaw_mini_lc/session.py`
- `src/openclaw_mini_lc/provider.py`
- `src/openclaw_mini_lc/queue.py`
- `README.md`

## 12. 面试前 5 分钟自检
1. 能讲清一次请求时序（第4节）。
2. 能讲清 3 个设计取舍（第5节）。
3. 能讲清 2 个排障案例（第7节）。
4. 能讲清 3 个已知限制和后续优化（第8、9节）。
