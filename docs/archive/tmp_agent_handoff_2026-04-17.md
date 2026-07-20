## 临时交接文档：剩余任务与下一步

### 当前总方向
- 项目目标：把 SpiritKinAI 推进成“个人专属智能体集群助手 / LPM 风格”。
- 优先级：`Ecommerce > 视频/动画/编程/游戏 > 通用`。
- 当前主线：让语音、文本、前端事件、执行器、确认门都进入统一智能体闭环。
- OpenClaw 策略：继续优先软件层与展示体验，暂不深挖真实机械硬件驱动。

### 剩余任务

0. **本轮新增总方向：原子操作 / Skill / 记忆 / 知识库工程化**
   - 总方向不变：继续推进“统一智能体闭环”，但要把能力层次拆清楚。
   - 新增核心分层：`Planner -> SkillRegistry/SkillRunner -> ToolRegistry -> AtomicOperation -> Executor -> Device/Connector`。
   - 原子操作负责最小、确定、可审计动作；Skill 负责多步流程、确认点、回滚、记忆读写和复用。
   - 不建议盲目追求“自动生成大量 Skill”；优先做真实 workflow 记录、验证、沉淀、降权和升级。
   - 目标规模参考：MVP 先做到 30~50 个原子操作、10~20 个 Skill；通用个人助理阶段做到 80~120 个原子操作、40~80 个高质量 Skill。

1. **通用语音智能体链路实机验证与收尾**
   - 用 `python scripts/smoke_asr.py --route-agent` 实测自然语音指令。
   - 重点验证 ASR 错词纠错，例如“机械B”“非书”“怎么装它”。
   - 检查链路是否真实经过 `IntentResolver -> ToolSpec -> Executor -> 确认门`。
   - 失败时优先看 `reply.metadata.intent_resolution` 与执行器 metadata。
   - 注意：`--route-feishu` 仍是飞书专用 smoke，通用语音入口应使用 `--route-agent`。

2. **IntentResolver / AgentCluster 继续增强**
   - 收集真实 ASR 错误样本，补到意图解析提示词、工具别名或测试中。
   - 优化澄清流：不确定接收人、目标设备、执行参数时，要追问而不是硬执行。
   - 增强联系人、设备、应用别名映射，减少固定句式依赖。
   - 保持 ToolSpec 白名单校验，防止 LLM 编造不存在的能力。
   - 高风险动作继续走确认门，不能因为 LLM 解析成功就直接执行。

3. **前端最小状态面板 / 展示闭环**
   - 消费 runtime 事件，至少展示：
     - `assistant.message`
     - `assistant.execution_updated`
     - `avatar.state`
     - `device.openclaw_state_updated`
   - UI 最小状态建议：监听中、理解中、等待确认、执行中、成功、失败、澄清。
   - OpenClaw 面板字段建议：节点、位置、夹爪状态、设备状态、最近命令、transport。
   - 目标：用户能直观看到“耳朵听见了、智能体理解了、动作执行了、脸有反馈”。
   - 本轮发现：`frontend/index.html` 默认连接 `ws://127.0.0.1:8765`，但 `backend.main` 不是 WebSocket 服务端，必须另启 `python -m backend.app.realtime_bridge`。
   - 已补：`scripts/start_realtime_panel.py` 可一键启动事件桥、runtime、前端 HTTP 服务、手机/网页命令网关；也支持 `--no-runtime` 分终端看日志。
   - 已补：一键启动默认启用更快热词配置与语音 LLM 意图纠错：`SPIRITKIN_HOTWORD_FAST=1`、`SPIRITKIN_HOTWORD_BEAM_SIZE=1`、`SPIRITKIN_VOICE_INTENT_MODE=first`。
   - 已补：前端自动重连、Bridge 未启动提示、最后事件时间、启动命令提示、网页/手机文本指令输入框。
   - 已补：`--lan` 模式会绑定局域网地址并自动生成 `SPIRITKIN_MOBILE_TOKEN`，iOS/Android 浏览器可访问本机面板并通过 HTTP 命令网关发送指令。
   - 待补：Capability Dashboard，显示当前可用原子操作、Skill、设备、软件/硬件扫描数量、记忆库、RAG 后端和模型状态。

4. **统一语音 / 文本交互与确认门体验打磨**
   - 文本和语音输入都应进入同一 `Runtime -> AgentCluster` 主链路。
   - 确认、取消、澄清、失败反馈的话术需要统一。
   - 语音回复需要更短，文本面板可以保留详细原因和 metadata。
   - 连续会话中要减少重复唤醒和机械确认感。
   - 已补：热词监听默认更偏低延迟，`SPIRITKIN_HOTWORD_TIMEOUT` 默认 0.8s、`SPIRITKIN_HOTWORD_PHRASE_TIME_LIMIT` 默认 1.0s，且最小值做了保护。
   - 已补：语音通道默认优先经 `IntentResolver` 纠错，再落到 ToolSpec/Executor；可用 `SPIRITKIN_VOICE_INTENT_MODE=fallback` 回退为规则优先。

5. **development plan v1 继续完善**
   - 把“新增软件/API 接入”的请求转成可审核开发计划。
   - 输出应包含：目标、风险、接口边界、实施步骤、测试计划、回滚策略。
   - 后续可用于飞书、浏览器、本地应用、第三方 API 等接入前的安全规划。

6. **电商阶段状态机 v1 继续深化**
   - 继续补售前、售后、复盘、长期运营状态。
   - 保留阶段防回退逻辑。
   - 后续要能把任务队列、项目 metadata、前端项目面板联动起来。

7. **更深部署验证 B1**
   - 在已有 compose 配置检查基础上，继续做：
     - `docker compose build`
     - 容器启动 smoke test
     - runtime 事件通道 smoke
   - 运行前注意确认是否会拉镜像或耗时较长。

8. **AutoProcessAP 对接评估入口**
   - 后续再评估 `E:\AutoProcessAP` 的复用边界。
   - 先判断它适合作为工具、执行器、知识库，还是独立 agent 能力来源。

