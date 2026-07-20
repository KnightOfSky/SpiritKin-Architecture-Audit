# 核心方案分批验收记录

- 日期：2026-07-18（在 2026-07-17 首轮记录上复核）
- 环境：Windows 11 / Python 3.12 / .NET 8 / NVIDIA RTX 5060 Ti 16GB / Atelier 夜间主题
- 范围：`core_system_improvement_plan.md` M1-M18、llama.cpp 迁移、M3/M14 缺失 UI
- 结论：代码、本机自动化、WPF 与指定 Edge 浏览器验收通过；真机、真实账号和长时媒体项进入后续人工验收

## 批次 A：M1-M6 核心架构与安全

| 场景 | 实际 | 结果 |
|---|---|---|
| 真实 Embedding/fallback、长期记忆、激活度 | 定向用例覆盖；七日前隔离偏好跨重启后由未降级的 llama.cpp 768 维 Embedding 命中、激活并注入 Soul | 通过 |
| AgentCluster 门面、纯路由、Soul Phase 无工具 schema | `AgentCluster` 548 行，ClusterDeps/Wiring 兼容；定向用例通过 | 通过 |
| 动态 Authz、四级风险、双重 Safety | WPF 与真实网关原值回写成功；授权后仍走 Safety | 通过 |
| stderr/self-heal、分类重试、fatal 零重试 | failure_context、退避和自愈轨迹用例通过；隔离 venv 真实缺包后安装 `colorama==0.4.6` 并自动重跑成功 | 通过 |
| Avatar 语义反应和 idle 边界 | 后端语义反应与 Node 边界通过；4 秒呼吸、7 秒重心、2-6 秒随机眨眼、300ms 表情缓动及明暗灯光已补齐 | 通过 |
| orchestrator 反向依赖 | AST/架构端口测试通过 | 通过 |

批次结果：`110 passed, 3 subtests passed`。

## 批次 B：M7-M11 协议、生态与运维

| 场景 | 实际 | 结果 |
|---|---|---|
| MCP stdio/HTTP/SSE、安全、重试、健康降级 | MCP 定向测试全绿 | 通过 |
| manifest 自动注册/Authz/工作流节点目录/冲突优先级 | 合法、非法、内置保护、多根冲突与 schema 映射通过；仅 `manifest.json + echo.py` 实际输出 `m8-manifest-ok` | 通过 |
| Device/Search/流式 ASR | Local/Android 契约、Brave/DDG 超时、faster-whisper/VAD 用例通过 | 通过 |
| Remote Worker | 直接 Worker `18790` 往返 `ok:true`；PyInstaller one-file EXE 8,750,245 bytes，执行中断联后 outbox=1，恢复后 875ms 回传、outbox=0、任务 `completed` | 通过 |
| Audit/Replay 增量 | 游标二次调用不重读，报告契约通过 | 通过 |
| PDD 扩展/productData/电商工作流 | Python 链路用例通过，Node normalizer 3/3 | 通过 |
| Feishu/Reviewer/persona/LangGraph/训练命令 | 配置、重试、工具范围、适配器和人工训练命令用例通过 | 通过 |

批次结果：`118 passed, 2 subtests passed`；Remote Worker smoke `ok:true`；PDD Node `3/3`。

Control Plane 断联恢复证据位于 `tmp/control-plane-worker-recovery-20260717/`：`control-plane/control_state.json` 中任务 `wtask_1784297728644_a92d6016ffff` 为 `completed` 且结果 `ok:true`，`worker/outbox/` 最终为空，控制面进程日志见同目录 `control-plane.log`。

EXE 断联恢复证据位于 `tmp/control-plane-worker-exe-recovery-20260717/`；单文件产物为 `dist/spiritkin-control-plane-worker.exe`，签名下载包为 `state/workers/releases/spiritkin-control-plane-worker.zip`。M4/M8 证据分别位于 `tmp/execution-repair-20260717/` 与 `tmp/manifest-echo-20260717/`。

## 批次 C：M12-M18 陪伴与交互

