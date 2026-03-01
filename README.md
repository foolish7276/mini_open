# OpenClaw-Mini (LangChain Reproduction)

这是一个用 Python + LangChain 复现 `openclaw-mini` 架构的项目。

## 当前能力（高保真版）

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

## 架构映射

- `src/openclaw_mini_lc/agent.py`：主循环、steering、子代理、事件发射
- `src/openclaw_mini_lc/context.py`：token 预算、裁剪、compaction
- `src/openclaw_mini_lc/session.py`：会话落盘与 compaction entry
- `src/openclaw_mini_lc/tools.py`：工具定义与安全控制
- `src/openclaw_mini_lc/queue.py`：session 串行 + global 并发上限
- `src/openclaw_mini_lc/provider.py`：LangChain provider 适配
- `src/openclaw_mini_lc/memory.py`：记忆存取与检索
- `src/openclaw_mini_lc/events.py`：异步事件总线

## 说明

- 该版本已覆盖 openclaw-mini 的主要架构思想和运行机制。
- 真正 token 级 LLM streaming 事件（provider 原生 chunk 逐 token 转发）在不同模型 SDK 上行为不一致；当前 `message_delta` 为文本块级事件，属于可用替代实现。