9. **原子操作清单补全与正式注册**
   - 当前已确认口径：`atomic_operations.py` 中默认跨设备原子动作已包含 app 启动/关闭、屏幕、输入、软件清单、硬件清单等基础能力；默认 Tool Registry 暴露仍需继续扩展。
   - 已补齐并注册：`software.list_installed`、`hardware.list_devices`、`app.close`；扫描类操作会返回自然语言摘要，避免前端/语音“无反馈”。
   - 已补：软件扫描结果会注入 session metadata，后续 LLM 意图纠错可利用真实本机软件清单，把“不常用软件/ASR 错词”映射回已安装应用。
   - 已确认：`LocalPCDevice.find_installed_app()` 会复用扫描缓存；因此“先扫描本机软件 -> 再打开/关闭某软件”的路径可行。
   - 已补：扫描库存从全局列表升级为按设备/远端节点分组，LLM prompt 会出现 `[本机] 软件=...`、`[office-pc] 软件=...`，避免跨设备库存混淆。
   - 已补：远端节点扫描返回后会记录 `scope=remote:<node_id>:<target>`，后续“公司电脑上的火爆浏览器”可利用对应远端库存纠错。
   - 下一批建议补：`window.list`、`window.activate`、`window.close`、`screen.capture`、`clipboard.read`、`clipboard.write`、`file.read`、`file.search`、`file.open`、`browser.open_url`、`browser.search`。
   - 高风险能力如 `file.write`、`file.delete`、知识库覆盖写入、外部消息发送、机械臂移动等必须继续走确认门。
   - 原子操作定义需包含：名称、operation、参数 schema、风险等级、是否只读、目标类型、确认策略和测试样例。

10. **Skill 层骨架**
   - 新增 `SkillSpec`、`SkillRegistry`、`SkillRunner`，不要继续把多步业务流程塞进 Planner。
   - Skill 应包含：名称、描述、触发意图、输入 schema、前置条件、步骤、工具白名单、风险等级、确认策略、回滚策略、成功判定、记忆策略、eval cases、使用统计和版本。
   - 首批 Skill 建议：打开浏览器并搜索、扫描并打开本机软件、读取屏幕并总结、把当前内容整理到知识库、发送飞书消息、OpenClaw 状态检查与归位、机械臂夹爪基础流程。
   - Skill 不应默认由模型直接生成并入库；应先进入草稿/候选状态，经 Harness 验证和用户确认后再升级为正式 Skill。

11. **Workflow-memory 与遗忘机制**
   - 建立真实执行流程记录：输入、计划、调用的原子操作、结果、失败原因、用户反馈、耗时、上下文。
   - 高频成功 workflow 可升级为正式 Skill；低频、失败率高、过期或冲突多的 workflow 自动降权或归档。
   - 权重参考因素：frequency、recency、success_rate、user_feedback、task_similarity、risk_penalty、staleness、conflict_count。
   - 遗忘机制优先做“降权/归档”，不要直接删除用户可能还需要的历史流程。

12. **统一记忆入口 / Memory Orchestrator**
   - 需要一个统一入口串起对话处理、短期记忆、长期记忆、工作记忆、事件记忆、知识库召回、workflow-memory 召回和写回。
   - 建议记忆分类：Short-term Memory、Working Memory、Long-term Memory、Episodic Memory、Semantic/Wiki Memory、Procedural/Workflow Memory。
   - OpenClaw 也要接入记忆：设备状态、最近动作、错误记录、安全区域、成功/失败操作经验、常用动作流程。
   - 长期记忆和知识库写入要有置信度门槛，涉及覆盖/删除/外发时必须确认。

13. **知识库、Obsidian、RAG 与 Wiki 自动整理**
   - Obsidian 建议作为 `MarkdownVaultConnector` / `ObsidianConnector` 适配器，不要写死成唯一知识库。
   - RAG 需要升级：文档摄取、chunk、embedding、hybrid search、rerank、引用来源、增量索引、过期检测。
   - Wiki 自动整理采用“草稿 -> diff -> 用户确认 -> 写入”的流程，禁止默认静默覆盖用户笔记。
   - 知识库原子能力建议：`kb.search`、`kb.read`、`kb.upsert_draft`、`kb.link_suggest`、`kb.tag_suggest`、`kb.archive`。
   - 可借鉴 Karpathy wiki 风格的自动整合：主题提取、知识卡片、标签、双链、查重、合并建议和召回权重更新。

14. **MCP / Harness / Hermes 取舍**
   - MCP 建议做适配层，把外部 MCP tools 映射成内部 `ToolSpec`，但内部仍走本项目的权限、风险、确认和审计机制。
   - Harness 必须做：dry-run、mock 工具、replay、Skill eval、workflow eval、权限校验、失败归因、执行日志和测试报告。
   - Hermes 不建议作为核心依赖；可借鉴其 Skill/Workflow 思路，但本项目应以统一入口、workflow-memory、RAG/Wiki、遗忘机制和 Harness 验证为核心。
   - 如果后续需要消息总线，可先做内部 Runtime/Task/Skill/Device/Confirmation event，再考虑 Hermes 风格或其他协议兼容。

15. **软著申请预备工作**
   - 提前整理正式工程材料：软件名称、版本号、模块说明、运行环境、功能清单、操作说明、源代码页选择建议。
   - 正式材料建议使用工程化表述：原子操作抽象层、复合技能编排模块、知识库检索增强模块、任务执行验证框架、设备协同控制模块。
   - 不建议把临时 handoff、smoke 脚本、debug 输出、测试替身、模型下载缓存、第三方库、密钥配置放入软著核心材料。
   - 注意合规边界：可以清理临时痕迹和整理正式文档，但不能伪造开发过程或规避审查要求。

16. **iOS / Android 远程操控与手机节点接入**
   - 已补本机接收手机指令入口：`backend/app/command_gateway.py`，HTTP `POST /command` -> `SpiritKinRuntime.handle_input(channel="mobile")` -> `AgentCluster`。
   - 已补一键启动 LAN 模式：`python scripts/start_realtime_panel.py --lan`，手机同 Wi-Fi 打开 `http://<本机IP>:8787/index.html`。
   - 安全口径：LAN 命令网关必须使用 token；公网必须走 HTTPS/VPN/反向隧道，不能裸露 HTTP 控制口。
   - iOS 手机被本机控制：优先通过快捷指令、URL Scheme、App Intents、Webhook 暴露白名单动作；不能绕过系统沙盒任意采集 App 数据或模拟全局点击。
   - Android 手机被本机控制：优先 Companion App；更深控制需用户授权 Accessibility Service / Notification Listener / Shizuku / ADB，不做隐蔽采集。
   - 后续代码落点：`backend/mobile/` companion 协议适配，手机作为 `RemoteNode` 上报 `capabilities`，再由 `RemoteExecutor` 下发白名单原子操作。

17. **基底模型栈 / Live2D / 智能体参与工作**
   - 已补文档：`docs/complete_agent_stack_roadmap.md`，记录完全体需要的 LLM、Intent Router、VLM、ASR、Wakeword、TTS、Embedding、Reranker、Policy Guard。
   - 已补 runtime capabilities：`recommended_model_stack` 会随 `runtime.capabilities` 发给前端，用于后续 Capability Dashboard。
   - 已补 Live2D 独立页：`frontend/live2d.html`，消费 `avatar.state`，可配置 Cubism `model3.json` URL；加载失败时降级为 orb。
   - 已补 Workflow Memory 最小实现：`backend/memory/workflow.py`，AgentCluster 每次原子执行会记录 workflow_record，为后续 Skill 沉淀和智能体参与工作复盘打基础。
   - 完全体粗估：可演示闭环 35%～45%；工程化个人助手 25%～35%；LPM 风格数字生命 15%～25%。

