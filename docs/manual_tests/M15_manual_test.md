# M15 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Microsoft Edge / Three.js 前端
- 状态：通过

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 状态与优先级 | 恢复提示、未完成任务、建议和问候按规则去重与选优 | 通过 |
| 2 | 交互边界 | 气泡只导航不执行，接受/忽略/关闭反馈可追踪 | 通过 |
| 3 | 低动效/响应式 | 前端纯函数和样式契约用例通过 | 通过 |
| 4 | Edge 视觉体验 | 390x844 真实事件注入 recovery 气泡，标题、正文、关闭和“打开对话”均可见；气泡不遮挡 HUD，触发 waiting 表情和 nod 动作 | 通过 |

- 证据：`backend/tests/unit/test_opening_bubble.py`、`test_opening_bubble_frontend.py`、`output/playwright/edge-acceptance-20260718/17-avatar-opening-bubble-390x844-authenticated.png`。
- 结论：实现与 Edge 视觉交互通过。
