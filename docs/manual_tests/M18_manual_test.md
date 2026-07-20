# M18 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Microsoft Edge / 受控本地 Demo 适配器
- 状态：可选实现与 Edge Demo 通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 默认拒绝 | 白名单默认为空，未知窗口/画面立即暂停 | 通过 |
| 2 | 运行约束 | 仅允许声明动作，焦点校验、速率限制和停止条件生效 | 通过 |
| 3 | Kill Switch | 全局停止可终止当前任务并留下结构化审计 | 通过 |
| 4 | 审计回放 | 状态、动作、原因和结果可按运行回放 | 通过 |
| 5 | 本地 Demo | Edge 中完成启动、移动、回收 1/5、暂停、继续、重置和 390x844 响应式检查；控制台 0 error | 通过 |
| 6 | 停止键 | 真实键盘 `Shift+Esc` 使状态变为 `stopped/global_hotkey`，内部停止延迟为 0ms，低于 200ms 门槛 | 通过 |
| 7 | 移动视口 | 390x844 HUD 和 Canvas 无裁切；Demo 仍以键盘/自动化为主，不把移动视口描述为触控游戏 | 通过 |

- 范围声明：只面向受控本地 Demo，不支持第三方线上游戏自动化。
- 证据：`backend/tests/unit/test_game_automation.py`、`output/playwright/edge-acceptance-20260718/10-game-start-1440x900.png`、`12-game-paused-1440x900.png`、`13-game-kill-switch-1440x900.png`、`14-game-mobile-390x844.png`。
- 结论：可选实现与指定 Edge 浏览器体验通过。