18. **Live2D 移动端/桌面端策略与启动入口**
   - 已补文档：`docs/live2d_mobile_strategy.md`。
   - 桌面/Web：推荐直接打开 `frontend/live2d.html`，浏览器 WebGL 渲染 Cubism 模型，通过 WebSocket 消费 `avatar.state`。
   - Android：第一阶段用 Chrome 访问同页；第二阶段 Companion App 内嵌 WebView；第三阶段才考虑原生 Live2D SDK。
   - iOS：第一阶段 Safari/PWA；第二阶段 WKWebView 壳；因为 WebGL/内存/全屏/音频手势限制，要限制 DPR 和贴图尺寸。
   - `frontend/live2d.html` 已支持 `ws`、`model`、`autoload`、`maxDpr`、`scale`、`mobile` URL 参数，移动端会提示触摸全屏并在 WebGL/模型失败时降级 orb。
   - `scripts/start_realtime_panel.py` 会打印 `[live2d]` 形象页地址；`--lan` 会额外打印手机 Live2D URL 示例。

19. **Capability Dashboard 最小前端展示**
   - 已更新 `frontend/index.html`：新增 Capability Dashboard、模型栈、入口三张卡片。
   - Dashboard 消费 `runtime.capabilities`，展示核心 supports 在线数、Text/Vision/ASR 模型、默认模式、channels、recommended_model_stack 摘要。
   - 入口卡片会按当前页面 host 自动生成桌面 Live2D、移动端 Live2D URL、Command API、WebSocket 地址。
   - 当前仍是前端最小展示；后续应继续把 ToolSpec、SkillSpec、设备 inventory、Workflow Memory 数量和权限中心状态接入同一面板。

20. **移动数据访问与公网/隧道入口**
   - 重要口径：`--lan` 只适合同一 Wi‑Fi/LAN；手机走 4G/5G 移动数据时，`http://<本机局域网IP>` 和 `ws://<本机局域网IP>` 不可达。
   - 中国区更新：若 Tailscale 手机端下载不到，优先改用 frp/自有 HTTPS 隧道；手机端不需要安装额外 App，直接浏览器访问 HTTPS/WSS。
   - 推荐方案：国内长期使用优先 frp + 自有域名/云服务器；国际区可用 Tailscale；临时演示可用 Cloudflare Tunnel/ngrok；工程化阶段应做云端 relay/message broker。
   - 已更新 `docs/remote_control_and_realtime_voice_plan.md`，补充移动数据访问策略、安全边界和示例命令。
   - 已增强 `scripts/start_realtime_panel.py`：新增 `--public-frontend-url`、`--public-events-ws-url`、`--public-command-url`，用于打印移动数据/公网隧道入口；脚本不会自动暴露公网。
   - 已增强 `scripts/start_realtime_panel.py`：新增 `--tailscale` / `--tailscale-ip`，会绑定 `0.0.0.0`，尝试执行 `tailscale ip -4`，打印手机移动数据可用的 Tailnet Frontend / Live2D / WebSocket / Command API 地址。
   - 已增强 `scripts/start_realtime_panel.py`：新增 `--frp-domain-suffix` / `--frp-prefix` / `--frp-http`，可按 `spiritkin.example.com`、`spiritkin-events.example.com`、`spiritkin-command.example.com` 自动推导公网入口。
   - 已新增 `scripts/generate_frp_config.py`：生成 frpc.toml 模板，映射 frontend/events/command，可选 remote worker。
   - 公网安全要求：HTTPS/WSS + Token/VPN/访问控制，不要裸露 HTTP 控制口；高风险动作仍走确认门。

21. **桌面原子操作第二批已接入**
   - 已补 `backend/action/atomic_operations.py`：新增 `screen.capture`、`clipboard.read/write`、`browser.open_url/search`、`window.list/activate/close`。
   - 已补 `backend/devices/local_pc.py`：本机实现默认浏览器打开 URL/搜索、Windows 剪贴板读写、截图保存、窗口列表/激活/关闭。
   - 已补 `backend/executors/local_pc_executor.py`：新增对应执行分发和自然语言结果总结。
   - 已补 `backend/orchestrator/planner.py`：支持“搜索 xxx”“读取剪贴板”“关闭 XXX 窗口”“截图/截屏”等自然语言路由。
   - 安全口径：`clipboard.read`、`clipboard.write`、`window.close` 走 high risk 确认门；`window.list` 为只读；`browser.open_url/search`、`screen.capture` 当前为中风险。
   - 当前限制：窗口操作目前仅支持 Windows；窗口匹配基于标题包含；浏览器搜索当前只支持 `bing/google/baidu` 模板；截图返回文件路径，不做对象存储上传。

22. **文件原子操作第三批已接入**
   - 已补 `backend/action/atomic_operations.py`：新增 `file.search`、`file.read`、`file.open`。
   - 已补 `backend/devices/local_pc.py`：支持在限定根目录内按文件名搜索、读取文本文件内容、用系统默认程序打开文件/目录。
   - 已补 `backend/executors/local_pc_executor.py`：增加文件搜索/读取/打开的执行分发与结果总结。
   - 已补 `backend/orchestrator/planner.py`：支持“搜索文件 xxx”“读取文件 xxx”“打开文件 xxx”等自然语言路由。
   - 安全口径：`file.search` 为只读；`file.read` 为 high risk，需要确认；`file.open` 为 medium risk。
   - 当前限制：文件搜索默认根目录优先 `SPIRITKIN_FILE_SEARCH_ROOT` 或当前工作目录；`file.read` 当前按文本读取 UTF-8/ignore，不做二进制解析；`file.open` 目前走系统默认关联程序。

23. **为什么 Live2D 当前桌面端/移动端先走网页**
   - 这不是最终形态，而是当前阶段的 Web-first / WebView-first 策略。
   - 原因 1：统一事件协议。后端只发 `avatar.state`，桌面浏览器、Android WebView、iOS Safari/WKWebView 先共用一套承接页。
   - 原因 2：最快形成跨端闭环。当前更缺的是语音、执行、记忆、远程控制和状态闭环，不适合现在就分平台重写原生 Live2D SDK。
   - 原因 3：降低多端维护成本。先把模型资源、expression/motion 命名、口型同步和状态机稳定，再进入原生 SDK 阶段更合理。
   - 当前路线：桌面/Web/Android/iOS 统一先走 `frontend/live2d.html` 或 WebView/Safari/PWA。
   - 后续路线：Android 原生壳、iOS 原生壳、桌面壳/悬浮层已经进入总任务清单中的长期 backlog（`Native Live2D 壳与多端形象层`）。

