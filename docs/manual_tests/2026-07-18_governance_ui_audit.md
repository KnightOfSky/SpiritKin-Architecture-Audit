# 2026-07-18 桌面治理与陪伴 UI 审计

- 环境：Windows 11 / SpiritKinDesktop / Atelier 夜间主题 / 1480x940
- 范围：M3 授权、M14 调度、M17 音乐栏、M5 嵌入 Avatar
- 方法：当前构建的 WPF UIAutomation 操作、状态回读、真实后端调用和画布像素检查

## 结论

“授权与调度”页面已覆盖方案缺失的管理工作流，信息密度与现有 Atelier 工作台一致；工具禁用/恢复、任务全动作与时区更新均能闭环。音乐播放器底栏、队列弹层和嵌入 Avatar 正常呈现，没有横向裁切、叠压或空白画布。

## 步骤与结果

| # | 操作 | 结果 | 证据 |
|---|---|---|---|
| 1 | 打开管理工作台并进入“授权与调度” | 页面标题、刷新状态、工具授权区与调度区可扫描，1480x940 无横向裁切 | `01-current-management-window.png`、`03-authorization-top.png` |
| 2 | 取消 `demo.ok` 启用并保存 | 后端真实调用返回 `tool_disabled_by_operator`；恢复后返回 `tool_authorized` | `04-tool-disabled.png` |
| 3 | 创建 date 任务，暂停、恢复、立即运行、确认取消 | 状态依次落为 paused、active、complete、cancelled | `02-authorization-scheduler.png`、`05-scheduler-cancelled.png` |
| 4 | 将另一任务时区更新为 UTC | 列表时区和下一次运行时间同步更新，最终任务已取消清理 | UIAutomation 状态回读 |
| 5 | 通过 View 菜单打开音乐播放器与队列 | 底栏和弹层完整可见，没有播放音频 | `06-music-player-bar.png`、`07-music-queue.png` |
| 6 | 刷新嵌入 Avatar | 画布非空白；17,250 点采样的非暗像素比 98.71%，亮度方差 2918.59 | `08-avatar-after-m5-fix.png` |

## 截图

![授权与调度页](../../tmp/ui-audit-20260718/02-authorization-scheduler.png)

![工具禁用状态](../../tmp/ui-audit-20260718/04-tool-disabled.png)

![音乐播放器队列](../../tmp/ui-audit-20260718/07-music-queue.png)

![M5 修复后的嵌入 Avatar](../../tmp/ui-audit-20260718/08-avatar-after-m5-fix.png)

## 可用性与风险

- 工具授权、保存与调度按钮具备稳定 AutomationId，当前自动化可重复定位核心操作。
- 页面保持现有工作台的紧凑信息架构；工具列表和任务列表均在各自滚动区域内，不会挤压编辑表单。
- 截图和 UIAutomation 不能替代键盘全流程、屏幕阅读器与 WCAG 对比度审计。
- M16 通话窗口打开后会启动麦克风采集，未在无用户许可时触发；M17 未播放音频。
- M5/M15/M18 的浏览器多视口验收仍需用户指定 Chrome 或 Edge 后执行。
