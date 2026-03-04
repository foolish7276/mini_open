# OpenClaw-Mini (LangChain Reproduction)

这是一个用 Python + LangChain 来实现 `openclaw-mini` 架构的项目。

## 当前能力

### 核心层
- 双层 Agent Loop（outer follow-up + inner tool execution）
- 事件流（类型化事件，支持 `turn/message/tool/steering/subagent`）
- Session JSONL 持久化（含 compaction 记录）
- Token 感知上下文裁剪 + 历史摘要压缩（compaction）
- Provider 适配（OpenAI / Anthropic）

### 工具体系
- `list` / `read` / `write` / `edit` / `grep` / `exec`
- `memory_save` / `memory_search` / `memory_get`
- `sessions_spawn`（子代理）
- 工具策略：`allow` / `deny` / `none`

### 扩展层
- Memory：关键词重叠 + 时间衰减检索
- Skills：`.codex/skills/**/SKILL.md` 文本触发
- Heartbeat：按 session 触发节流

### 工程层
- Session 串行 + 全局并发上限调度器
- 路径安全检查（防越界）
- 输出截断与超时保护（exec/read/grep）
- tool 执行中 steering 中断与剩余工具跳过

## 快速开始

1. 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 配置环境变量

```bash
cp .env.example .env
# 推荐填写 MODEL_API_KEY（通用）
# 也可按 provider 分别填写 OPENAI_API_KEY / ANTHROPIC_API_KEY
```

3. 运行

```bash
openclaw-mini-lc "请扫描项目并给出改进建议" --session demo --tool-policy allow
```

4. 观察事件流（可选）

```bash
openclaw-mini-lc "列出当前目录" --stream-events
```

5. 使用代理 / 自定义网关（可选）

```bash
openclaw-mini-lc "你好" --provider openai --model gpt-4o-mini --base-url https://your-proxy/v1
```

## 源项目模块对照

### 核心层（必读）

| 源项目模块 | 源项目文件 | 当前 Python/LangChain 对应文件 | 说明 |
|---|---|---|---|
| Agent | `agent.ts` | `src/openclaw_mini_lc/agent.py` | 入口、事件发射、主调度 |
| Agent Loop | `agent-loop.ts` | `src/openclaw_mini_lc/agent.py` | 双层循环（outer follow-up / inner tools+steering）合并在 `agent.py` |
| EventStream | `agent-events.ts` | `src/openclaw_mini_lc/events.py` + `src/openclaw_mini_lc/types.py` | 事件总线实现 + 事件类型定义 |
| Session | `session.ts` | `src/openclaw_mini_lc/session.py` | JSONL 持久化、message/compaction 记录 |
| Context Loader | `context/loader.ts` | `src/openclaw_mini_lc/context.py` | bootstrap 文件加载 |
| Pruning | `context/pruning.ts` | `src/openclaw_mini_lc/context.py` | token 预算驱动的历史裁剪 |
| Compaction | `context/compaction.ts` | `src/openclaw_mini_lc/context.py` + `src/openclaw_mini_lc/provider.py` | 历史摘要压缩（使用同模型补全） |
| Tools | `tools/*.ts` | `src/openclaw_mini_lc/tools.py` | 工具抽象 + 内置工具集合 |
| Provider | `provider/*.ts` | `src/openclaw_mini_lc/provider.py` | 多模型适配（当前接 OpenAI/Anthropic） |

### 扩展层（选读）

| 源项目模块 | 源项目文件 | 当前 Python/LangChain 对应文件 | 说明 |
|---|---|---|---|
| Memory | `memory.ts` | `src/openclaw_mini_lc/memory.py` | 长期记忆、关键词检索 + 时间衰减 |
| Skills | `skills.ts` | `src/openclaw_mini_lc/skills.py` | `SKILL.md` 文本匹配触发 |
| Heartbeat | `heartbeat.ts` | `src/openclaw_mini_lc/heartbeat.py` | 按 session 触发节流 |

### 工程层（可跳过）

| 源项目模块 | 源项目文件 | 当前 Python/LangChain 对应文件 | 说明 |
|---|---|---|---|
| Session Key | `session-key.ts` | `src/openclaw_mini_lc/session.py` | `normalize_session_key()` 会话键规范化 |
| Tool Policy | `tool-policy.ts` | `src/openclaw_mini_lc/tools.py` | `filter_tools_by_policy()` 实现 `allow/deny/none` |
| Command Queue | `command-queue.ts` | `src/openclaw_mini_lc/queue.py` | session 串行 + global 并发限制 |
| Tool Result Guard | `session-tool-result-guard.ts` | `src/openclaw_mini_lc/agent.py` | 每个 tool_call 都会写回 `ToolMessage`，避免缺失 tool_result |
| Context Window Guard | `context-window-guard.ts` | `src/openclaw_mini_lc/context.py` + `src/openclaw_mini_lc/config.py` | token 预算与上下文上限控制 |
| Sandbox Paths | `sandbox-paths.ts` | `src/openclaw_mini_lc/tools.py` | `_resolve_safe()` 路径安全检查 |

## 说明

- 该版本已覆盖 openclaw-mini 的主要架构思想和运行机制。
- 真正 token 级 LLM streaming 事件（provider 原生 chunk 逐 token 转发）在不同模型 SDK 上行为不一致；当前 `message_delta` 为文本块级事件，属于可用替代实现。
