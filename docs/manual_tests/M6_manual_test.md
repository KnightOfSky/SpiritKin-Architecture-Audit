# M6 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12 / SpiritKinDesktop
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 反向 import | AST/层级规则确认 orchestrator 不反向依赖 app | 通过 |
| 2 | Fake services | 独立实例化和端口注入通过 | 通过 |
| 3 | 冷启动 | WPF、事件桥和命令网关正常启动并响应 | 通过 |

- 证据：`backend/tests/unit/test_layering_rules.py`、`test_architecture_layers.py`。
- 结论：通过。
