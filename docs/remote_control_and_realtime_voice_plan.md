## 实时语音原子操作 / 远程控制 / LPM 风格演进方案

### 1. 现在能做到什么
- 当前链路已经能支持：`语音一句话 -> Planner -> Executor -> 原子动作执行`
- 这属于**回合式、准实时**原子操作，不是全双工持续对话
- 已适合先验证：鼠标、键盘、文本输入、OpenClaw 基础动作

### 2. 如果要更接近“实时”还差什么
- 流式 ASR（边说边识别）
- 可打断的推理与动作队列
- 执行状态实时回传
- 高风险动作二次确认
- 语音/表情/动作的统一状态机

### 3. 测试时怎么接“眼耳口脸手脚”
#### 耳
- 麦克风 + 热词检测 + ASR
- 用 `backend/app/runtime.py` 作为主入口

#### 眼
- 屏幕截图、OCR、视觉理解
- 先跑 `backend/tests/manual/` 里的视觉脚本验证输入链路

#### 口
- `expression/speech.py` 做 TTS 播报
- 先验证回复能否正常播报，再验证长句中断

#### 脸
- Live2D / 表情推送跟随 `AgentReply.emotion`
- 建议先做四态：`neutral / happy / thinking / confused`

#### 手脚
- 本地 PC：`LocalPCExecutor`
- 机械臂：`OpenClawExecutor`
- 后续远端 PC：`RemoteExecutor`

### 4. 手机 -> 本地 -> 公司电脑 的建议链路
推荐拆成两层：

1. **控制面**
   - 手机 App / Web 页面
   - 本地家里电脑上的 SpiritKinAI 中枢

2. **执行面**
   - 公司电脑常驻 `remote_worker`
   - 向本地中枢上报节点状态
   - 接收标准执行请求并回传结果

建议协议最小字段：
- `node_id`
- `capabilities`
- `operation`
- `params`
- `request_id`
- `status`
- `result`

#### 4.1 现在可用的手机发指令路径
- 本机启动：`python scripts/start_realtime_panel.py --lan`
- 启动器会同时拉起：事件桥、手机/网页命令网关、前端 HTTP 面板、语音 runtime
- iOS / Android 与本机处于同一 Wi-Fi 时，浏览器打开：`http://<本机局域网IP>:8787/index.html`
- 面板内把 WebSocket 指向：`ws://<本机局域网IP>:8765`
- 面板内把 Command API 指向：`http://<本机局域网IP>:8788/command`
- 如果终端打印了 `Token`，需要在面板 Token 输入框填写后再发送命令

也可以不用网页，直接从 iOS 快捷指令 / Android HTTP 工具发 POST：

```json
{"text":"扫描本机软件","channel":"mobile"}
```

请求头：`Content-Type: application/json`；如果启用了 token，再加 `X-SpiritKin-Token: <token>`。

#### 4.1.1 手机使用移动数据时的连接方式

如果手机没有连同一 Wi-Fi，而是走 4G/5G 移动数据，`http://<本机局域网IP>`、`ws://<本机局域网IP>` 都不可达。必须增加一层公网可达通道：

1. **frp / 自有 HTTPS 反向隧道（中国区优先推荐）**
   - 手机端不需要安装额外 App，直接浏览器打开 HTTPS/WSS 地址。
   - 最稳妥方案：一台国内/香港云服务器跑 `frps`，本机跑 `frpc`，用自己的域名做 HTTPS。
   - 建议映射三个子域名：`spiritkin.example.com` -> 前端，`spiritkin-events.example.com` -> WebSocket，`spiritkin-command.example.com` -> Command API。
   - 优点：国内可用性通常比依赖海外 App 分发/VPN 客户端更可控。
   - 注意：必须加 token、HTTPS/WSS、访问控制；不要把高风险控制口裸奔公网。

