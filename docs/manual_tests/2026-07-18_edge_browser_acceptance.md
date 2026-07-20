# 2026-07-18 Edge 浏览器验收

- 浏览器：Microsoft Edge（用户指定）
- 服务：`http://127.0.0.1:8787/`，复用当前 Runtime 事件桥与命令网关
- 范围：M5 Avatar、M15 开场气泡、M18 受控本地游戏 Demo

## 结论

Edge 多视口与主要交互通过。首轮 1440x900/1024x768 发现 Avatar 兔耳被 Canvas 顶边截断；将宽屏相机基线后移并保留移动端极限后，1440x900、1024x768、390x844 均显示完整。事件桥使用当前桌面会话凭据后稳定显示 `ws: connected`。

## 步骤

| # | 操作 | 健康度 | 实际结果 |
|---|---|---|---|
| 1 | Avatar 1440x900 | 通过 | 模型、背景、HUD、控制面板完整；兔耳裁切修复后全身可见 |
| 2 | Avatar 1024x768 | 通过 | 控制区可滚动且无横向裁切，模型完整 |
| 3 | Avatar 390x844 + recovery 气泡 | 通过 | 气泡标题、正文、关闭、动作按钮均可见，不遮挡 HUD；waiting + nod 生效 |
| 4 | Avatar 5 秒 FPS | 通过 | 1440x900 为 43.03 FPS；390x844 为 44.91 FPS；画布像素非空白 |
| 5 | 游戏 Demo 启动/回收 | 通过 | Canvas、HUD 和 5 个核心可见；允许动作回收首个核心，进度变为 1/5 |
| 6 | 游戏暂停/继续 | 通过 | 居中暂停层、继续按钮和背景降噪状态清楚 |
| 7 | 游戏 Kill Switch | 通过 | `Shift+Esc` 进入 `stopped/global_hotkey`，内部停止延迟 0ms |
| 8 | 游戏 390x844 | 通过 | HUD 与画布无裁切；仍明确为键盘/自动化 Demo，不宣称触控玩法 |

## 截图

![Avatar 1440x900](../../output/playwright/edge-acceptance-20260718/15-avatar-1440x900-authenticated.png)

![Avatar 移动端开场气泡](../../output/playwright/edge-acceptance-20260718/17-avatar-opening-bubble-390x844-authenticated.png)

![游戏暂停态](../../output/playwright/edge-acceptance-20260718/12-game-paused-1440x900.png)

![游戏移动视口](../../output/playwright/edge-acceptance-20260718/14-game-mobile-390x844.png)

## 限制

- FPS 来自当前 RTX 5060 Ti 机器，不代表独立低端硬件。
- Avatar 唯一控制台错误是浏览器自动请求缺失的 `favicon.ico`；模型、事件、画布和交互无运行错误。
- 截图不能证明键盘全路径或屏幕阅读器合规；本轮 DOM 快照已确认核心按钮与气泡具备可访问名称。