24. **Workflow Memory 增强版已接入**
   - 已补 `backend/memory/workflow.py`：新增 `SQLiteWorkflowMemory`，并保留 `JsonlWorkflowMemory` 兼容；`build_workflow_memory()` 会按 `.sqlite/.sqlite3/.db` 或 `.jsonl` 后缀选择后端。
   - 已补 `backend/app/settings.py`：`resolve_workflow_memory_path()` 默认路径改为 `state/workflow_memory.sqlite3`，仍可用 `SPIRITKIN_WORKFLOW_MEMORY_PATH` 覆盖。
   - 已补按 `operation/target/device/success` 召回的 `query()`、跨会话 `stats()`、`archive_before()` 归档压缩到 `.jsonl.gz`、以及 `skill_candidates()` 候选 Skill 接口。
   - 已补 `backend/orchestrator/agent_cluster.py` 与 `backend/app/runtime.py`：`runtime.capabilities.workflow_memory` 现在带 recent、stats、skill_candidate_count 和最多 5 个 Skill 候选。
   - 当前行为：每次原子执行都会写入 SQLite；启动时会加载已有记录并延续 `wf-000001` 这类 workflow_id 计数；JSONL 路径仍可用于轻量/兼容模式。
   - 当前限制：还没有把 Skill 候选自动升级为正式 Skill，也没有做复杂相似度召回/去重/降权策略；后续可接 Memory Orchestrator 与 Skill Registry。

25. **Capability Dashboard 二期已接入**
   - 已补 `backend/app/runtime.py`：`runtime.capabilities` 现在额外携带 `tooling`、`inventory`、`workflow_memory`、`safety` 四类快照。
   - `tooling`：Tool/Skill 数量与样例；`inventory`：software/hardware/device scope 计数；`workflow_memory`：recent_count 与 latest workflow；`safety`：当前是否等待确认及 pending target/operation。
   - 已补 `frontend/index.html`：新增“工具 / Skill / Workflow”“库存 / 确认门”卡片；页面连接后会立即显示能力快照，并在执行/确认事件到来时更新 workflow 与 pending confirmation 状态。
   - 当前限制：inventory 的实时增量主要依赖事件中的 `inventory_update` 简要信息；完整库存细节仍以后续专门事件或刷新后的 `runtime.capabilities` 为准。

26. **Live2D 资源与动作映射 skeleton 已接入**
   - 已补 `frontend/models/README.md`：规定模型目录结构和 manifest 字段。
   - 已补 `frontend/models/manifest.example.json`：给出 `defaultRole`、`model`、`scale`、`expressions`、`motions` 的示例骨架。
   - 已补 `frontend/live2d.html`：支持 `role` / `config` URL 参数，会尝试加载 `models/manifest.json`，按角色名解析 model3.json、emotion -> expression、action -> motion 映射。
   - 当前行为：即使没有真实模型资源也不会卡死；manifest/模型缺失时继续降级 orb。
   - 当前限制：尚未内置真实模型资源，口型同步/音频驱动动作也还未接入；当前只是资源组织和映射骨架。

27. **Live2D 资源实装入口已接入**
   - 已新增 `frontend/models/manifest.json`：默认角色 `spirit`，包含 emotion -> expression 与 action -> motion 映射；`ready=false` 表示真实模型资源尚未放入。
   - 已增强 `frontend/live2d.html`：加载 manifest 后展示资源状态；当角色 `ready=false` 且用户未显式指定模型时，不会反复请求缺失模型，而是稳定降级 orb。
   - 已增强 `frontend/index.html` 与 `scripts/start_realtime_panel.py`：桌面/移动 Live2D URL 默认带 `role=spirit&config=models/manifest.json&autoload=1`。
   - 已新增 `scripts/validate_live2d_manifest.py`：校验 manifest、ready 角色的 model3 路径、Moc/Textures 引用，以及 expression/motion 映射；当前默认输出 warning（真实模型未放入）但不报错。
   - 已补 `backend/tests/unit/test_live2d_manifest.py`：覆盖默认 placeholder、ready=true 缺模型报错、ready=true 完整本地 model3 通过。
   - 放入真实资源步骤：把模型包放到 `frontend/models/spirit/`，确认 `spirit.model3.json` 路径和映射名称，然后将 `ready` 改为 `true` 并运行校验脚本。

28. **远端 worker 最小心跳链路 skeleton 已接入**
   - 已补 `backend/executors/remote_protocol.py`：新增 `RemoteNodeHeartbeat`，并让 `ExecutorRemoteNodeClient` 能构造最小 heartbeat 快照。
   - 已补 `backend/executors/node_registry.py`：支持 `register_heartbeat()`、`refresh_from_client()`、`mark_stale()`、`list_online_nodes()`，记录 `last_seen_at`、`status`、`capabilities`、`auth_token_id`。
   - 当前意义：中枢侧已经有“节点在线/过期/能力声明”的协议骨架，后续只差真实 worker 进程或网络 transport 把 heartbeat 发过来。
   - 当前限制：还没有真正的 `backend/remote/worker.py` 长连接/HTTP/WebSocket 心跳进程；也还没有 mTLS/真实 token 校验，当前只是注册表和协议层 skeleton。

29. **remote worker 最小可运行版已接入**
   - 已补 `backend/remote/worker.py`：基于标准库 `ThreadingHTTPServer` 提供 `GET /health`、`GET /heartbeat`、`POST /execute`。
   - 已补 `backend/executors/remote_protocol.py`：新增 `HttpRemoteNodeClient`，中枢侧可通过 HTTP 调远端 worker，并复用现有 `RemoteExecutionPayload / RemoteExecutionResponse / RemoteNodeHeartbeat`。
   - 当前默认 worker 会挂载 `LocalPCExecutor`，因此远端 PC 侧已经可以最小承接 desktop/local_pc/file/browser/clipboard/window 等本机执行请求。
   - 当前鉴权：`X-SpiritKin-Remote-Token` header；当前是最小 token 校验，不是 mTLS。
   - 当前定位：这是“可运行的最小 worker”，还不是完整分布式系统；后续还要补更强鉴权、审计、重连和生产级部署。

