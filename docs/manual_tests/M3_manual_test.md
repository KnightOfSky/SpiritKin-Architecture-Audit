# M3 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / SpiritKinDesktop Atelier 夜间主题
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 工具授权面板 | WPF 可读 126 个工具并原值回写 | 通过 |
| 2 | 风险确认 | safe/network/fs-write/shell 策略与 shell 每次确认用例通过 | 通过 |
| 3 | 双重安全 | Authz 放行后仍调用 Safety；soft/hard stop 不受影响 | 通过 |
| 4 | 桌面禁用/恢复闭环 | 在“授权与调度”页禁用 `demo.ok` 后真实调用返回 `tool_disabled_by_operator`；再由同一 UI 恢复后返回 `tool_authorized` | 通过 |

- 禁改区抽查：未替换或绕过 `evaluate_execution_safety`。
- 证据：`backend/tests/unit/test_tool_authz.py`、`tmp/ui-audit-20260718/03-authorization-top.png`、`tmp/ui-audit-20260718/04-tool-disabled.png`。
- 结论：通过。
