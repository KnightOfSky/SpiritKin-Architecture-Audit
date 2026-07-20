# M7 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12 / 本地 HTTP 测试服务
- 状态：本机链路通过，公网远端 MCP 待外部网络验收

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 三种 Transport | stdio、Streamable HTTP、legacy SSE 的发现与调用专项通过 | 通过 |
| 2 | 会话与重试 | session header、超时重试和 SSE 重连用例通过 | 通过 |
| 3 | 健康降级/恢复 | 连续三次失败降级，恢复后重新可用并写审计 | 通过 |
| 4 | 公网远端 MCP | 需要可访问的远端 MCP 地址和网络环境 | 待外部验收 |

- 禁改区抽查：远端参数仍经过网关校验，不绕过 Authz/Safety。
- 证据：`backend/tests/unit/test_mcp_management.py`、`backend/tests/unit/test_mcp_transport.py`。
- 结论：本地真实 HTTP/SSE 链路通过；公网端到端待验收。