30. **NodeRegistry 主动轮询 + 远端节点 Dashboard 已接入**
   - 已补 `backend/executors/node_registry.py`：新增 `refresh_all_from_clients()`、`snapshot()`，可汇总 `online/stale/offline`，并在路由时跳过不可路由节点。
   - 已补 `backend/remote/poller.py`：`RemoteHeartbeatPoller` 后台定时刷新 heartbeat。
   - 已补 `backend/app/runtime.py`：`SpiritKinRuntime` 在传入含节点的 `node_registry` 时会自动启动 poller；`runtime.capabilities` 现携带 `remote_nodes` 快照。
   - 已补 `frontend/index.html`：Capability Dashboard 新增“远端节点”卡片，显示总数、在线数、stale、offline 与节点样例。
   - 当前限制：仍是单进程内最小轮询器；尚未做集中日志、重试退避、TLS/mTLS、持久注册表和 UI drill-down。

31. **真实远端 PC 节点配置入口已接入**
   - 已补 `backend/app/settings.py`：`resolve_remote_worker_nodes()` 支持 `SPIRITKIN_REMOTE_WORKER_URL`、`SPIRITKIN_REMOTE_WORKER_NODE_ID`、`SPIRITKIN_REMOTE_WORKER_TOKEN`、`SPIRITKIN_REMOTE_WORKER_ALIASES`，也支持 `config.remote.workers`。
   - 已补 `backend/app/runtime.py`：当未显式传入 `node_registry` 时，会从配置构建 `HttpRemoteNodeClient + RemoteNode + NodeRegistry`，并沿用现有 heartbeat poller 与 Dashboard 快照。
   - 已补 `scripts/start_realtime_panel.py`：新增 `--remote-worker-url`、`--remote-node-id`、`--remote-worker-token`、`--remote-worker-aliases`，方便一键启动中枢时注册远端 PC。
   - 远端 PC 启动：设置 `SPIRITKIN_REMOTE_NODE_ID`、`SPIRITKIN_REMOTE_TOKEN`、`SPIRITKIN_REMOTE_ALIASES` 后运行 `python -m backend.remote.worker`。
   - 中枢启动：`python scripts/start_realtime_panel.py --lan --remote-worker-url http://<远端IP>:8790 --remote-node-id office-pc --remote-worker-token <token>`。
   - 当前限制：仍需要用户自己准备网络可达路径；移动数据场景建议 Tailscale/ZeroTier/VPN/HTTPS Tunnel，不要裸露 worker HTTP 端口。

32. **远端 worker 实机 smoke test 脚本已接入**
   - 已补 `scripts/smoke_remote_worker.py`：通过 HTTP client 依次调用 `/heartbeat` 和 `/execute`，输出 JSON 报告并用退出码表达成功/失败。
   - 示例：`python scripts/smoke_remote_worker.py --url http://<远端IP>:8790 --node-id office-pc --token <token> --target desktop --operation status`。
   - 已补单测：`test_smoke_remote_worker_checks_heartbeat_and_execute` 使用本地临时 `ThreadingHTTPServer` 覆盖 heartbeat + execute。

33. **Tailscale 移动数据默认路线已接入启动器**
   - 默认推荐 Tailscale：PC、手机、远端 PC worker 都加入同一 Tailnet 后，移动数据访问不需要裸露公网 HTTP 控制口。
   - 启动中枢：`python scripts/start_realtime_panel.py --tailscale`；如自动检测失败，用 `--tailscale-ip 100.x.y.z`。
   - 远端 worker URL 建议使用远端 PC 的 Tailscale IP：`--remote-worker-url http://<远端PC的TailscaleIP>:8790`。
   - 已补 `scripts/smoke_mobile_access.py`：检查 Tailscale/移动数据通道里的 Frontend URL 和 Command Gateway `/health` 是否可达。
   - 当前仍需用户实际安装/登录 Tailscale；代码侧已完成 URL 检测/打印、token 生成、smoke 脚本、文档和单测。

34. **中国区 frp/HTTPS 隧道路线已接入启动器**
   - 因 Tailscale 中国区手机端可能下载不到，默认国内路线改为 frp/自有 HTTPS 隧道。
   - 启动中枢：`python scripts/start_realtime_panel.py --frp-domain-suffix example.com --frp-prefix spiritkin`。
   - 生成 frpc 模板：`python scripts/generate_frp_config.py --server-addr frp.example.com --token <token> --domain-suffix example.com --prefix spiritkin > frpc.toml`。
   - 生成的公网入口约定：`https://spiritkin.example.com/index.html`、`wss://spiritkin-events.example.com`、`https://spiritkin-command.example.com/command`。
   - 远端 worker 可选映射：加 `--remote-worker-port 8790`，得到 `https://spiritkin-worker.example.com`。

35. **远端节点状态详情与心跳日志已接入**
   - 已补 `backend/executors/node_registry.py`：snapshot 现在携带 `recent_events`，节点条目包含 `consecutive_heartbeat_failures` 与 `last_heartbeat_error`。
   - heartbeat 成功会清零连续失败并记录 `heartbeat_ok`；失败会标记 `offline`、累计连续失败并记录 `heartbeat_failed`；TTL 过期首次转 stale 时记录 `heartbeat_stale`。
   - 已补 `frontend/index.html`：远端节点卡片从“节点样例”升级为“节点详情 + 最近心跳事件”，方便 demo 时判断远端 worker 是正常、过期还是鉴权/网络失败。
   - 当前仍是内存日志，未持久化；后续工程化可接入审计日志、退避重试和详情页 drill-down。

36. **权限中心 / 审计最小版已接入**
   - 已新增 `backend/security/audit.py` 与 `backend/security/__init__.py`：提供 `AuditRecord`、`InMemoryAuditLog`、`JsonlAuditLog`、`build_audit_log()`。
   - 已补 `backend/app/settings.py`：`resolve_audit_log_path()`，默认路径 `state/audit_log.jsonl`，可用 `SPIRITKIN_AUDIT_LOG_PATH` 或 `runtime/security.audit_log_path` 配置。
   - 已补 `backend/app/runtime.py`：审计 mobile/web/desktop 输入、高风险确认请求/取消、执行结果；`runtime.capabilities.audit` 暴露 total/high/remote/mobile/failure 与 recent。
   - 已补 `backend/app/command_gateway.py`：未授权 command 请求会写入 `command_unauthorized` 审计事件。
   - 已补 `frontend/index.html`：Capability Dashboard 新增“权限 / 审计”卡片，显示高风险、远端、移动端、失败和最近审计记录。
   - 当前定位：这是最小安全可视化，不是完整权限中心；后续可补用户身份、策略引擎、操作审批、审计查询页和脱敏导出。

