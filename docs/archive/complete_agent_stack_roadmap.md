## SpiritKinAI 完全体模型栈与参与工作路线

> 2026-06-12 current decision: Live2D is no longer part of the active roadmap. Historical Live2D notes in this file are retained only as old implementation context. The active avatar surface is the current Three.js / Bangboo GLB 3D panel.

### 当前完成度粗估
- 可演示闭环：约 40%～50%。语音/文本/手机网页入口、工具执行、确认门、事件桥、远端 worker、Workflow Memory、Live2D Web-first 入口均已可跑。
- 工程化个人助手：代码侧约完成。已补 Workflow Memory、候选 Skill、Skill 审核/升降权/持久化入口、dry-run replay harness、失败样本库、审计关联、候选 Skill replay 阈值验证、报告导出入口、RAG 增量索引 + reranker、权限中心、Android Companion 最小闭环和 iOS Shortcuts / App Intents Connector；剩余为 Android/iOS 真机 App 与外部设备验收。
- LPM 风格数字生命：代码侧基础闭环已完成。已补 Avatar Shell、口型事件、可打断 TTS 生命周期、Streaming ASR 事件/Wakeword Gate、LPM 状态事件、长期人格状态机、多智能体自我改进、Native 壳配置层和持续感知状态层；剩余为真实 Live2D 模型、真实语音/设备长时间体验与 Native 壳实机验收。

### 必须接入的基底模型
1. **LLM Reasoning**：通用推理、领域智能体、复杂计划。建议 Qwen2.5/3 7B～14B Instruct 起步。
2. **Intent Router / Tool Planner**：ASR 纠错、工具映射、参数补全。可以复用主 LLM 的 fast profile，后续蒸馏小模型。
3. **Vision-Language Model**：屏幕理解、图像/视频帧、手势。当前走 OpenAI-compatible Qwen-VL 配置。
4. **ASR**：语音转文本。当前 faster-whisper large-v3-turbo；低配可降 small/base。
5. **Wakeword**：唤醒词。当前 Whisper hotword 可用但不是最终；完全体建议 Porcupine/openWakeWord/Vosk。
6. **TTS**：语音输出。当前 pyttsx3；完全体建议 Edge-TTS/CosyVoice/Fish-Speech。
7. **Embedding**：长期记忆、RAG、workflow 召回。建议 bge-m3 / nomic-embed-text。
8. **Reranker**：知识库和 workflow 候选重排。建议 bge-reranker-v2-m3。
9. **Policy Guard**：权限、风险、确认、审计。规则优先，LLM 辅助判断。

### 让智能体参与工作的关键闭环
1. 接收输入：语音、文本、手机、远端节点事件。
2. 理解意图：统一纠错、库存/记忆/RAG 注入、ToolSpec 白名单映射。
3. 计划执行：Planner -> Skill -> Tool -> AtomicOperation -> Executor。
4. 参与工作：执行结果进入 Workflow Memory，失败进入 Repair Advisor，成功流程可升级为 Skill。
5. 表达反馈：TTS、Live2D、前端事件、手机通知同步反馈。
6. 复盘沉淀：workflow 统计 success_rate/frequency/recency，决定升权、降权或归档。

### Live2D 路线
- 当前已有 `avatar.state` 事件和 `frontend/live2d.html` 适配页。
- 若配置 Cubism `model3.json` URL，页面会尝试用 `pixi-live2d-display` 加载模型。
- 未配置或加载失败时，降级为 orb 表情，不阻塞其他功能。
- 桌面/Web 直接使用 `live2d.html` + WebGL；Android 优先 Chrome/WebView 复用同页；iOS 优先 Safari/WKWebView 复用同页但降低 DPR 和贴图规格。
- 详细策略见 `docs/live2d_mobile_strategy.md`。
- 后续要补：模型资源目录、表情/动作命名映射、口型同步、眨眼/呼吸、打断说话动画。

### P1 代码侧已完成（Engineering-ready）
- Android Companion 最小版：已补 `device.status`、`software.list_installed`、`app.launch`、heartbeat 状态上报、installed_apps 上报、command queue / pending_commands 拉取；待真机 Companion App 对接验收。
- iOS Shortcuts / App Intents connector：已补白名单动作、Webhook/URL Scheme schema、App Intent payload 映射、unsupported action 拒绝；待真实 iPhone Shortcuts / App Intents 对接验收。
- Skill 正式升级链路：已补候选审核、升权/降权/归档/拒绝状态、版本化、持久化 Skill Registry 入口、候选 Skill replay 阈值验证；待人工审核 UI 细化。
- Harness / Replay / Eval 二期：已补失败样本库、候选 Skill replay 阈值验证、审计联动、报告导出参数、Dashboard 报告命令入口与 `frontend/replay_report.html` 可视化报告页。
- RAG 增量索引、引用、rerank、Obsidian / Markdown vault connector：已补 chunk citation、DirectoryWatcher、IncrementalKnowledgeIndexer、ObsidianVaultConnector、embedding retriever 默认 reranker 与 rerank metadata；待持久化向量库/混合检索作为后续增强。
- 权限中心工程化：已补 PermissionCenter、能力授权 glob、策略拦截、速率限制、审计 query/export_jsonl 脱敏导出、CommandGateway 公网/token 安全上下文，并新增 `frontend/audit_report.html` 审计详情查看页。