2. **Tailscale / ZeroTier VPN（国际区/可下载客户端时推荐）**
   - PC 和手机都加入同一私有虚拟网络。
   - 优点：不把服务直接暴露到公网，安全性和稳定性较好。
   - URL 可用 Tailscale IP / MagicDNS，例如 `http://spirit-pc:8787/index.html`。
   - 如果中国区手机端下载不到，就不要卡在这条路，改走 frp/HTTPS 隧道。

3. **Cloudflare Tunnel / ngrok / 其他 HTTPS 隧道（推荐临时演示）**
   - 把本机 `8787` 前端、`8788` Command API、`8765` WebSocket 映射成公网 HTTPS/WSS。
   - 优点：手机移动数据可直接访问。
   - 注意：必须使用 HTTPS/WSS、token、访问控制；不要裸露 HTTP 控制口。

4. **自建云端 relay / message broker（推荐工程化阶段）**
   - 手机只连云端 relay，本机 runtime 也连 relay，双方通过队列/会话转发指令和事件。
   - 优点：更适合多端、离线重连、审计和权限中心。
   - 缺点：需要额外服务端和鉴权设计。

启动器现在支持只打印公网/隧道入口，不会自动暴露公网：

```powershell
python scripts/start_realtime_panel.py --lan `
  --public-frontend-url https://panel.example.com `
  --public-events-ws-url wss://events.example.com/ws `
  --public-command-url https://api.example.com/command
```

其中公网通道本身需要你用 VPN、Cloudflare Tunnel、ngrok、frp 或自建反代提前建立。

#### 4.1.2 推荐路线：Tailscale 移动数据访问

在 PC 和手机都安装并登录同一 Tailscale 账号后，PC 侧直接用：

```powershell
python scripts/start_realtime_panel.py --tailscale
```

脚本会绑定 `0.0.0.0`，尝试执行 `tailscale ip -4` 自动获取本机 Tailnet IPv4，并打印：

- 手机移动数据访问的 Frontend URL
- Live2D URL
- WebSocket URL
- Command API URL
- `X-SpiritKin-Token` token

如果自动检测失败，可手动指定：

```powershell
python scripts/start_realtime_panel.py --tailscale --tailscale-ip 100.64.0.8
```

手机不需要连同一 Wi‑Fi，只要手机 Tailscale 在线，就能通过 `http://100.x.y.z:8787/index.html` 访问。

通道 smoke test：

```powershell
python scripts/smoke_mobile_access.py `
  --frontend-url http://100.x.y.z:8787/index.html `
  --command-url http://100.x.y.z:8788/command `
  --token <启动器打印的 X-SpiritKin-Token>
```

建议用手机移动数据打开同一 URL 再实测一次；脚本侧主要验证当前机器视角下 Tailnet/隧道 URL 是否通。

#### 4.1.3 中国区推荐路线：frp + 域名

如果手机端下载不到 Tailscale，推荐走 frp/自有 HTTPS 隧道。启动器可以直接根据域名后缀推导三个公网入口：

```powershell
python scripts/start_realtime_panel.py --frp-domain-suffix example.com --frp-prefix spiritkin
```

会打印：

- `https://spiritkin.example.com/index.html`
- `wss://spiritkin-events.example.com`
- `https://spiritkin-command.example.com/command`

生成 `frpc.toml` 模板：

```powershell
python scripts/generate_frp_config.py `
  --server-addr frp.example.com `
  --server-port 7000 `
  --token <frp-token> `
  --domain-suffix example.com `
  --prefix spiritkin > frpc.toml
```

如需把远端 worker 也映射出去，可加：

```powershell
python scripts/generate_frp_config.py `
  --server-addr frp.example.com `
  --token <frp-token> `
  --domain-suffix example.com `
  --prefix spiritkin `
  --remote-worker-port 8790 > frpc.toml
```

然后中枢启动可使用：

```powershell
python scripts/start_realtime_panel.py --frp-domain-suffix example.com `
  --remote-worker-url https://spiritkin-worker.example.com `
  --remote-node-id office-pc `
  --remote-worker-token secret-token
```