37. **桌面原子操作下一批已接入**
   - 已补 `backend/action/atomic_operations.py`：新增 `window.resize`、`window.move`、`browser.tab.list`、`browser.tab.activate`、`browser.tab.close`、`notification.send`、`file.write`、`file.save_as` 共 8 个操作定义。
   - 已补 `backend/devices/local_pc.py`：
     - `resize_window(title, width, height)` / `move_window(title, x, y)`：PowerShell + user32 `MoveWindow` API，当前仅 Windows。
     - `send_notification(title, text)`：Windows `System.Windows.Forms.NotifyIcon` 托盘通知。
     - `write_file_text(path, text)` / `save_text_as(path, text)`：Python 文件写入，支持自动创建父目录。
     - `list_browser_tabs()` / `activate_browser_tab()` / `close_browser_tab()`：Shell.Application COM 接口，当前仅支持 Edge/IE 标签页枚举与操作。
   - 已补 `backend/executors/local_pc_executor.py`：新增 8 个 operation 分支与友好消息摘要。
   - 已补 `backend/orchestrator/planner.py`：新增口语化映射与正则提取方法 `_extract_dimensions`、`_extract_numbers`、`_extract_notification`、`_extract_tab_identifier`，支持自然语言触发新操作。
   - 已补 `backend/tests/unit/test_architecture_layers.py`：Planner 路由测试覆盖 window resize/move、notification、browser tab list、file write。
   - 当前限制：browser tab 仅支持 Windows Edge/IE COM；window resize/move 仅 Windows；notification 仅 Windows；跨平台后续需补 macOS/Linux 实现。

38. **P1 Skill 升级机制最小版已接入**
   - 已新增 `backend/skills/workflow.py`：提供 `build_workflow_skill_specs()` 与 `workflow_skill_name()`，可把 Workflow Memory 的高频成功候选转换为 `SkillSpec`。
   - 已增强 `backend/orchestrator/agent_cluster.py`：`available_skills` 会同步 `workflow_memory.skill_candidates()`，将匹配到现有 ToolSpec 的候选 workflow 注册为动态候选 Skill。
   - 候选 Skill 当前包含单步 `SkillStepSpec`、tool allowlist、risk level、success_count/total_count/success_rate、example_params 和 `metadata.status=candidate`。
   - 已补 `backend/tests/unit/test_skill_layer.py` 与 `backend/tests/unit/test_workflow_memory.py`：覆盖候选转换与 AgentCluster 暴露动态候选 Skill。
   - 当前限制：这还是“候选 Skill”，不是自动升权为正式 Skill；后续还需审核/验证/降权/版本化/持久化 Skill Registry。

39. **P1 Harness / Replay / Eval 最小版已接入**
   - 已新增 `backend/eval/replay.py` 与 `backend/eval/__init__.py`：提供 `ReplayRecord`、`ReplayReport`、`build_replay_report()`。
   - 当前 replay 是 dry-run：从 Workflow Memory snapshot 重建 `ExecutionRequest`，不执行真实动作；按 ToolSpec 标记 tool_name、risk_level、high_risk_count 和可回放性。
   - 已新增 `scripts/replay_workflow_memory.py`：读取默认 `state/workflow_memory.sqlite3` 或 `--path` 指定 memory，输出 JSON replay report；支持 `--include-archived` 与 `--require-known-tool`。
   - 已新增 `backend/tests/unit/test_replay_harness.py`：覆盖请求重建、未知工具/高风险标记、CLI 输出 JSON 报告。
   - 当前限制：尚未接入失败样本库、自动 eval case 生成、候选 Skill dry-run 验证阈值和前端报告页。

### 项目长期 Backlog / 进度口径

当前任务清单完成只代表最近一批工程改动完成，不代表项目完成。粗略进度仍按三层看：

- **可演示闭环**：约 35%～45%。还要继续实机稳定性、真实 Live2D 资源、更多桌面原子操作、移动数据通道、真实远端 worker。
- **可演示闭环**：约 40%～50%。已具备最小远端 worker + heartbeat 轮询 + Dashboard 展示，但还缺真实公网链路与更完整的远端设备矩阵。
- **工程化个人助手**：代码侧约完成。已补 Workflow Memory、候选 Skill、Skill 审核/升降权/持久化入口、Replay/Eval 二期核心、RAG 增量索引 + reranker、权限中心、Android Companion 最小闭环和 iOS Shortcuts / App Intents Connector；剩余主要是真机 App/外部设备验收。
- **LPM 风格数字生命**：代码侧基础闭环已完成。已补 Avatar Shell、口型事件、可打断 TTS 生命周期、Streaming ASR 事件/Wakeword Gate、LPM 状态事件、长期人格状态机、多智能体自我改进、Native 壳配置层和持续感知状态层；剩余为真实 Live2D 模型、真实语音/设备长时间体验与 Native 壳实机验收。

P0 后续任务：
- 移动数据真实连通：中国区默认选 frp/HTTPS 隧道，下一步用实际域名和云服务器实测手机 4G/5G 访问面板和 Command API。
- Capability Dashboard 增强：审计详情页、远端节点详情页 drill-down、库存明细、workflow 趋势图。
- Live2D 资源实装：放入真实 `model3.json` 角色包、说话动作、移动端压缩模型、expression/motion 真映射。

下一批更有价值的 P0：
- 移动数据真实连通（Tailscale / Tunnel 实机验证）
- 用一台真实远端 PC 跑 `backend.remote.worker` 执行 `scripts/smoke_remote_worker.py` 实机验证
- 远端节点状态详情页/日志与失败重试

当前更适合继续自动推进的下一步：
- 移动数据真实连通（中国区优先 frp/HTTPS 隧道；需要用户提供或准备域名/云服务器后实测）
- 或执行远端 worker 实机 smoke test 并根据结果修连接问题
- 或 Live2D 真资源接入（如果你提供模型包）

> 注：任务列表现已从“单轮小任务”扩展为全项目路线图，可直接查看 P0 / P1 / P2 backlog 来追踪整个项目进程。

P1 代码侧已完成（Engineering-ready）：
- **Android Companion 最小版**：已补 `device.status`、`software.list_installed`、`app.launch`、heartbeat 状态上报、installed_apps 上报、command queue / pending_commands 拉取；待真机 Companion App 对接验收。
- **iOS Shortcuts / App Intents Connector**：已补白名单动作、Webhook / URL Scheme schema、App Intent payload 映射、unsupported action 拒绝；待真实 iPhone Shortcuts / App Intents 对接验收。
- **Skill 正式升级链路**：已补候选审核、升权/降权/归档/拒绝状态、版本化、持久化 Skill Registry 入口和候选 Skill replay 阈值验证；待人工审核 UI 细化。
- **Harness / Replay / Eval 二期**：已补失败样本库、候选 Skill replay 阈值验证、审计日志联动、报告导出参数、Dashboard 报告命令入口和 `frontend/replay_report.html` 可视化报告页。
- **RAG 增量索引 + Reranker**：已补增量 ingest、citation、rerank、过期检测、Obsidian / Markdown vault connector，并让 embedding retriever 默认接入 reranker 且输出 rerank metadata；待持久化向量库/混合检索作为后续增强。
- **权限中心工程化**：已补 PermissionCenter、能力授权 glob、策略拦截、速率限制、审计 query/export_jsonl 脱敏导出、CommandGateway 公网/token 安全上下文，并新增 `frontend/audit_report.html` 审计详情查看页。

