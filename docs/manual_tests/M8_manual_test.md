# M8 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12 / 临时 manifest 工具目录
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 零代码发现 | 仅新增 `manifest.json` 与目录内 `echo.py`，无需修改注册代码 | 通过 |
| 2 | 受治理执行 | `entry.script + entry.argv` 经 Registry、Authz、Safety 和 Python Worker 执行 | 通过 |
| 3 | 工作流节点 | manifest 的输入 schema 自动生成工作流节点定义 | 通过 |
| 4 | 非法与冲突 | 越界脚本、非法 manifest、内置覆盖和多目录优先级均被拒绝或报告 | 通过 |

- 证据：`tmp/manifest-echo-20260717/`、`backend/tests/unit/test_tool_manifests.py`。
- 结论：通过，真实输出为 `m8-manifest-ok`。