#### 4.2 本机跨设备操控手机的现实边界
- iOS：系统沙盒很严格，不能像控制 PC 一样任意读写 App 数据或模拟全局点击。推荐通过“快捷指令 + URL Scheme / Webhook / App Intents”暴露白名单动作，例如采集位置、读取剪贴板、拍照后上传、发送通知、打开指定 App。
- Android：可做得更深，但必须用户授权。推荐顺序是 Companion App -> Accessibility Service -> Notification Listener -> Shizuku/ADB（仅开发/高级用户）。
- 两端都应把手机注册成远端节点，上报 `node_id`、在线状态、支持的 `capabilities` 和用户授权范围。
- 不做隐蔽采集；数据采集必须由手机端明确授权、可见、可撤销，并写审计日志。

#### 4.3 手机节点建议能力模型
- `mobile.notify.send`：向手机发通知
- `mobile.clipboard.read/write`：剪贴板读取/写入，默认高风险确认
- `mobile.location.get`：定位采集，必须手机端授权
- `mobile.photo.capture` / `mobile.file.pick_upload`：拍照或选择文件上传
- `mobile.shortcut.run`：iOS 快捷指令或 Android Intent/Tasker 任务
- `mobile.app.open`：打开指定 App 或 deeplink
- `mobile.sensor.sample`：采样电量、网络、运动传感器等授权数据

这些能力应进入 `NodeRegistry`，由 `RemoteExecutor` 按 `node_id` 下发，而不是让 LLM 直接拼接任意系统命令。

### 5. 远程控制的最小安全要求
- 节点鉴权（token / mTLS 至少选一种）
- 操作审计日志
- 高风险动作确认
- 空闲超时断开
- 节点在线/离线心跳
- 手机命令入口绑定 LAN 时必须设置 token；跨公网必须走 HTTPS / 反向隧道 / VPN，不能裸露 HTTP 控制口
- 手机走移动数据时，优先 VPN 或 HTTPS/WSS 隧道；公网 Command API 必须启用 token，并建议加 IP/身份访问控制、速率限制和审计
- 已接入最小审计：mobile/web/desktop 输入、高风险确认、执行结果、未授权 command 请求会进入 `state/audit_log.jsonl`，Dashboard 会显示审计摘要。
- 后续安全增强建议：用户身份、IP/设备白名单、限流、审计详情页、敏感字段脱敏、mTLS 或 WireGuard/Tailscale/ZeroTier 等专用通道。

### 6. 后续代码落点建议
- `backend/executors/remote_executor.py`：远端请求下发
- `backend/executors/node_registry.py`：节点注册与查找
- `backend/executors/remote_protocol.py`：远端协议对象
- `backend/app/command_gateway.py`：手机/网页 HTTP 指令入口
- 已补最小版：`backend/remote/worker.py`（HTTP worker，含 `/health`、`/heartbeat`、`/execute`）
- 后续再补：`backend/mobile/` companion 协议适配层（iOS Shortcuts / Android Accessibility / Shizuku）

#### 6.1 当前 remote worker 最小版

- 入口：`python -m backend.remote.worker`
- 默认地址：`http://127.0.0.1:8790`
- 默认端点：
  - `GET /health`
  - `GET /heartbeat`
  - `POST /execute`
- 鉴权头：`X-SpiritKin-Remote-Token`
- 当前默认 executor：`LocalPCExecutor`
- 当前默认 env：
  - `SPIRITKIN_REMOTE_WORKER_HOST`
  - `SPIRITKIN_REMOTE_WORKER_PORT`
  - `SPIRITKIN_REMOTE_NODE_ID`
  - `SPIRITKIN_REMOTE_TOKEN`
  - `SPIRITKIN_REMOTE_ALIASES`

中枢侧最小 HTTP client 已补在 `backend/executors/remote_protocol.py` 的 `HttpRemoteNodeClient`。

#### 6.2 当前中枢侧心跳轮询与面板展示