P2 代码侧已完成（LPM-Complete 基础闭环）：
- **Streaming ASR + 专用 Wakeword**：已补 `asr.speech_started` / `asr.partial` / `asr.final` 事件和 `StreamingWakewordGate`。
- **可打断 TTS + Live2D 口型同步**：已补 `speech.started` / `speech.phoneme` / `speech.interrupted` / `speech.ended`，Avatar Shell 与 Live2D 均消费 mouth-shape。
- **长期记忆与人格状态机**：已补 `companion_mode()`、`lpm_state()`、presence/memory/personality 事件。
- **多智能体调度与自我改进闭环**：已补 `SelfImprovementLoop`、trajectory/failure/performance 聚合、eval cases 和 improvement actions。
- **Native Live2D 壳与多端形象层**：已补 `AvatarShellProfile` 与桌面/Android/iOS WebView manifest 配置层。
- **持续感知与长期陪伴形态**：已补 context observation、active_contexts、recent_observations、proactive_suggestion。

P2 当前推进记录：
- **Avatar Shell 前端统一**：已把 `frontend/spirit_avatar.html` 从 DS 生成的浏览器 ASR/TTS 玩具页改造成 P2 角色前台；页面只消费 runtime WebSocket 事件，接 Command API，支持确认流、移动端 URL、`performance.state` / `avatar.state` / `speech.phoneme` 预留，并在 `index.html` 与 `scripts/start_realtime_panel.py` 输出入口。
- **口型事件链路**：Runtime 已在 assistant reply 后生成 `speech.phoneme` 事件，Avatar Shell 按 `timestamp_ms` 播放 mouth-shape 时间线，`live2d.html` 尝试把 mouth shape 映射到 `ParamMouthOpenY`，为后续真实 TTS/Live2D 口型同步打基础。
- **LPM 状态事件**：Runtime 已在每轮回复后记录 MemoryOrchestrator interaction，并向前端发 `presence.updated`、`memory.updated`、`personality.updated`；capabilities 暴露 `lpm_state_events` 与 memory snapshot，Avatar Shell 可显示 persona / memory / presence。
- **可打断 TTS 生命周期**：`SpeechController` 已支持可选 `event_sink`，播报期间发 `speech.started`、`speech.phoneme`、`speech.interrupted`、`speech.ended`；`RealtimeDuplexSession` 默认把 speech 事件接到 runtime event bridge，Avatar Shell / Live2D 会根据生命周期事件更新 speaking / interrupted / mouth idle 状态。
- **Streaming ASR 事件与 Wakeword Gate**：`StreamingSession` 已支持 `event_sink`，输出 `asr.speech_started`、`asr.partial`、`asr.final`；新增 `StreamingWakewordGate`，支持 hotword 激活、wake window、`cleaned_text` 和 `wake_required` 门控，`create_streaming_listener()` 可启用独立 wakeword gate。
- **长期记忆与人格状态机**：`PersonalityState` 已新增 `companion_mode()` 与 `lpm_state()`，输出 mode / mood / energy / familiarity / reliability / session_minutes；`MemoryOrchestrator.snapshot()` 暴露 `lpm_state`，供 Avatar Shell / 前端展示长期陪伴状态。
- **多智能体自我改进闭环**：已新增 `SelfImprovementLoop` / `SelfImprovementReport` / `ImprovementAction`，聚合 Agent performance、trajectory bottleneck、failure samples，生成 eval cases 与改进动作；AgentCluster 默认记录 performance 与 failure trajectory，并暴露 `build_self_improvement_report()`。
- **Native Live2D 壳与多端形象配置层**：已新增 `AvatarShellProfile`、`build_avatar_shell_profile()`、`build_multi_end_avatar_manifest()`，统一桌面 WebView、Android WebView、iOS WebView 的 Avatar/Live2D URL、窗口参数和能力声明，作为未来 Native 壳 / 原生 SDK 接入配置基础。
- **持续感知与长期陪伴**：`PresenceManager` 已支持 `record_context()`，可记录 app / screen / task / calendar / project 等上下文，snapshot 暴露 `active_contexts`、`recent_observations`、`proactive_suggestion`，为长期在线与主动陪伴提供状态层。

### 最建议的下一步

1. **先实机跑通 `--route-agent`**
   - 命令：`python scripts/smoke_asr.py --route-agent`
   - 测试自然话术，不要只测固定口令。
   - 样例：
     - “机械B现在怎么装它”
     - “把夹爪打开一下”
     - “帮我给张三发个飞书，就说会议挪到三点”
     - “打开浏览器”

2. **根据实机失败样本补 IntentResolver 测试**
   - 优先补到：`backend/tests/unit/test_agent_cluster.py`。
   - 如果是 Runtime 传参或语音链路问题，补到：`backend/tests/unit/test_runtime.py`。

3. **做前端最小状态面板**
   - 先把事件展示出来，再继续做复杂 UI。
   - 优先展示“理解结果 / corrected_text / 等待确认 / 执行结果”。

4. **继续实机验证前端双工、一键启动与手机指令**
   - 本机推荐命令：`python scripts/start_realtime_panel.py` 一键启动桥、runtime、前端、命令网关。
   - 手机同 Wi-Fi 推荐命令：`python scripts/start_realtime_panel.py --lan`，按终端打印的 URL / Token 在 iOS 或 Android 浏览器访问。
   - 如果要分开看日志：终端 1 运行 `python scripts/start_realtime_panel.py --no-runtime`，终端 2 运行 `python -m backend.main`。
   - 手机可测试：`扫描本机软件`、`打开火爆浏览器`、`关闭火爆浏览器`、`确认执行`。

5. **补原子操作注册与 Skill 骨架**
   - 先把 `software.list_installed`、`hardware.list_devices` 正式加入工具注册表。
   - 再新增 `SkillSpec` / `SkillRegistry` / `SkillRunner` 最小骨架和单测。

6. **启动 Memory Orchestrator / Workflow-memory 设计文档**
   - 先写结构和接口，不急着上复杂算法。
   - 明确 workflow 如何记录、召回、升权、降权、归档和升级为 Skill。

