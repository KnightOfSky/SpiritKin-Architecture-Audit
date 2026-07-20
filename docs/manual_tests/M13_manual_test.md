# M13 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Python 3.12
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 边界提取 | 显式边界可提取、去重、更新和撤回 | 通过 |
| 2 | 重启持久化 | 原子写入后重新加载，边界与关系状态保持一致 | 通过 |
| 3 | Soul 注入 | 关系阶段和关怀策略进入 Soul 上下文，不注入工具 schema | 通过 |
| 4 | 多端事件 | 状态更新产生结构化事件，桌面/移动端可消费 | 通过 |
| 5 | 发布决策 | 关系阶段达到阈值时仍需显式边界和策略共同允许 | 通过 |
| 6 | 真实跨重启闭环 | 隔离存储创建 boundary，销毁并重载后重复输入去重；边界进入 Soul 提示词，解除后再次重载 active 数为 0 | 通过 |

- 证据：`backend/tests/unit/test_relationship_system.py`、`scripts/smoke_memory_relationship.py`、`tmp/memory-relationship-smoke-20260718/smoke-report.json`。
- 结论：通过。
