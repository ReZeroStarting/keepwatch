# 竞品分析（2026-10）

> 这份文档回答一个问题：**这个项目凭什么存在。**
> 结论先行：**"agent 之间的邮箱"已被占据；"无人值守时的可靠性"仍是空白，且是竞品主动让出来的。**

## 一、直接竞品：agent-mailbox

- PyPI `agent-mailbox` v0.8.2，2026-10-10 发布，Apache-2.0
- 三周内从 0.5.0 迭代到 0.8.2，仍在高频更新
- 形态：本地 MCP 邮箱 + 本地 Web workbench + macOS App（Windows/Linux 为 preview）

### 它解决什么

1. **项目级信箱**：每个 agent 通过 stdio MCP 连接，按项目隔离
2. **显式接受**：读到消息 ≠ 接受任务，必须显式 accept
3. **批准版本**：接受任务时记录资源清单，agent 只读被 pin 的版本号
4. **人工验收**：提交结果后停在"待裁决"，人来 accept / 打回
5. **跨宿主**：Claude Code、Codex、DeepSeek Harness、Workbuddy、Doubao、ZCode、Hermes

### 它明确不解决什么

它们的 README 原话：

> *"Loading MCP tools does not itself wake an idle conversation. Ask the agent to check its mailbox, or configure a host-specific wake hook."*
>
> *"Notification is a separate connection... A successful notification does not mean the agent accepted or completed the task."*

即：**唤醒、判活、回收、续跑，全部外包给宿主。**

### 结论

| 维度 | agent-mailbox | 本项目 |
|---|---|---|
| 传输 | MCP over stdio | 文件目录 |
| 任务语义 | 显式 accept | claim + 租约 |
| 人的位置 | 在环（最终裁决） | **不在场** |
| 判活 | 无 | heartbeat + 阈值 |
| 卡死恢复 | 无 | 租约过期回收 + 经手章 |
| 断点续跑 | 无 | checkpoint 交接 |
| 依赖 | mcp / cryptography 等 | 零第三方 |

**互补，不竞争。** 理论上可以给 agent-mailbox 写一个适配器（见 ROADMAP v0.4）。

## 二、相邻项目

| 项目 | 形态 | 与本项目的关系 |
|---|---|---|
| [A2A](https://github.com/ai-boost/awesome-a2a) | HTTP/JSON-RPC 互操作协议 | 网络层标准；不解决"没人干活" |
| [Temporal](https://temporal.io) + [LangGraph 插件](https://temporal.click/blog/temporal-langgraph-plugin-durable-execution) | 持久化执行服务 | 可靠性语义的工业级答案，但需要服务端 + SDK 代码。本项目是它的"零依赖缩小版"，面向"agent 自己会用 CLI"的场景 |
| [agent-watchdog](https://pypi.org/project/agent-watchdog/) | 进程内熔断（循环/预算/超时） | 管**单次运行**；本项目管**跨进程、跨时段** |
| [mailbox-mcp](https://github.com/EllGree/mailbox-mcp) | MCP 封装的 `.claude/inbox` | 只有邮件原语，Phase 1 |
| [parallel-worktree](https://github.com/sh5623/parallel-worktree) | 多 worktree 并行 + 收割 | 解决并行写，不解决消息丢失 |
| AutoGen / CrewAI / MetaGPT | 进程内多 agent 编排 | 不处理进程边界与崩溃 |

## 三、白区（本项目要占的）

1. **静默失效判定**：区分"空闲""死亡""卡住""做完没 ack"——四种状态的处置完全不同
2. **租约回收 + 经手章**：不是简单重试，而是把"这活被人干过"写回消息本身
3. **无人值守的停机/续跑**：checkpoint 交接，换进程、换 agent 都能接上
4. **闸门在工具里，不在提示词里**：换 agent 不失效

## 四、命名

已被占用：`agentbus`、`agent-mailbox`、`agent-watchdog`、`deadman`、`mailbox-mcp`。

候选（发布前必须在 PyPI + GitHub 双查）：
- `nightshift`（当前工作名，语义：无人值守 = 上夜班）
- `mailbox-ops`
- `silent-failure`
- `steward`

## 五、风险

- 若 agent-mailbox 后续补上"盲区唤醒 + 租约"，白区会缩小 → 对策：保持轻、可嵌入、可对接
- 单人维护 → 对策：协议优先，CLI 稳定，UI 永不做
