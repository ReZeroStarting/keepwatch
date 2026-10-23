# Protocol v0.1 (draft)

> 目标：任何 agent 只要能读写文件，就能接入；任何人类只要 `ls` 和 `cat`，就能看懂当前状态。

## 1. 目录布局

```
<ROOT>/
├── agents/
│   └── <agent-id>/
│       ├── pending/       # 可以抢的活
│       ├── processing/    # 已被认领（含租约）
│       └── done/          # 已归档（只移不删）
├── state/
│   ├── <agent-id>.heartbeat.json
│   └── gate.json          # {"pipeline_enabled": true}
└── audit/
    └── <yyyy-mm>.jsonl    # 追加式审计流水
```

约定：

- **只写对方的 `pending/`**，读写自己的三个箱
- **只移不删**：任何归档动作都是 `os.replace`，永不 `unlink`
- 目录名与 agent id 同名，`agent-id` 只允许 `[a-z0-9_-]{1,32}`

## 2. 消息格式

一个消息 = 一个 JSON 文件，文件名 `<message-id>.json`。

```json
{
  "id": "20261023T160000-7f3a1c",
  "from": "agent_a",
  "to": "agent_b",
  "type": "task",
  "subject": "第 3 批派发",
  "body": "...",
  "created_at": "2026-10-23T16:00:00Z",
  "reply_to": null,
  "trace": "20261023T160000-7f3a1c",
  "depth": 0,
  "expect_reply": true,
  "_lease": null
}
```

字段约束：

| 字段 | 约束 |
|---|---|
| `type` | `task` / `reply` / `notice` / `ping` |
| `depth` | 无父消息为 0；回信 = 父 + 1；超过 `max_depth`（默认 3）拒绝发送 |
| `trace` | 新开话题时等于自身 `id`；回信必须**继承**，禁止重生 |
| `reply_to` | 指向父消息 id，必须真实存在 |
| `_lease` | 仅出现在 `processing/` 中，结构见下 |

## 3. 状态机

```
        send
         │
         ▼
     pending ──claim──► processing ──ack──► done
         ▲                  │
         └────── reap ──────┘   (租约过期)
```

- `claim`：先抢独占令牌 `<processing>/.<id>.claim`（`O_CREAT|O_EXCL`），拿到才动文件。
  抢不到的进程得到 `Held`（退出码 3），即"别人正在处理"
- `ack` / `done`：`processing → done`，写 `closed_at` / `closed_by`
- `reap`：租约过期后由看门狗把 `processing → pending`，在消息里记 `tries += 1`、`prev_owners += [owner]`、`reaped_at`

### 为什么不用"重命名"当锁（重要）

最初的实现用 `os.replace(pending → processing)` 的原子性当锁，**在真实测试中失效了**：

> 在同一 Windows 文件系统下，两条线程对同一源文件并发调用 `os.replace()`，
> **两次都返回成功**——即使第二次执行时源文件已经不存在。

结果是两个 worker 同时"认领"了同一条任务，其中一个的写入静默覆盖了另一个。

现在改用 `O_CREAT|O_EXCL` 独占创建作为互斥：

- 该原语在 NTFS 与 POSIX 上都保证原子性（16 线程并发实测：恰好 1 个成功）
- 令牌文件只活在一次状态转换期间，正常路径几毫秒
- 进程崩溃留下的令牌由 mtime 超过 30 秒判定为陈旧并回收
- 令牌文件以 `.` 开头且不以 `.json` 结尾，不会被 `list_tasks` 误读

**教训**：文件系统的原子性保证因平台而异，"用文件移动当锁"是能通过单元测试、
但在并发下静默出错的假设。要验证它，必须写并发测试。

## 4. 租约

```json
"_lease": {
  "owner": "agent_b",
  "acquired_at": "2026-10-23T16:01:00Z",
  "expires_at": "2026-10-23T16:06:00Z",
  "tries": 1,
  "prev_owners": []
}
```

- 默认 TTL 5 分钟，可配置
- 同一 owner 可续租（`renew`），续租不增加 `tries`
- 他人未过期时 `claim` 拒绝，退出码 3
- `tries` 达到 `max_tries`（默认 3）时打标 `suspect: true`，看门狗升级告警——**只打标，不动活**

## 5. 心跳

`state/<agent>.heartbeat.json`：

```json
{"agent": "agent_b", "at": "2026-10-23T16:02:11Z", "pid": 4242}
```

**任何带 `--who` / `--from` 的命令都自动写心跳**，不需要单独调用。
理由：靠纪律上报的心跳必然失守，心跳应该是干活的副产品。

## 6. 看门狗判定

| 判定 | 条件 | 建议动作 |
|---|---|---|
| `OK` | 全部新鲜 | 静默 |
| `IDLE` | `pending` 有件，但无人认领且未超阈值 | 静默（正常等待） |
| `DEAD` | 心跳缺失或超过 `heartbeat_after_min`（默认 20） | 告警：该 agent 可能已死 |
| `STUCK` | `processing` 中租约过期 | 告警 + 可 `reap` |
| `DONE_UNACKED` | `processing` 有件但 owner 心跳新鲜、租约过期 | 告警：做完没归档，下一轮会重复派发 |
| `STOPPED` | `gate.json` 中 `pipeline_enabled=false` | 只报一次，不重复告警 |

`DONE_UNACKED` 是最容易漏、代价最高的一种：活干完了但没 ack，系统会重新派一遍，**等于白干一次还可能覆盖结果**。

## 7. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 一般错误（参数、IO） |
| 2 | 目标不存在 / 已被他人抢先 |
| 3 | 租约被他人持有 |
| 13 | 被策略阻止（depth 超限、依赖未完成） |
| 14 | 流水线已停（闸门关闭） |

**退出码是接口的一部分**，agent 靠它决定下一步，不靠解析人话。

## 8. 原子性规则

1. 写入一律"先写 `.tmp` 再 `os.replace`"，禁止出现半个文件
2. 跨设备移动不可原子 → 启动时检测，`state/` 与 `agents/` 必须同盘
3. 任何命令失败都不应留下处于中间态的文件；中途崩溃最多造成"消息留在原地"，由看门狗发现
4. 审计流水只追加，不修改

## 9. 明确不做

- 不做加密、不做鉴权（同机同用户的信任边界）
- 不做网络传输（需要网络时用 A2A 或 MCP，本项目不掺和）
- 不做调度（何时跑一轮是宿主的事，本项目的职责是"发现没跑"）
