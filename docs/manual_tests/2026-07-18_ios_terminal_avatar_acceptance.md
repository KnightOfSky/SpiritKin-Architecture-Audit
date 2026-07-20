# 2026-07-18 iOS Terminal Avatar 验收

- 产品面：`/ios/terminal` PWA 与原生 SwiftUI Control destination 源码
- 浏览器：Microsoft Edge（用户指定）
- 服务：`http://127.0.0.1:8791/ios/terminal`
- 角色边界：iOS 为移动主控；Android 为 Bridge/执行端

## 结论

iOS PWA 已把现有 3D Avatar 作为首屏主控舞台，并使用与桌面、Web、
Android Bridge 相同的 v4 日/夜语义色。Android APK 和 `MainActivity`
未增加 Avatar 入口。原生 SwiftUI Control destination 已加入同一
`WKWebView` 舞台源码，但当前 Windows 环境不能替代 Xcode/真机验收。

## 步骤

| # | 操作 | 健康度 | 实际结果 |
|---|---|---|---|
| 1 | 原 PWA 390x844 首屏 | 需改进 | 只有密集管理卡片，没有 Avatar；首屏角色感不足 |
| 2 | 新 PWA 390x844 Light | 通过 | Avatar、运行状态、四项安静仪表和管理入口完整，无横向溢出 |
| 3 | 新 PWA 820x1180 Light | 通过 | 舞台保持宽幅，四项仪表单行展示，管理内容密度可读 |
| 4 | 新 PWA 390x844 Dark | 通过 | 暖炭底、铜色交互语义和浅色冷日光主题构成统一双色温系统 |
| 5 | Edge 控制台 | 通过 | 0 error / 0 warning；仅有浏览器对非 form 密码框的 verbose 建议 |
| 6 | Android Bridge 源码边界 | 通过 | 回归测试确认没有 `avatar_3d` 或 `AvatarView` 入口 |
| 7 | 原生 SwiftUI 源码 | 代码完成，待 Xcode | Control 页面加入全宽 `AvatarStageView`，使用 `WKWebView` 与 44pt 原生导航体系 |

## 截图

### iPhone Light

![iOS Terminal Avatar Light](../../output/playwright/ios-terminal-audit-20260718/03-ios-terminal-avatar-light-390x844.png)

### iPad Light

![iOS Terminal Avatar iPad](../../output/playwright/ios-terminal-audit-20260718/04-ios-terminal-avatar-light-820x1180.png)

### iPhone Dark

![iOS Terminal Avatar Dark](../../output/playwright/ios-terminal-audit-20260718/05-ios-terminal-avatar-dark-390x844.png)

## 限制

- Edge 视口验证的是 iOS PWA，不是 Safari/WebKit 真机截图。
- 原生 SwiftUI 源码尚未在 macOS/Xcode 编译、签名或安装。
- 本地 iframe 已加载 3D 资源；iOS 到 Runtime 的实时事件需要部署
  `SPIRITKIN_IOS_AVATAR_URL` 对应的 HTTPS/WSS 和专用只读令牌。不得复用
  主控管理令牌作为 Avatar 查询参数。
- 截图不能证明 VoiceOver、最大 Dynamic Type、iPad Split View 或真实
  触摸手势合规，这些仍属于真机矩阵。

## 2026-07-19 接续复验

这次接续在同一工作区完成了以下回归：

- `frontend/ios_controller_prototype.html` 在 Edge 的 iPhone 15 视口验证了
  对话、板块、设备、我的四个标签页；System/Light/Dark 三态切换、3D
  Avatar 舞台、真实 `/ios/terminal` iframe、快捷指令与 App Intents 目录
  均可见且无布局重叠。截图保存在
  `output/playwright/ios-controller-resume-20260719/`。
- 主控端 `GET /ios/schemas/shortcuts.json` 实际返回 6 个目录项：Ask
  Spirit、读写剪贴板、截屏、通知、电量；`X-SpiritKin-iOS-Token` 与
  CORS 头也已覆盖。
- `start_desktop_console.py --restart-wpf` 实际拉起 llama.cpp chat/
  embedding、8791 主控、8792 原型和 50000 CosyVoice；各端点健康检查
  通过，CosyVoice 复用本地 Fairy 机械女声配置。
- 相关 Python 回归套件共 123 项（包含领域分类、会话同步和快捷指令路由）全部通过；
  设计令牌的 26 项日/夜对比度检查也通过。

仍需明确的运行限制：当前桌面 chat 使用 35B 本地模型，在冷启动或高负载
时一次 Ask Spirit 生成可能超过 iOS 请求等待时间。接口会把运行时超时映射
为可重试的 503，而不会伪装成成功；真机发布前应使用更小的移动友好模型或
把该动作改为异步任务并在 iOS 显示任务状态。原生 SwiftUI 仍需 macOS/Xcode
编译、签名和真机矩阵验收。

## iOS 主控能力与执行边界

当前 iOS 主控已经不是只能打开 Terminal 的请求壳，能力分为四层：

1. **会话层**：读取、创建、切换、归档、删除共享桌面会话；消息携带稳定
   `session_id`，通过 `GET/POST /ios/sessions` 与桌面状态同步。
2. **领域层**：板块按电商、内容与媒体、开发与自动化、系统与治理、其他
   分类。电商板块包含商品发布、素材、Android 上架和完整 `/ios/terminal`。
3. **控制层**：查看服务、模型、模块、安全、工作流运行、Android Bridge、
   远程 Worker、素材，并可启动/审批/取消/重试允许的动作。
4. **系统接入层**：Shortcuts、App Intents、Share Sheet、照片/文件选择器、
   URL Scheme 和公开 API；截屏、外发、提交等高风险动作仍需要系统或人工确认。

执行路径是：

```text
iOS UI / Shortcuts
      -> iOS endpoint (/ios/sessions, /ios/control/action, /ios/shortcut)
      -> 桌面状态 / SpiritKinRuntime / workflow engine
      -> llama.cpp、CosyVoice、Android Bridge 或 Remote Worker
      -> 状态、审批、素材和结果回到 iOS
```

因此，LLM 推理、工作流编排、桌面服务、CosyVoice 和 Android 操作目前确实
主要由桌面/本地 Runtime 执行；iOS 负责界面状态、会话管理、权限确认、任务
调度和结果呈现。iOS 本地完成的包括主题切换、会话缓存、系统选择器、通知、
Share Sheet，以及在真机上由 Shortcuts/App Intents 执行的设备侧动作。

现阶段仍不能等同于 ChatGPT、Claude 或快捷指令原生体验：推送通知、离线队列、
后台可靠刷新、真机网络配对和 Xcode 签名还未达到发布级别；当前 Windows 环境
只能完成 PWA 与 SwiftUI 源码验收。
