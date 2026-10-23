# Roadmap

## 原则

1. **协议先于实现**：`docs/PROTOCOL.md` 是契约，代码是实现
2. **每个版本只加一类能力**，且必须带测试
3. **永远零第三方依赖**（CI 里用 `pip install --no-deps` 验证）
4. UI 不做，HTTP 不做，云不做

## v0.1 — 能跑起来（✅ 已完成）

- [x] 包结构：`core` / `cli`（一个模块装两种命令，够用就行）
- [x] 命令：`init` / `send` / `check` / `claim` / `renew` / `release` / `run` / `done` / `verify` / `reap` / `status`
- [x] `run` 包装器：认领 → 执行 → 记录真实退出码 → 通过则收工、失败则放回队列、超过 `--max-tries` 打 `suspect`
- [x] 心跳写入（所有带 `--who` 的命令自动写）
- [x] 租约：claim 抢锁、TTL、过期回收、经手章（`tries` / `prev_owners`）
- [x] `done` 前必须有 `rc=0` 的 verify 记录（`--force` 可越）
- [x] 退出码 0/1/2/3/13，`--json` 全命令可用
- [x] 测试：28 条，含 16 线程并发抢占、崩溃恢复、孤儿文件、坏 agent id
- [x] `examples/two_workers.py`：一个 worker 死掉、另一个接手的完整故事

**验收**：`pip install -e .` 后 3 分钟跑通 demo（已达成）。

## v0.2 — 能发现故障（约 2 周）

- [ ] `heartbeat` 自动写入（所有带 `--who` 的命令）
- [ ] `watchdog`：五种判定 + `--json` + 语义化退出码
- [ ] `reap`：租约回收 + 经手章
- [ ] `--must-verify`：没有 rc=0 记录不许 ack
- [ ] 崩溃注入测试：`kill -9` 在 claim/ack 中途，断言无数据丢失

**验收**：故意 kill 掉 agent，看门狗在阈值内报 `DEAD`；重启后能接着干。

## v0.3 — 能扛长跑（约 3 周）

- [ ] `checkpoint`：一条命令写出续跑指南
- [ ] `pipeline --on|--off` 总闸门
- [ ] 依赖（`--after`）+ `hold/` 队列
- [ ] `doctor`：确定性自检，退出码 0/1
- [ ] 单机 8 小时无人值守的实测记录（写进 docs/field-notes.md）

**验收**：跑一夜，第二天 `doctor` 全绿，`checkpoint` 能说清"停在哪、下一步做什么"。

## v0.4 — 能对接（可选）

- [ ] 适配器：把 agent-mailbox 的 MCP 邮箱当作传输层
- [ ] 适配器：纯目录约定（任何自建邮箱）
- [ ] `migrate`：从旧目录结构导入

## v1.0 — 能信任

- [ ] 协议冻结，写 `docs/PROTOCOL-v1.md`
- [ ] 一致性测试套件（conformance tests），别人实现也能跑
- [ ] 至少一个外部用户在真实项目里用了 2 周

## 发布检查单（每次发版）

- [ ] PyPI 名称未被占用
- [ ] `LICENSE` + `pyproject.toml` 元数据完整
- [ ] README 首屏能在 10 秒内说清"这是什么、为什么需要"
- [ ] CI 绿：Linux + Windows，3.9 / 3.12 / 3.13
- [ ] `CHANGELOG.md` 有本次条目
- [ ] `git tag vX.Y.Z` + GitHub Release
- [ ] 无 `.bak`、无 `__pycache__`、无本地路径