| 场景 | 实际 | 结果 |
|---|---|---|
| 主动策略与关系边界 | 安静时段、冷却、上限、显式边界/撤回、Soul 注入通过；边界跨重启去重、解除和再次重启清零 smoke 通过 | 通过 |
| APScheduler 与 WPF 调度 | date/interval/cron、misfire、幂等、安全门通过；网关 PID `27124 -> 13240` 后近时任务于计划时间后 0.52s 变为 `complete`，SQLite delivery 恰好 1 条 | 通过 |
| 开场气泡 | 状态机、优先级、去重、低动效、反馈、只导航不执行通过 | 通过 |
| 语音通话 | 状态转换、VAD、字幕、打断、权限错误和结束清理通过 | 通过 |
| 音乐播放器 | NAudio 队列、seek、音量、损坏跳过、AudioFocus、路径/网络授权通过 | 通过 |
| 游戏自动化 | 本地 Demo 白名单、焦点、Kill Switch、速率、未知画面、审计通过 | 通过 |
| Edge 浏览器 | Avatar 1440x900/1024x768/390x844 完整；开场气泡移动端通过；游戏回收/暂停/继续/Kill Switch/移动视口通过 | 通过 |
| 合成语音 | Edge TTS 中文 MP3/VTT 生成并由本地 faster-whisper 识别，无需麦克风或播放声音 | 通过 |

批次结果：`62 passed`。

## llama.cpp 与桌面 UI

| 场景 | 实际 | 结果 |
|---|---|---|
| llama.cpp 安装 | b10058 / commit `788e07dc9`，官方 SHA-256 校验一致，CUDA 识别 RTX 5060 Ti | 通过 |
| 服务切换 | LM Studio `1234` 无监听；Qwen `8080` 与 Nomic `8081` 健康 | 通过 |
| 自动启动去重 | 冷启动两个进程；WPF 重启后仍为同一两个 PID，端口仅 `8080/8081` | 通过 |
| 对话/Embedding | OpenAI-compatible chat 与 768 维 embedding 实际请求成功 | 通过 |
| 授权与调度 UI | 1480×940 与声明最小尺寸 1280×760 可见；列表、编辑器、按钮无横向裁切 | 通过 |
| 网关契约 | 126 个工具可读；授权原值回写；测试提醒最终 `cancelled` | 通过 |
| 当前桌面全动作 | `demo.ok` 禁用/真实拒绝/恢复成功；date 任务在 UI 完成暂停、恢复、立即运行、取消和 UTC 时区更新 | 通过 |
| M5/M17 当前视图 | 嵌入 Avatar 画布非空白；音乐底栏与队列弹层完整可见且未触发音频 | 通过 |
| 服务自重启响应 | 真实 POST 收到 `restart_scheduled` 与完整 `operations`，旧网关退出前客户端不再误报连接失败 | 通过 |
| Android APK | `.7` 候选包由最新源码重建，compile/target SDK 35，v1/v2/v3 签名通过；普通按钮触控高度修正为 48dp。当前分发批准仍为 `.4` | 本机构建通过，待真机复核和人工批准 |

## 最终门禁

- 全量 Python（包含 M1/M13 持久化 smoke 对应链路、M5 精确动效边界与完整模块语法、M4/M8/M10 完成性修复、M14 提交顺序、llama.cpp 协作轨迹分类和 Android 48dp 触控目标回归）：`1604 passed, 20 subtests passed`。
- M14 到期时序：调度模块 `9/9`，一次性任务“先持久化 complete、后发布事件”重复 `12/12` 通过。
- 根目录项目验证脚本：`212 passed`，Ruff pass。
- WPF：build `0 warning / 0 error`，desktop tests `271/271`。
- 台账：`docs/test-ledger.md` 已追加 PASS 行。
- 独立记录：`M1_manual_test.md` 至 `M18_manual_test.md` 全部存在，索引见 `docs/manual_tests/README.md`。
- 当前 UI 审计：`docs/manual_tests/2026-07-18_governance_ui_audit.md`，包含 WPF 实操步骤、截图和未触发媒体权限的边界说明。
- Edge 验收：`docs/manual_tests/2026-07-18_edge_browser_acceptance.md`，包含多视口截图、FPS 和游戏停止键证据。

## 外部环境人工验收队列

以下项目需要用户提供对应设备、账号或媒体许可，不能用 mock/静态截图冒充完成：

1. 真实远端 PC：跨机往返延迟、物理拔网线 30 秒自动恢复、安装包升级与长时 heartbeat；本机独立双进程断联/outbox 恢复已通过。
2. Android 真机：ADB/Bridge 截图、Accessibility/MediaProjection、麦克风拔插与后台采集检查。
3. PDD 测试账号和本地登录 Profile：真实商品 rawData、草稿创建录屏、人工审核后发布或保持草稿。
4. 长时媒体：真实麦克风语音连续 10 分钟、音乐连续 30 分钟、TTS duck/恢复与文件移动/删除恢复；合成语音文件闭环已通过。
5. 目标低端硬件：若需要代表低端机，另记录 Avatar FPS；当前 Edge 在 1440x900/390x844 分别为 43.03/44.91 FPS。

这些条目是分批人工验收输入，不是未实现代码清单。每项完成后在本文件追加设备、步骤、实测结果和证据路径。