### P2 代码侧已完成（LPM-Complete 基础闭环）
- Streaming ASR 事件 + Wakeword Gate：已具备流式状态事件、partial/final transcript 与唤醒门控基础。
- 可打断 TTS + Live2D 口型：已具备 speech lifecycle、phoneme/mouth-shape timeline、Avatar Shell 与 Live2D mouth-open 映射基础。
- Long-term Memory / 人格状态机：已具备 LPM state、companion mode、presence/memory/personality 事件。
- 多智能体调度与自我改进：已具备 performance / trajectory / failure sample 聚合、eval cases 和 improvement actions。
- Native Live2D 壳与多端形象层：已具备桌面/Android/iOS WebView profile manifest 基础。
- 持续感知与主动陪伴：已具备 context observation、active_contexts、recent_observations、proactive_suggestion 状态层。

### P2 当前推进记录
- Avatar Shell 前端统一：已把 `frontend/spirit_avatar.html` 从 DS 生成的浏览器 ASR/TTS 玩具页改造成 P2 角色前台，只消费 runtime WebSocket 事件，接 Command API，支持确认流、移动端 URL、`performance.state` / `avatar.state` / `speech.phoneme` 预留，并在 `index.html` 与 `scripts/start_realtime_panel.py` 输出入口。
- 口型事件链路：Runtime 已在 assistant reply 后生成 `speech.phoneme` 事件，Avatar Shell 按 `timestamp_ms` 播放 mouth-shape 时间线，`live2d.html` 尝试把 mouth shape 映射到 `ParamMouthOpenY`，为后续真实 TTS/Live2D 口型同步打基础。
- LPM 状态事件：Runtime 已在每轮回复后记录 MemoryOrchestrator interaction，并向前端发 `presence.updated`、`memory.updated`、`personality.updated`；capabilities 暴露 `lpm_state_events` 与 memory snapshot，Avatar Shell 可显示 persona / memory / presence。
- 可打断 TTS 生命周期：`SpeechController` 已支持可选 `event_sink`，播报期间发 `speech.started`、`speech.phoneme`、`speech.interrupted`、`speech.ended`；`RealtimeDuplexSession` 默认把 speech 事件接到 runtime event bridge，Avatar Shell / Live2D 会根据生命周期事件更新 speaking / interrupted / mouth idle 状态。
- Streaming ASR 事件与 Wakeword Gate：`StreamingSession` 已支持 `event_sink`，输出 `asr.speech_started`、`asr.partial`、`asr.final`；新增 `StreamingWakewordGate`，支持 hotword 激活、wake window、`cleaned_text` 和 `wake_required` 门控，`create_streaming_listener()` 可启用独立 wakeword gate。
- 长期记忆与人格状态机：`PersonalityState` 已新增 `companion_mode()` 与 `lpm_state()`，输出 mode / mood / energy / familiarity / reliability / session_minutes；`MemoryOrchestrator.snapshot()` 暴露 `lpm_state`，供 Avatar Shell / 前端展示长期陪伴状态。
- 多智能体自我改进闭环：已新增 `SelfImprovementLoop` / `SelfImprovementReport` / `ImprovementAction`，聚合 Agent performance、trajectory bottleneck、failure samples，生成 eval cases 与改进动作；AgentCluster 默认记录 performance 与 failure trajectory，并暴露 `build_self_improvement_report()`。
- Native Live2D 壳与多端形象配置层：已新增 `AvatarShellProfile`、`build_avatar_shell_profile()`、`build_multi_end_avatar_manifest()`，统一桌面 WebView、Android WebView、iOS WebView 的 Avatar/Live2D URL、窗口参数和能力声明，作为未来 Native 壳 / 原生 SDK 接入配置基础。
- 持续感知与长期陪伴：`PresenceManager` 已支持 `record_context()`，可记录 app / screen / task / calendar / project 等上下文，snapshot 暴露 `active_contexts`、`recent_observations`、`proactive_suggestion`，为长期在线与主动陪伴提供状态层。
