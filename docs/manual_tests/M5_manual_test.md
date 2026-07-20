# M5 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / Microsoft Edge / Three.js 前端 / WPF WebView2 嵌入视图
- 状态：实现、Edge 多视口与当前机器 FPS 通过；目标低端机性能仅保留为设备复核项

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 语义情绪反应 | 真实 Embedding 与关键词降级路径通过 | 通过 |
| 2 | idle 参数 | 胸腔呼吸周期 4 秒、hips 重心摇摆周期 7 秒、截断指数分布随机眨眼间隔 2-6 秒，且说话/静态偏好会抑制眨眼 | 通过 |
| 3 | 表情过渡 | VRM 与 morph 表情共用 300ms ease-out 过渡；静态动效偏好立即落值 | 通过 |
| 4 | 明暗主题灯光 | light/dark 使用不同 hemisphere/directional 强度与颜色配置，主题事件可即时重应用 | 通过 |
| 5 | 桌面嵌入画布 | 1480x940 当前桌面中 Avatar 非空白；画布采样 17,250 点，非暗像素比 98.71%，亮度方差 2918.59 | 通过 |
| 6 | 60 秒自然度/多视口 | Edge 会话持续超过 60 秒；1440x900、1024x768、390x844 均完成真实模型和画布检查。首轮发现宽屏兔耳裁切，调整响应式相机基线后复测全身完整 | 通过 |
| 7 | Edge FPS/画布 | 1440x900 为 43.03 FPS，390x844 为 44.91 FPS；两次均为 modelReady 且 Canvas 像素非空白 | 通过 |
| 8 | 事件通道 | 从桌面启动状态安全注入现有会话凭据后，Edge HUD 显示 `ws: connected`；临时凭据文件已删除且未写入报告 | 通过 |

- 证据：`backend/tests/unit/test_semantic_reaction.py`、`backend/tests/unit/test_avatar_idle_motion_frontend.py`、`tmp/ui-audit-20260718/08-avatar-after-m5-fix.png`、`output/playwright/edge-acceptance-20260718/15-avatar-1440x900-authenticated.png`、`16-avatar-1024x768-authenticated.png`。
- 结论：方案缺失的 idle、表情缓动、主题灯光和 Edge 多视口已闭合；若要代表低端硬件，只需另在目标设备记录 FPS。