### 新 agent 建议先看这些文件
- `backend/orchestrator/intent_resolver.py`
- `backend/orchestrator/agent_cluster.py`
- `backend/orchestrator/planner.py`
- `backend/app/runtime.py`
- `scripts/smoke_asr.py`
- `backend/tools/base.py`
- `backend/executors/base.py`
- `backend/services/conversation_engine.py`
- `backend/tests/unit/test_agent_cluster.py`
- `backend/tests/unit/test_runtime.py`

### 最近有效的验证命令
- `python -m py_compile backend/orchestrator/intent_resolver.py backend/orchestrator/agent_cluster.py backend/app/runtime.py scripts/smoke_asr.py`
- `python -m unittest backend.tests.unit.test_agent_cluster backend.tests.unit.test_runtime -v`
- `python -m unittest backend.tests.unit.test_agent_cluster backend.tests.unit.test_runtime backend.tests.unit.test_start_realtime_panel -v`
- `python -m py_compile backend/orchestrator/agent_cluster.py backend/app/runtime.py backend/perception/audio/hotword.py scripts/start_realtime_panel.py`
- `python -m unittest backend.tests.unit.test_agent_cluster backend.tests.unit.test_runtime backend.tests.unit.test_start_realtime_panel backend.tests.unit.test_command_gateway -v`
- `python -m py_compile backend/orchestrator/agent_cluster.py backend/app/runtime.py backend/app/command_gateway.py backend/app/realtime_bridge.py scripts/start_realtime_panel.py`
- `python -m unittest backend.tests.unit.test_workflow_memory backend.tests.unit.test_runtime backend.tests.unit.test_agent_cluster -v`
- `python -m py_compile backend/memory/workflow.py backend/orchestrator/agent_cluster.py backend/app/runtime.py backend/app/settings.py`
- `python -m unittest backend.tests.unit.test_start_realtime_panel -v`
- `python -m py_compile scripts/start_realtime_panel.py backend/app/runtime.py backend/app/settings.py`
- `python -m unittest backend.tests.unit.test_runtime backend.tests.unit.test_start_realtime_panel -v`
- `python -m py_compile backend/app/runtime.py backend/app/settings.py scripts/start_realtime_panel.py`
- `python -m unittest backend.tests.unit.test_start_realtime_panel -v`
- `python -m py_compile scripts/start_realtime_panel.py`
- `python -m unittest backend.tests.unit.test_local_pc_device backend.tests.unit.test_agent_cluster -v`
- `python -m py_compile backend/action/atomic_operations.py backend/devices/base.py backend/devices/local_pc.py backend/executors/local_pc_executor.py backend/orchestrator/planner.py`
- `python -m py_compile backend/memory/workflow.py backend/memory/__init__.py backend/app/settings.py backend/app/runtime.py`
- `python -m unittest backend.tests.unit.test_workflow_memory backend.tests.unit.test_runtime backend.tests.unit.test_settings -v`
- `python -m py_compile backend/app/runtime.py`
- `python -m unittest backend.tests.unit.test_runtime -v`
- `python -m py_compile backend/memory/workflow.py backend/memory/__init__.py backend/app/settings.py backend/orchestrator/agent_cluster.py backend/app/runtime.py backend/tests/unit/test_workflow_memory.py`
- `python -m unittest backend.tests.unit.test_settings backend.tests.unit.test_runtime backend.tests.unit.test_agent_cluster backend.tests.unit.test_architecture_layers backend.tests.unit.test_workflow_memory -v`
- `python -m py_compile backend/skills/workflow.py backend/skills/__init__.py backend/orchestrator/agent_cluster.py backend/tests/unit/test_skill_layer.py backend/tests/unit/test_workflow_memory.py`
- `python -m unittest backend.tests.unit.test_skill_layer backend.tests.unit.test_workflow_memory backend.tests.unit.test_runtime -v`
- `python -m py_compile backend/eval/__init__.py backend/eval/replay.py scripts/replay_workflow_memory.py backend/tests/unit/test_replay_harness.py`
- `python -m unittest backend.tests.unit.test_replay_harness -v`
- `IDE diagnostics: frontend/live2d.html frontend/models/README.md frontend/models/manifest.example.json docs/live2d_mobile_strategy.md`
- `python -m py_compile backend/remote/worker.py backend/remote/__init__.py backend/executors/remote_protocol.py backend/executors/node_registry.py backend/executors/__init__.py`
- `python -m unittest backend.tests.unit.test_remote_worker backend.tests.unit.test_tooling_and_remote -v`
- `python -m unittest backend.tests.unit.test_local_pc_device backend.tests.unit.test_agent_cluster -v`
- `python -m py_compile backend/action/atomic_operations.py backend/devices/base.py backend/devices/local_pc.py backend/executors/local_pc_executor.py backend/orchestrator/planner.py`
- 如果当前环境装好了 ASR 依赖，再跑：`python scripts/smoke_asr.py --route-agent`
- 一键启动前端双工：`python scripts/start_realtime_panel.py`
- 一键启动并允许 iOS/Android 同 Wi-Fi 访问：`python scripts/start_realtime_panel.py --lan`
- 手机走移动数据时：先建立 VPN/HTTPS 隧道，再用 `python scripts/start_realtime_panel.py --lan --public-frontend-url https://... --public-events-ws-url wss://... --public-command-url https://.../command` 打印公网入口
- 推荐 Tailscale 移动数据入口：`python scripts/start_realtime_panel.py --tailscale`
- Tailscale/移动数据通道 smoke：`python scripts/smoke_mobile_access.py --frontend-url http://100.x.y.z:8787/index.html --command-url http://100.x.y.z:8788/command --token <token>`
- 手机/网页命令网关：`http://<本机IP>:8788/command`，请求头 `X-SpiritKin-Token: <token>`
- 前端事件桥单独启动：`python -m backend.app.realtime_bridge`
- Runtime 主循环单独启动：`python -m backend.main`
- 前端默认连接地址：`ws://127.0.0.1:8765`

### 当前状态一句话总结
当前 P0 已基本具备 demo 主链路；P1 代码侧已补齐 Engineering-ready 关键闭环；P2 代码侧已补齐 LPM-Complete 基础闭环。剩余主要进入 P0/P1/P2 统一实机验收：真实 Live2D 模型、真实手机/移动网络、远端 PC、Android/iOS 真机和长时间语音/陪伴体验验证。

### 新交接中心
- `docs/agent_cluster_optimal_plan.md`：统一安全内核 + 混搭模型/Agent 框架的性能最优方案。
- `docs/landing_and_test_handoff.md`：落地状态、真实可用边界、模型目录刷新、云训练包和实机验收清单。
