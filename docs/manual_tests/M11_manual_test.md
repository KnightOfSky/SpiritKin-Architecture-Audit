# M11 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12
- 状态：配置与适配器通过，云端真实凭据待外部验收

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | Feishu | `dry_run` 默认、签名、重试和失败映射用例通过 | 通过 |
| 2 | Reviewer | 开关、模型配置、失败降级与输出契约通过 | 通过 |
| 3 | Persona 工具 | game/video persona 可通过 manifest 挂接既有执行器 | 通过 |
| 4 | 编排适配器 | LangGraph 最小适配可用，CrewAI 缺依赖时明确降级 | 通过 |
| 5 | 云端真实调用 | 需要 Feishu/Reviewer 有效凭据和可访问网络 | 待外部验收 |

- 证据：`backend/tests/unit/test_feishu_service.py`、`test_external_reviewer.py`、`test_agent_adapters.py`。
- 结论：本机配置激活与降级契约通过；云端真实调用待验收。
