# M2 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12 / WPF .NET 8
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 纯路由 | 相同输入得到相同 `RouteDecision` 且不修改 metadata | 通过 |
| 2 | Soul Phase | Prompt 不包含工具 schema，阶段只持有 LLM callable | 通过 |
| 3 | Facade 兼容 | `ClusterDeps`、Wiring 和旧构造调用全绿；门面 548 行 | 通过 |

- 禁改区抽查：seen/ack、发言队列与协作专项包含在全量回归中。
- 证据：`test_cluster_router_and_context.py`、`test_response_phase.py`、`test_agent_cluster_facade.py`。
- 结论：通过。
