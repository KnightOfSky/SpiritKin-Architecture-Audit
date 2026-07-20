# M14 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Python 3.12 / APScheduler SQLite
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 三种触发器 | date、interval、cron 的创建、暂停、恢复、立即运行和删除通过 | 通过 |
| 2 | 时区与恢复 | 时区/DST、misfire、并发、幂等和重启恢复专项通过 | 通过 |
| 3 | 真实近时触发 | 创建 45 秒后任务并重启命令网关，PID 变化后任务仍按时完成 | 通过 |
| 4 | 恰好一次 | SQLite delivery 仅一条，状态在事件发出前提交为 `complete` | 通过 |
| 5 | 桌面管理 | “授权与调度”页可查看、创建、暂停/恢复、立即运行和删除任务 | 通过 |
| 6 | 当前 UI 全动作 | 新建 date 任务后在桌面依次暂停、恢复、立即运行、确认取消，状态依次为 paused/active/complete/cancelled | 通过 |
| 7 | 时区编辑 | 由桌面把任务从 Asia/Shanghai 更新为 UTC，列表的时区与下一次运行时间同步为 UTC，随后清理为 cancelled | 通过 |
| 8 | 网关鉴权 | 无鉴权直接读取调度接口返回 unauthorized；桌面通过受管会话正常读取 | 通过 |

- 禁改区抽查：任务触发仍重新经过普通安全门，通知受关系边界抑制。
- 证据：`backend/tests/unit/test_scheduler_service.py`、命令网关真实重启记录、`tmp/ui-audit-20260718/02-authorization-scheduler.png`、`tmp/ui-audit-20260718/05-scheduler-cancelled.png`。
- 结论：通过。