- 已补 `backend/executors/node_registry.py`：`refresh_all_from_clients()`、`snapshot()`，并在路由阶段跳过 `stale/offline` 节点。
- 已补 `backend/remote/poller.py`：`RemoteHeartbeatPoller` 后台线程，按 interval 主动刷新远端 worker 的 `/heartbeat`。
- 已补 `backend/app/runtime.py`：当 `node_registry` 里已有节点时，会自动启动 poller；`runtime.capabilities` 额外携带 `remote_nodes` 快照。
- 已补 `frontend/index.html`：Capability Dashboard 新增“远端节点”卡片，展示 `total / online / stale / offline` 与节点样例。
- 已补远端节点健康详情：`remote_nodes.nodes[]` 包含连续 heartbeat 失败次数和最后错误；`remote_nodes.recent_events[]` 记录 `heartbeat_ok / heartbeat_failed / heartbeat_stale`。
- 当前口径：前端展示只读快照，不会在每次页面刷新时直接阻塞式探测远端节点；真正网络探测由后台 poller 完成。

#### 6.3 接入一台真实远端 PC

远端 PC 上启动 worker：

```powershell
$env:SPIRITKIN_REMOTE_NODE_ID="office-pc"
$env:SPIRITKIN_REMOTE_TOKEN="secret-token"
$env:SPIRITKIN_REMOTE_ALIASES="公司电脑,office"
python -m backend.remote.worker
```

中枢机器通过环境变量或启动器参数注册该节点：

```powershell
python scripts/start_realtime_panel.py --lan `
  --remote-worker-url http://<远端IP或TailscaleIP>:8790 `
  --remote-node-id office-pc `
  --remote-worker-token secret-token `
  --remote-worker-aliases 公司电脑,office
```

也可写入 `config/config.yaml`：

```yaml
remote:
  workers:
    - node_id: office-pc
      url: http://100.64.0.8:8790
      token: secret-token
      aliases: [公司电脑, office]
```

注意：如果跨移动数据/公网访问，优先用 Tailscale/ZeroTier/VPN 或 HTTPS 隧道，不要裸露 worker 的 HTTP 控制口。

如果远端 PC 也在同一 Tailnet，建议 worker URL 直接使用它的 Tailscale IP：

```powershell
python scripts/start_realtime_panel.py --tailscale `
  --remote-worker-url http://<远端PC的TailscaleIP>:8790 `
  --remote-node-id office-pc `
  --remote-worker-token secret-token
```

实机 smoke test：

```powershell
python scripts/smoke_remote_worker.py `
  --url http://<远端IP或TailscaleIP>:8790 `
  --node-id office-pc `
  --token secret-token `
  --target desktop `
  --operation status
```

成功时会输出 JSON，包含 `heartbeat` 和 `execution` 两段；退出码为 `0`。

### 7. 如何逐步做到更像 LPM
LPM 关键不只是“回答问题”，而是：
- 持续 listening
- 持续 reacting
- 语音、表情、动作联动
- 对环境变化有连续表现

SpiritKinAI 更现实的路线应是：

#### 阶段 1：先做“原子操作 + 角色反馈”
- 语音触发原子操作
- 执行前后有情绪和播报反馈

#### 阶段 2：再做“实时状态机”
- listening
- thinking
- acting
- speaking

#### 阶段 3：再做“持续表演层”
- 说话时嘴型/表情联动
- 听到关键词时即时反应
- 视觉事件触发自然动作

#### 阶段 4：最后再考虑专门训练
- 收集多模态交互轨迹
- 建立评测集
- 优先做风格/路由/反应微调，而不是从零训大模型

### 8. 关于知识库与训练
- 知识库建议尽早接入，优先于重训练
- 训练前先有：评测集、工具轨迹、失败样本、人工反馈
- 训练顺序建议：`RAG -> tool routing -> evals -> SFT/LoRA -> 偏好优化`

### 9. 当前最现实的下一步
1. 把 `tool registry` 接入 planner / agent
2. 增加 `kb.search` tool 到实际工作流
3. 实现远端 worker 心跳与鉴权
4. 做流式语音输入与动作确认机制
5. 再逐步增强角色化、连续化表现