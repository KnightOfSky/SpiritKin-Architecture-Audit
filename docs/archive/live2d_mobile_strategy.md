# SpiritKinAI Live2D 桌面 / Web / Android / iOS 接入策略

## 总结

- **桌面端**：优先用浏览器打开 `frontend/live2d.html`，通过 WebSocket 消费 `avatar.state`，用 WebGL 渲染 Live2D。
- **Web 端**：同桌面方案，共用 `live2d.html`，适合作为最快可验证方案和跨设备展示页。
- **Android**：优先用 Chrome / WebView 加载同一个 `live2d.html`；需要 Companion App 时再内嵌 WebView。
- **iOS**：优先 Safari / WKWebView 加载同一个页面，但要降低 DPR、贴图尺寸、动作频率，并要求用户触摸后进入全屏/音频播放。
- **Native Live2D SDK**：不是当前最优先。等模型资源、动作映射、口型同步稳定后，再分别做 Android/iOS 原生 SDK。

## 桌面和网页是否直接上 Live2D？

是。当前推荐路线是：

1. `backend` 只负责产生 `avatar.state` 事件。
2. `realtime_bridge` 通过 WebSocket 推送事件。
3. `frontend/live2d.html` 负责加载 Cubism `model3.json` 并渲染。

这样桌面、浏览器、手机浏览器都复用同一套事件协议，避免每个平台重复写一套状态机。

## Android 推荐方式

### 第一阶段：移动浏览器

- 手机连同一 Wi-Fi。
- PC 启动 `python scripts/start_realtime_panel.py --lan`。
- Android Chrome 打开 `http://<PC局域网IP>:8787/live2d.html`。
- 页面连接 `ws://<PC局域网IP>:8765`。

优点：最快验证、无需安装 App、调试方便。

### 第二阶段：Companion App WebView

- Android Companion App 内嵌 WebView 加载同一 URL。
- App 负责权限、通知、传感器、前台服务、节点上报。
- Live2D 仍由 WebView 渲染，事件仍来自 WebSocket。

### 第三阶段：原生 Live2D SDK

仅当需要更稳定的性能、离线资源管理、原生口型同步、后台/悬浮窗时再做。

## iOS 推荐方式

### 第一阶段：Safari / PWA

- Safari 打开 `live2d.html`。
- 建议加到主屏幕作为 PWA。
- 用户触摸页面后再进入全屏或启用音频相关能力。

iOS 限制：

- Safari 的自动播放和音频上下文需要用户手势。
- WebGL 内存更敏感，大贴图模型容易失败。
- 全屏 API 支持不如 Android Chrome 稳定。
- 后台运行能力受限，不能指望网页长期后台保活。

### 第二阶段：WKWebView 壳

- iOS App 内嵌 WKWebView 加载同一页面。
- 通过 App Intents / Shortcuts / URL Scheme 暴露白名单动作。
- Live2D 渲染仍走 WebGL，模型资源可打包进 App。

### 第三阶段：原生 Live2D SDK

仅当需要稳定高帧率、系统级集成、离线角色包和更细口型同步时再做。

## 移动端性能策略

- DPR 默认限制到 `1.5` 左右，低端设备可降到 `1.0`。
- 优先 2048 或更低贴图，谨慎使用 4096 贴图。
- 首屏不自动加载大模型；允许 URL 参数 `autoload=1`，但失败要降级 orb。
- 模型加载失败、CDN 失败、WebGL 不可用时必须保持事件消费，不阻塞 Agent。
- 说话动画不要每帧触发 motion，按状态变化触发即可。
- 移动端默认显示全屏触摸提示，让用户主动进入沉浸模式。

## URL 参数约定

- `ws=ws://<host>:8765`：事件桥地址。
- `model=http://<host>:8787/models/role/role.model3.json`：Cubism 模型地址。
- `autoload=1`：进入页面后自动加载模型。
- `maxDpr=1.5`：限制渲染 DPR。
- `scale=0.22`：模型缩放。
- `mobile=1`：强制使用移动端布局。
- `role=spirit`：按角色名从 manifest 中解析模型与动作映射。
- `config=models/manifest.json`：manifest 配置地址。

## 当前项目落点

- 页面：`frontend/live2d.html`
- 资源目录：`frontend/models/`
- manifest 示例：`frontend/models/manifest.example.json`
- 首页入口：`frontend/index.html`
- 启动器：`scripts/start_realtime_panel.py`
- 事件：`avatar.state`
- 后续模型资源建议目录：`frontend/models/<role>/<role>.model3.json`
