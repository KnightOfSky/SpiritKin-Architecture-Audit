# SpiritKinAI 核心系统改进方案（完整版）

> **作者**：Claude（方案+验收+兜底），**实施方**：GPT/OPUS
> **状态**：实现闭合；自动化、本机 UI 与指定 Edge 分批验收通过，真机/真实账号/长时媒体验收待执行（2026-07-18）
> **背景**：完整扫描 SpiritKinAI（98,642 行 Python + 809 个 C# 文件）与 Cyrene-Agent（59,029 行 TypeScript），对比架构优劣，针对 20 项未完成功能和 8 项架构痛点制定系统级改进方案

## 0.4 2026-07-18 实施复核

这份方案不是“全部未实施”清单。以下状态以当前 `D:\SpiritKinAI` 代码、定向测试和 WPF 构建为准；Cyrene-Agent 只作为外部只读参考，临时 clone 不纳入项目交付并已清理。

| 批次 | 当前状态 | 证据 / 尚未闭合项 |
|---|---|---|
| M1 | 实现完成，本机验收通过 | 长期记忆召回、DMAE 激活度、中文匹配和降级已接主链；知识库、记忆、reranker、Avatar 共用进程级 EmbeddingService。隔离七日记忆在重启后由 llama.cpp 768 维向量真实召回并注入 Soul。 |
| M2 | 实现完成，本机验收通过 | 路由、执行、Soul 回复、模型调用、受管 Agent 名册和 wiring 已拆分；`ClusterDeps`/`AgentClusterWiring` 保留旧调用兼容，`AgentCluster` 已降至 548 行薄门面。 |
| M3 | 实现完成，本机验收通过 | Tool/Skill/MCP 统一授权、四级风险、双重安全校验、manifest 冲突报告和 WPF 授权管理页已接入；桌面禁用/真实拒绝/恢复闭环通过。 |
| M4 | 实现完成，本机验收通过 | stderr failure_context、失败分类、指数退避、fatal 零重试、CLI preflight 和轮转 self-heal 日志已接主执行链；缺包修复现会消费受治理 `python.install_package`，确认后持久化续跑原请求，隔离 venv 真实安装/重试通过。 |
| M5 | 实现完成，Edge 验收通过 | Avatar 语义 reaction、关键词降级、full/reduced/static 和 trace 已有；补齐 4 秒呼吸、7 秒重心摇摆、2-6 秒随机眨眼、300ms 表情缓动、明暗主题灯光和宽屏相机安全边距。 |
| M6 | 实现完成，本机验收通过 | orchestration 对 `backend.app` 的反向依赖已改为端口/注入。 |
| M7 | 实现完成，本机验收通过 | stdio、Streamable HTTP、legacy SSE 的发现、调用、重试、会话头、网关安全、三次健康降级/恢复和审计已接入。 |
| M8 | 实现完成，本机验收通过 | 声明式 manifest 扫描、Authz、工作流节点生成、非法隔离、内置工具保护和多目录优先级冲突报告已统一；`entry.script + entry.argv` 可在不改注册代码时绑定目录内 Python 脚本并由受治理 Worker 执行。 |
| M9 | 实现完成，本机验收通过 | Local/Android DeviceBackend 完整契约、Brave/DuckDuckGo SearchProvider、faster-whisper 增量 ASR/VAD 和可操作错误已实现。 |
| M10 | 实现完成，本机验收通过 | 直接/控制面 Worker、双端安全、PyInstaller one-file EXE、签名安装包/outbox/重连、增量 Audit、PDD 扩展/productData/电商工作流、FFmpeg 与运维手册已交付；EXE 独立进程断联恢复 smoke 已通过，跨机物理断网、真实账号与真机作为验收环境项执行。 |
| M11 | 实现完成，本机验收通过 | Feishu dry-run/签名重试、Reviewer 配置、persona 工具、LangGraph 最小适配、CrewAI 明确降级及“命令生成→人工训练”说明已交付。 |
| M12 | 实现完成，本机验收通过 | 主动信号、关系边界、安静时段、冷却/上限、建议反馈及重新进入普通安全链已覆盖。 |
| M13 | 实现完成，本机验收通过 | 关系状态、显式边界提取/去重/撤回、原子持久化、Soul 注入和多端事件已覆盖；跨重启去重、解除和清零 smoke 通过。 |
| M14 | 实现完成，本机验收通过 | APScheduler SQLite、时区/DST、misfire/并发/幂等/安全门及 WPF date/interval/cron 管理已覆盖；真实接口全动作、UTC 更新及命令网关换 PID 后任务恢复通过。 |
| M15 | 实现完成，Edge 验收通过 | 开场气泡状态机、优先级、结构化事件、低动效、去重、反馈和只导航不执行已覆盖；390x844 recovery 气泡视觉与动作通过。 |
| M16 | 实现完成，本机验收通过 | WPF 通话窗口、权威状态机、VAD/字幕、TTS 打断、权限错误和结束清理已覆盖。 |
| M17 | 实现完成，本机验收通过 | NAudio 播放服务、队列/进度/循环/音量、底栏、AudioFocus、路径边界与远程 URL 授权已覆盖；桌面底栏与队列弹层当前视觉通过。 |
| M18 | 可选实现完成，Edge 验收通过 | 受控本地 Demo 适配器、默认空白名单、焦点/Kill Switch/速率/未知画面暂停和审计回放已覆盖；Edge 回收/暂停/继续/停止键与移动视口通过，不面向第三方线上游戏。 |

本轮新增闭环：WPF 记忆证据处置面板补充来源/归属显示；微信 iLink 文本双向通道补齐真实 `getupdates` 长轮询与 `sendmessage` 回传（默认关闭）；`email.send` 纳入统一工具注册、显式确认、工作区附件边界和测试。

2026-07-17 本地模型与 UI 补齐：Windows CUDA 版 llama.cpp b10058 已安装到 `runtime/llama.cpp/`，Qwen 35B-A3B 对话/多模态服务运行于 `:8080`，Nomic Embedding 独立服务运行于 `:8081`；配置、默认 Provider、Evolution 抽取链和桌面自动启停均已切换，LM Studio 仅保留兼容入口。WPF 新增“授权与调度”管理页，覆盖 M3 工具授权和 M14 定时任务的核心操作。

2026-07-17 自动化分批验收：M1-M6 为 110 passed + 3 subtests；M7-M11 为 118 passed + 2 subtests，另有独立进程 Remote Worker smoke `ok:true` 与 PDD normalizer 3/3；M12-M18 为 62/62。补齐 SearchProvider、Avatar idle、manifest 节点目录、网关自重启顺序和 llama.cpp 协作轨迹分类用例，并修正一次性调度任务先发事件后提交状态的竞态。2026-07-18 在补齐 M5 精确动效参数、完整模块语法回归、新增持久化 smoke 和 Android 48dp 触控目标回归后，当前全量 Python 回归为 1604 passed + 20 subtests；最终根目录 `run_verification.py` 为 212 passed、Ruff pass、WPF build 0 warning/0 error、桌面 271/271，并已写入 `docs/test-ledger.md`。18 份独立记录已落在 `docs/manual_tests/M1_manual_test.md` 至 `M18_manual_test.md`，汇总与外部环境验收项见 `docs/manual_tests/2026-07-17_core_plan_batch_acceptance.md`。

2026-07-17 真实恢复补测：`scripts/smoke_control_plane_worker_recovery.py` 以独立控制面/Worker 进程验证执行中断联，结果先保留为 1 个 outbox 文件，控制面重启后在下一次 heartbeat 前回传并清空，任务终态 `completed`。命令网关服务面板自重启改为在完整快照完成后才启动退出倒计时；真实请求收到 `restart_scheduled` 和 `operations`，PID `18896 -> 27124` 后定时任务仍为 `active`，随后已清理为 `cancelled`。

2026-07-17 完成性审计补漏：M4 原先只解析而未执行 `repair_tool`，现仅在真实缺包证据下接受 `python.install_package`，继续经过 Authz/Safety/高风险确认，并在确认后恢复原请求；隔离 venv 从 `ModuleNotFoundError` 到安装 `colorama==0.4.6` 再重跑输出 `0.4.6`，证据位于 `tmp/execution-repair-20260717/`。M8 原先只能生成请求，现通过 `entry.script + entry.argv` 在工具目录边界内实际执行 manifest 脚本，证据位于 `tmp/manifest-echo-20260717/`。M10 新增可复现 PyInstaller 6.21.0 one-file 构建，`dist/spiritkin-control-plane-worker.exe` 已通过真实断联/outbox 恢复，控制面在线下载包也已改为包含 EXE。

本轮继续拆分：新增 `AgentRoster`，统一受管 Agent profile 归一化、启停/优先级、mention 路由记录和 adapter 构建；`AgentCluster` 仅消费名册组装结果。相关名册/门面/路由测试 12 项通过，M1-M18 定向功能测试 166 项通过。

2026-07-18 完成性复核：新增 `scripts/smoke_memory_relationship.py`，用隔离存储完成七日前偏好写入、进程级实例重建、llama.cpp 768 维真实召回、DMAE 激活与 Soul 注入；同一脚本完成关系边界跨重启去重、注入、解除和再次重启清零。WPF 当前构建完成 M3 工具禁用/恢复的真实调用闭环，以及 M14 date 任务创建、暂停、恢复、立即运行、取消和 UTC 时区更新。UI 证据与限制记录在 `docs/manual_tests/2026-07-18_governance_ui_audit.md`。

2026-07-18 Edge 与移动端复核：用户指定 Microsoft Edge 后，Avatar 1440x900、1024x768、390x844 多视口、事件通道、开场气泡和 5 秒 FPS 通过；验收中发现并修复宽屏 Canvas 顶边裁切，当前 FPS 为 43.03/44.91。受控游戏 Demo 完成回收、暂停/继续、`Shift+Esc` 0ms 停止和移动视口。Android `.7` 候选 APK 由最新源码重建并通过 v1/v2/v3 签名，普通操作按钮提升到 48dp；当前分发批准仍为 `.4`，`.7` 待真机复核和人工批准。当前无 ADB 设备，真机截图、无障碍和 MediaProjection 仍不作静态推断。完整浏览器证据见 `docs/manual_tests/2026-07-18_edge_browser_acceptance.md`。

2026-07-18 移动端角色边界修正：Android 明确为 Bridge/执行端，不承载 3D Avatar、声音身份或音频训练 UI；`/ios/terminal` 与原生 SwiftUI Control destination 增加 iOS-only Avatar 舞台，并复用 v4 日/夜语义色。指定说话音色与 AI 翻唱拆为 TTS voice profile 和 SVC 两条流水线，架构、权限、作业状态及分批交付见 `docs/voice_identity_and_ai_cover_plan.md`。

电商链路纠偏：手机 PDD App 回传的网页链接现在由项目内 `browser-extension/pdd-product-extractor` 默认自动领取；扩展迁移 AutoProcess rawData/SKU/图片字段语义，使用独立 `browser_extension` 配对角色，并把结果同时写为受审计 JSON Artifact 和对应电商任务 Artifact。旧微信小程序短链、probe 和 OCR productData adapter 已删除。

---

## 目录

- [0. 执行摘要](#0-执行摘要)
- [1. 双项目架构对比](#1-双项目架构对比)
- [2. 未完成功能清单](#2-未完成功能清单)
- [3. 改进批次总览](#3-改进批次总览)
- [4. 详细实施方案](#4-详细实施方案)
  - [M1: 记忆系统语义化](#m1-记忆系统语义化)
  - [M2: 上帝类拆分](#m2-上帝类拆分)
  - [M3: 白名单动态化](#m3-白名单动态化)
  - [M4: 执行回路自愈](#m4-执行回路自愈)
  - [M5: Avatar 陪伴感增强](#m5-avatar-陪伴感增强)
  - [M6: 循环依赖反转](#m6-循环依赖反转)
  - [M7: MCP 生态增强](#m7-mcp-生态增强)
  - [M8: 技能扩展自动化](#m8-技能扩展自动化)
  - [M9: 基础协议补全](#m9-基础协议补全)
  - [M10: 运维自动化补全](#m10-运维自动化补全)
  - [M11: 配置激活与适配器](#m11-配置激活与适配器)
  - [M12: 主动交互系统](#m12-主动交互系统)
  - [M13: 关系系统](#m13-关系系统)
  - [M14: 定时调度器](#m14-定时调度器)
  - [M15: 开场气泡系统](#m15-开场气泡系统)
  - [M16: 语音通话窗口](#m16-语音通话窗口)
  - [M17: 音乐播放器](#m17-音乐播放器)
  - [M18: 游戏自动化](#m18-游戏自动化)
- [5. 验收与验证](#5-验收与验证)
- [6. 禁改区](#6-禁改区)
- [7. 交付时间线](#7-交付时间线)

---

## 0. 执行摘要

### 0.1 核心发现

**SpiritKinAI 架构健康度**（基于完整代码扫描）：
- **模块化** 6/10：`agent_cluster.py` 2264 行上帝类，26 个构造参数
- **可扩展性** 8/10：工具/技能/Agent 注册表完善，MCP 协议支持动态扩展
- **可测试性** 7/10：pytest 136+ 用例，但集成测试不足
- **容错性** 5/10：异常处理过于宽泛（broad `except Exception`），缺少降级策略
- **可观测性** 8/10：39 个 JSONL 轨迹日志、审计日志、事件总线完善
- **安全性** 7/10：Kill Switch + 白名单 + 审核门控，但授权系统静态（6 处硬编码）

**Cyrene-Agent 核心优势**（值得学习）：
1. **两阶段 FC 循环**：工具阶段与灵魂阶段分离，避免人格回复时看到工具定义
2. **DMAE 动态记忆**：基于使用频率和内在价值的记忆衰减公式（用户奖励 Bu=20、模型维护 Bm=8）
3. **向量语义匹配**：58 个贴纸用 BGE-M3 (1024维) 预计算 embedding，余弦相似度实时匹配
4. **工具注册表统一**：所有工具（内置/MCP/Skill）统一 `ToolDefinition` 接口，风险分级（safe/network/shell/fs-write）
5. **轻量化依赖**：核心依赖 < 30 个，ONNX 量化模型（BGE-M3 仅 570MB）

### 0.2 改进目标

**18 个批次，覆盖架构、协议、运维与主动陪伴缺口**：
- **架构收敛**（M2/M3/M6/M8）：拆上帝类、统一白名单、反转依赖、自动化扩展
- **语义增强**（M1/M5）：真实 Embedding、长期记忆召回、3D Avatar 生命感
- **容错增强**（M4/M9）：stderr 回喂、自动重试、协议补全
- **生态扩展**（M7/M10/M11）：MCP sse/http、远程 Worker、配置激活
- **主动陪伴**（M12-M18）：边界感知、主动交互、定时任务、开场气泡、语音、音乐与可选游戏自动化

### 0.3 交付价值

- **用户体验**：Avatar 生命感、记忆召回准确度提升
- **开发效率**：新工具零代码接入、工作流节点自动生成
- **系统稳定性**：自动重试、降级策略、异常不再吞噬
- **商业价值**：远程设备控制、电商 RPA 端到端验证

---

## 1. 双项目架构对比

### 1.1 技术栈对比

| 维度 | SpiritKinAI | Cyrene-Agent |
|---|---|---|
| **后端** | Python 3.12 | Electron 43 主进程（TypeScript 5） |
| **前端** | 原生 HTML/JS（无构建） | Vite 5 + TypeScript |
| **Avatar** | Three.js + VRM 3D 模型 | Pixi.js 7 + Live2D |
| **Embedding** | LM Studio (nomic-embed) | ONNX (BGE-M3 / MiniLM) |
| **向量检索** | ChromaDB（可选） | 内存索引 + BM25 混合 |
| **代码规模** | 98,642 行 Python | 59,029 行 TypeScript |
| **依赖数** | ~120+ (requirements.txt) | < 30 核心依赖 |
| **测试覆盖** | pytest 136+ 用例 | Vitest 部分模块 |

### 1.2 架构亮点对比

#### Cyrene-Agent 优势（SpiritKinAI 可学习）

**1. 两阶段 FC 循环（职责分离）**
```
Phase 1: Tool Phase
  ├─ System Prompt: tool_system + 工具目录
  ├─ 携带完整 tools schema
  └─ LLM 决定是否调用工具

Phase 2: Soul Phase
  ├─ System Prompt: soul_system (人设 + 记忆 + 关系)
  ├─ 不携带 tools schema (阻止工具调用)
  └─ 工具结果摘要注入对话历史
```
**启示**：SpiritKinAI 的 AgentCluster 混杂了路由、执行、确认、回复组装，应分离为独立模块。

**2. DMAE 动态记忆激活引擎**
```python
用户命中奖励： Ru = Bu × (1 + γ·ln(1+U_old))  # Bu=20, γ=0.5
模型维护奖励： Rm = Bm × e^(-λ·U_old)         # Bm=8, λ=0.3
遗忘衰减：     D = (α·U² + β·M²) / √I        # α=1.5, β=0.3

状态机：
  Activation <= 0       → Archived
  0 < Activation < 30   → Dormant
  Activation >= 30      → Active (注入 Prompt)
```
**启示**：SpiritKinAI 的长期记忆只有存储无召回策略，应引入激活度计算。

**3. 向量语义匹配（58 个贴纸 embedding 预计算）**
```typescript
// 余弦相似度核心算法
function cosineSimilarity(a: number[], b: number[]): number {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}
```
**启示**：SpiritKinAI 的情绪标签是硬编码字符串（`<emotion:happy>`），应改为向量检索。

**4. 工具注册表统一接口**
```typescript
interface ToolDefinition {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  risk?: ToolRiskLevel;  // safe | network | shell | fs-write
  inputSchema: {...};
  execute: (args, ctx?) => Promise<string>;
}
```
**启示**：SpiritKinAI 的工具白名单硬编码在 6 处，应统一为动态授权系统。

#### SpiritKinAI 优势（Cyrene-Agent 不具备）

**1. 工作流引擎**（Cyrene-Agent 无此能力）
- DAG 节点执行：tool_call、skill_call、agent_task、review_gate、branch、subgraph、foreach
- 依赖解析、模板变量替换、审核门控
- 轨迹日志、回放支持

**2. 多设备执行器架构**
- 11 个执行器：LocalPC、Remote、Android、OpenClaw、Feishu、Browser、Python、Git、FFmpeg、Service RAG
- 统一 ExecutionRequest → ExecutionResult 协议

**3. Kill Switch 安全控制**
- soft_stop / hard_stop 两级紧急停止
- 执行安全评估：`evaluate_execution_safety()`
- 网关请求拦截：`evaluate_gateway_request_safety()`

**4. 多端桌面应用**
- WPF 桌面端 809 个 C# 文件
- 管理面板：任务、项目、Agent、工作流、模型、知识库
- 实时协作：Agent 路由总线、消息 ack 机制

### 1.3 架构痛点对比

| 痛点类型 | SpiritKinAI | Cyrene-Agent |
|---|---|---|
| **上帝类** | `agent_cluster.py` 2264 行，26 参数 | 无（两阶段分离） |
| **白名单硬编码** | 6 处硬编码 | 统一 ToolRegistry |
| **异常吞噬** | broad `except Exception` 多处 | 无明显问题 |
| **循环依赖** | orchestrator ↔ app | 无 |
| **记忆召回** | 有存储无召回 | DMAE 自动激活 |
| **Avatar 生命感** | idle 完全静止 | idle 呼吸/摇摆 |
| **工具扩展** | 新增要改 3 处 | 调 register() 即可 |

---

## 2. 未完成功能清单

### P0 关键缺口（阻塞核心功能）

| # | 功能 | 位置 | 状态 | 影响 |
|---|---|---|---|---|
| 1 | **工作流执行语义** | `workflow_graph.py` | 15 条限制 | branch 不生效、retry 不执行、参数不插值 |
| 2 | **流式 ASR 后端** | `streaming_listener.py:39` | 直接 raise | 无实时语音交互 |
| 3 | **DeviceBackend 协议** | `devices/base.py` | 24 方法全为 `...` | 跨设备控制无法工作 |
| 4 | **SearchProvider 协议** | `search/base.py:43` | Protocol 占位 | Web 搜索无法工作 |

### P1 重要半成品（主链能跑但缺关键环节）

| # | 功能 | 位置 | 状态 | 影响 |
|---|---|---|---|---|
| 5 | **Embedding Provider 占位** | `knowledge/embedding.py:13-31` | 默认哈希占位 | RAG 召回不准 |
| 6 | **Reranker 未接真实模型** | `knowledge/reranker.py` | 词重叠打分 | 重排序质量差 |
| 7 | **MCP SSE/HTTP Transport** | `mcp_adapter.py:242-249` | 仅 stdio | 无法接远程 MCP |
| 8 | **Feishu Executor dry_run** | `feishu.py:16` | 硬编码 True | 无法实际发送消息 |
| 9 | **训练执行只输出命令** | 多处 | 生成 unsloth 命令 | 无法自动微调 |
| 10 | **Audit/Replay 报告管道** | 前端页面已存在 | 缺数据生成 | 报告无法查看 |
| 11 | **Remote Worker 真机端** | `remote_executor.py` | 协议存在未验证 | 远程控制不通 |
| 12 | **PDD RPA 未真机验证** | 电商模块 | 代码存在未验证 | 上架流程不通 |
| 13 | **External Reviewer Agent** | `agent_management.py:380` | enabled=False | 代码审查走不通 |
| 14 | **部分 Agent 仅 persona** | `game_development`/`video_animation` | 无工具链 | 无法执行任务 |

### P2 计划功能（已规划未实现）

| # | 功能 | 位置 | 状态 |
|---|---|---|---|
| 15 | **LLM 驱动修复建议器** | `repair.py:63-66` | 抽象未扩展 |
| 16 | **LangGraph/CrewAI 适配器** | `agent_adapters.py:39-45` | 仅 Native 实现 |
| 17 | **执行回路自愈** | 文档提及 | stderr 不回喂、无自动重试 |

### 已弃用功能

| # | 功能 | 状态 |
|---|---|---|
| 18 | **Live2D 完整链路** | 2026-07-10 用户确认弃用，保留代码但不投入 |

---

## 3. 改进批次总览

### 3.1 批次依赖关系图

```
阶段一（地基）：
  M1 记忆系统语义化 ──┐
                    ├──> M5 Avatar 陪伴感（依赖 M1 Embedding）
  M2 上帝类拆分 ─────┤
                    └──> M3 白名单动态化（依赖 M2 Scheduler）
                         M6 循环依赖反转（依赖 M2）
                         M8 技能扩展自动化（依赖 M2）

阶段二（扩展）：
  M4 执行回路自愈 ──> 独立，可并行
  M7 MCP 生态增强 ──> 独立，可并行

阶段三（补全）：
  M9 基础协议补全 ──> 独立，可并行
  M10 运维自动化补全 ──> 依赖 M9 DeviceBackend
  M11 配置激活与适配器 ──> 独立，最后做

阶段四（主动陪伴）：
  M13 关系系统 ─────────┐
                       ├──> M12 主动交互 ──> M15 开场气泡
  M1 记忆语义化 ───────┘
  M14 定时调度器 ─────────> M12 主动交互 / M16 语音通话
  M16 语音通话窗口 ───────> 复用 M9 ASR/TTS
  M17 音乐播放器 ─────────> 独立，可并行
  M18 游戏自动化 ─────────> 依赖 M8/M9/M10，P3 可选
```

### 3.2 批次优先级与工作量

| 批次 | 优先级 | 工作量 | 预计周期 | 核心价值 |
|---|---|---|---|---|
| **M1 记忆系统语义化** | P0 | 中（5-7天） | 1 周 | 所有语义功能的地基 |
| **M2 上帝类拆分** | P0 | 大（10-14天） | 2 周 | 架构收敛，长期可维护 |
| **M3 白名单动态化** | P1 | 中（5-7天） | 1 周 | 授权系统统一 |
| **M4 执行回路自愈** | P0 | 中（5-7天） | 1 周 | 成功率杠杆 |
| **M5 Avatar 陪伴感** | P1 | 中（5-7天） | 1 周 | 用户体验提升 |
| **M6 循环依赖反转** | P1 | 小（3-5天） | 3 天 | 解除编译循环 |
| **M7 MCP 生态增强** | P2 | 中（5-7天） | 1 周 | 远程 MCP 生态 |
| **M8 技能扩展自动化** | P1 | 中（5-7天） | 1 周 | 新工具零代码接入 |
| **M9 基础协议补全** | P0 | 大（10-14天） | 2 周 | 填坑，协议完整性 |
| **M10 运维自动化补全** | P1 | 大（10-14天） | 2 周 | 商业价值高 |
| **M11 配置激活与适配器** | P2 | 小（2-3天） | 2 天 | 小成本激活功能 |
| **M12 主动交互系统** | P1 | 中（5-7天） | 1 周 | 从被动响应转向场景驱动 |
| **M13 关系系统** | P1 | 小（3-4天） | 4 天 | 最高 ROI，避免重复冒犯边界 |
| **M14 定时调度器** | P2 | 中（6-8天） | 1 周 | 可靠提醒与周期任务 |
| **M15 开场气泡系统** | P1 | 大（8-10天） | 2 周 | 第一视口生命感与主动陪伴入口 |
| **M16 语音通话窗口** | P2 | 中（5-7天） | 1 周 | 连续语音陪伴体验 |
| **M17 音乐播放器** | P2 | 小（3-5天） | 1 周 | 陪伴场景媒体能力 |
| **M18 游戏自动化** | P3 | 中（5-7天） | 1 周，可选 | 受控游戏任务执行 |

**原计划工作量**：70-95 天（M1-M11）
**扩展工作量**：35-48 天（M12-M18）
**总工作量**：105-143 天（约 6 个月）
**并行实施**：阶段一（M1+M2）→ 阶段二（M3-M8）→ 阶段三（M9-M11）→ 阶段四优先 M13，再推进 M12/M15，其余并行。

### 3.4 扩展批次价值优先级

扩展批次不按编号顺序实施，按价值/工作量比排序：

1. **M13 关系系统**：先建立明确边界与关怀策略，防止后续主动功能重复打扰用户。
2. **M1 记忆语义化**：关系与主动触发都依赖可靠的长期语义召回。
3. **M2 上帝类拆分**：主动事件进入主链前先收敛 AgentCluster 职责。
4. **M12 主动交互**：在关系边界、记忆和调度基础上产生可解释建议。
5. **M15 开场气泡**：最后把主动建议呈现在 Avatar 第一视口，避免 UI 先于策略落地。

### 3.3 覆盖率分析

**已覆盖的未完成功能**：
- ✅ P0-1 工作流执行语义（独立方案 W1~W5）
- ✅ P1-5 Embedding Provider 占位 → M1
- ✅ P1-6 Reranker 未接真实模型 → M1
- ✅ P1-7 MCP SSE/HTTP Transport → M7
- ✅ P1-14 部分 Agent 仅 persona → M11
- ✅ 上帝类拆分 → M2
- ✅ 白名单硬编码 → M3
- ✅ 执行回路自愈 → M4
- ✅ Avatar 生命感 → M5
- ✅ 循环依赖 → M6
- ✅ 技能扩展 → M8

**新增补全功能**：
- ✅ P0-2 流式 ASR 后端 → M9
- ✅ P0-3 DeviceBackend 协议 → M9
- ✅ P0-4 SearchProvider 协议 → M9
- ✅ P1-8 Feishu dry_run → M11
- ✅ P1-9 训练执行 → M11（文档说明或占位）
- ✅ P1-10 Audit/Replay 报告管道 → M10
- ✅ P1-11 Remote Worker 真机端 → M10
- ✅ P1-12 PDD RPA 验证 → M10
- ✅ P1-13 External Reviewer Agent → M11
- ✅ P2-15 LLM 修复建议器 → M11（可选）
- ✅ P2-16 LangGraph/CrewAI 适配器 → M11

**覆盖率**：20/20 项（100%）

---

## 4. 详细实施方案

### M1: 记忆系统语义化

**目标**：用真实 Embedding 替换哈希占位，引入 DMAE 式激活度召回，让长期记忆真正参与对话。

**实施路径**：
1. **接入真实 Embedding Provider**（`knowledge/embedding.py:13-31`）：新增 `LMStudioEmbeddingProvider`（nomic-embed，本地 http://localhost:1234/v1/embeddings），保留哈希占位作 fallback，工厂函数按配置选择。
2. **Reranker 接真实模型**（`knowledge/reranker.py`）：新增 `EmbeddingReranker`，用查询与候选段的余弦相似度替换词重叠打分；接口签名不变。
3. **长期记忆激活度引擎**（新建 `memory/activation.py`）：实现 DMAE 简化版——用户命中奖励、模型维护奖励、时间衰减，三态状态机（Active/Dormant/Archived）。
4. **召回注入 Prompt**（`agent_cluster.py` 记忆组装段）：每轮对话前取 Activation ≥ 30 的记忆条目，按分数排序取 Top-5 注入 system prompt。
5. **情绪标签向量化**（`avatar` 情绪匹配处）：为现有情绪标签预计算 embedding，回复文本与标签做余弦匹配，替换硬编码 `<emotion:xxx>` 关键词判断。

**关键代码示例**：
```python
# memory/activation.py（伪代码）
class MemoryActivation:
    BU, GAMMA = 20.0, 0.5    # 用户命中
    BM, LAMBDA = 8.0, 0.3    # 模型维护
    ALPHA, BETA = 1.5, 0.3   # 遗忘

    def on_user_hit(self, mem):
        mem.activation += self.BU * (1 + self.GAMMA * math.log(1 + mem.hit_count))
        mem.hit_count += 1

    def decay_tick(self, mem, days_idle, days_since_maintain):
        d = (self.ALPHA * days_idle**2 + self.BETA * days_since_maintain**2) / math.sqrt(mem.intrinsic_value)
        mem.activation -= d
        mem.state = ("archived" if mem.activation <= 0
                     else "dormant" if mem.activation < 30 else "active")

def recall_for_prompt(query_emb, memories, top_k=5):
    active = [m for m in memories if m.state == "active"]
    scored = [(cosine(query_emb, m.embedding) * (1 + m.activation / 100), m) for m in active]
    return [m for _, m in sorted(scored, reverse=True)[:top_k]]
```

**验收标准**：
- pytest 新增：`test_embedding_provider_fallback`（LM Studio 不可达时回落哈希）、`test_activation_state_machine`（三态转换边界）、`test_recall_top_k`（激活度加权排序正确）、`test_reranker_cosine`（相似段排前）。
- 手测：对话中提及一周前记录的偏好，验证记忆被召回注入；LM Studio 关闭时系统不崩溃且日志记录降级。

**当前实现与实测（2026-07-17）**：
- `EmbeddingService` 以配置指纹缓存进程级实例，统一知识库、长期记忆、Embedding reranker 和 Avatar 的 provider、超时、维度、调用统计与降级状态；运行时修改 embedding 配置会清空旧实例。
- provider 从真实向量切换到 hashing fallback 且维度变化时，知识检索器会重新索引，持久向量搜索拒绝不同维度直接计算余弦。
- `scripts/evaluate_embedding_retrieval.py` 对 `config/evals/embedding_retrieval.json` 计算 Recall@1、Recall@K、MRR，并把报告写入 `state/evaluations/embedding/latest.json`；搜索管理快照暴露最新结果。
- 本机 LM Studio 实测：Nomic 原始 embedding Recall@1=0.33、Recall@3=0.50、MRR=0.49；经项目配置的 Qwen reranker 后 Recall@1/Recall@3/MRR 均为 1.0，768 维，未降级，门禁通过。原始和最终指标同时保留，不掩盖底层模型短板。

---

### M2: 上帝类拆分

**目标**：将 `agent_cluster.py`（2264 行、26 构造参数）拆分为路由、执行、确认、回复组装四个独立模块，参照 Cyrene 两阶段 FC 循环。

**实施路径**：
1. **绘制职责地图**：先在 `agent_cluster.py` 标注四类职责段落（路由决策 / 工具执行 / 审核确认 / 回复组装），产出拆分清单，不动代码。
2. **抽取 `cluster/router.py`**：消息→Agent/工具的路由决策，输入 ClusterContext，输出 RouteDecision，纯函数化。
3. **抽取 `cluster/executor_bridge.py`**：工具/技能调用与结果收集（对应 Tool Phase），持有执行器引用，不持有人格状态。
4. **抽取 `cluster/reply_composer.py`**：人格回复组装（对应 Soul Phase），只接收工具结果摘要，不接触 tools schema。
5. **构造参数收敛**：26 个构造参数收敛为 `ClusterDeps` dataclass（分组：llm / memory / executors / safety / events），`AgentCluster` 保留为薄门面（Facade），对外 API 不变。

**关键代码示例**：
```python
# cluster/deps.py（伪代码）
@dataclass
class ClusterDeps:
    llm: LLMGateway
    memory: MemoryFacade
    executors: ExecutorRegistry
    safety: SafetyGate        # 包含 evaluate_execution_safety，禁改
    events: EventBus

# agent_cluster.py 变为薄门面
class AgentCluster:
    def __init__(self, deps: ClusterDeps):
        self.router = Router(deps)
        self.exec_bridge = ExecutorBridge(deps)
        self.composer = ReplyComposer(deps)

    async def handle(self, msg):
        decision = self.router.route(msg)             # Phase 0: 路由
        tool_results = await self.exec_bridge.run(decision)  # Phase 1: 工具
        return await self.composer.compose(msg, tool_results)  # Phase 2: 灵魂
```

**验收标准**：
- pytest 新增：`test_router_pure`（相同输入相同 RouteDecision）、`test_composer_no_tools_schema`（Soul Phase prompt 不含工具定义）、`test_facade_api_compat`（旧调用方全部通过）；现有 136+ 用例零回归。
- 手测：完整对话（含工具调用、审核确认、多 Agent 协作）行为与拆分前一致；协作 seen/ack、发言队列 v4 相关流程逐一走一遍（禁改区回归）。

---

### M3: 白名单动态化

**目标**：将 6 处硬编码工具白名单统一为动态授权注册表，参照 Cyrene ToolRegistry 的风险分级。

**实施路径**：
1. **新建 `security/tool_authz.py`**：`ToolAuthzRegistry`，每个工具带 `risk`（safe/network/shell/fs-write）与 `enabled`，数据落 `config/tool_authz.json`。
2. **迁移 6 处硬编码**：grep 定位所有硬编码白名单，逐处替换为 `authz.is_allowed(tool_id, context)`，保留原语义作默认值（首次生成 json 时导入）。
3. **风险分级默认策略**：safe 自动放行；network/fs-write 需会话内一次确认；shell 每次确认——与 `evaluate_execution_safety` 双重校验叠加（不替代，禁改）。
4. **管理面板接入**：WPF 管理面板新增工具授权页（读写 tool_authz.json），改动仅数据源，布局不动。

**关键代码示例**：
```python
# security/tool_authz.py（伪代码）
class ToolAuthzRegistry:
    def is_allowed(self, tool_id, ctx) -> AuthzResult:
        entry = self._entries.get(tool_id)
        if entry is None or not entry.enabled:
            return AuthzResult.DENY
        policy = {"safe": ALLOW, "network": CONFIRM_ONCE,
                  "fs-write": CONFIRM_ONCE, "shell": CONFIRM_EACH}[entry.risk]
        return policy.evaluate(ctx.session_confirmations)
    # 注意：authz 通过后仍必须走 evaluate_execution_safety —— 双重校验，禁删
```

**验收标准**：
- pytest 新增：`test_authz_migration_parity`（迁移后行为与 6 处硬编码逐一等价）、`test_risk_policy`（四级风险策略）、`test_double_check_preserved`（authz 放行后 evaluate_execution_safety 仍被调用）。
- 手测：面板关闭某工具后调用被拒；shell 类工具每次弹确认；Kill Switch soft/hard stop 不受影响。

---

### M4: 执行回路自愈

**目标**：stderr 回喂模型 + 分类自动重试，把"报错即终止"变为"报错即自愈"，直接提升任务成功率。

**实施路径**：
1. **stderr 结构化回喂**（`ExecutionResult` 组装处）：失败结果附加 `failure_context`（stderr 尾部 2KB + exit code + 命令），注入下一轮 LLM 消息，替代当前"捕获但丢弃"。
2. **错误分类器**（新建 `execution/failure_classifier.py`）：正则规则分类——transient（超时/连接重置）、fixable（依赖缺失/路径错误/语法错）、fatal（权限拒绝/Kill Switch）。
3. **重试策略**（新建 `execution/retry_policy.py`）：transient 指数退避重试 ≤3 次；fixable 回喂 LLM 生成修复动作后重试 ≤2 次；fatal 立即上报不重试。
4. **CLI 探测补盲**：执行前 `shutil.which` 探测命令存在性，缺失时直接归类 fixable 并附安装建议，消除探测盲区。
5. **自愈轨迹日志**：新增 `self_heal.jsonl`——⚠️ 必须走缓存+轮转写入，禁止全量重读（jsonl 热路径 O(N²) 教训）。

**关键代码示例**：
```python
# execution/retry_policy.py（伪代码）
async def run_with_healing(request, executor, llm):
    for attempt in range(MAX_ATTEMPTS):
        result = await executor.run(request)
        if result.ok:
            return result
        kind = classify(result.stderr, result.exit_code)
        if kind == "fatal":
            return result.with_context("fatal, no retry")
        if kind == "transient":
            await asyncio.sleep(2 ** attempt)
            continue
        # fixable: stderr 回喂模型生成修复
        fix = await llm.suggest_fix(request, tail(result.stderr, 2048))
        request = apply_fix(request, fix)  # 修复动作仍需过 evaluate_execution_safety
    return result
```

**验收标准**：
- pytest 新增：`test_classify_transient/fixable/fatal`、`test_retry_backoff`（退避次数与间隔）、`test_stderr_feedback_in_prompt`（失败上下文出现在下一轮消息）、`test_fatal_no_retry`（Kill Switch 触发零重试）。
- 手测：故意执行缺依赖脚本，观察自动 pip install 后重试成功；断网执行网络命令，观察退避重试与最终上报；`self_heal.jsonl` 高频写入下网关无卡顿。

---

### M5: Avatar 陪伴感增强

**目标**：让 VRM Avatar 在 idle 时具备呼吸/摇摆/眨眼等生命感，并用 M1 向量化情绪匹配驱动表情。

**实施路径**：
1. **idle 微动作循环**（Three.js Avatar 渲染层，前端 JS）：呼吸（胸腔 scale 正弦波，周期 ~4s）、重心摇摆（hips 微幅旋转，周期 ~7s）、随机眨眼（泊松间隔 2-6s）。
2. **视线追随**：鼠标位置映射头部/眼球 LookAt，带阻尼插值（lerp 0.1），越界时缓慢回正。
3. **情绪→表情映射向量化**：接 M1 的情绪 embedding 匹配结果，驱动 VRM BlendShape（happy/sad/surprised/neutral），过渡用 300ms 缓动。
4. **说话状态动作**：TTS 播放期间叠加口型（音量包络→口型开合）与轻微手势循环。
5. **日/夜主题适配灯光**：Avatar 场景灯光跟随主题切换（企业级+科技感规范）；⚠️ 验收对比概念稿前先确认当前生效主题（主题启动模式陷阱）。

**关键代码示例**：
```typescript
// avatar/idle_motion.ts（伪代码）
function updateIdle(vrm: VRM, t: number) {
  const breath = Math.sin(t * 2 * Math.PI / 4.0) * 0.012;
  vrm.humanoid.getNormalizedBoneNode('chest').scale.setScalar(1 + breath);
  const sway = Math.sin(t * 2 * Math.PI / 7.0) * 0.02;
  vrm.humanoid.getNormalizedBoneNode('hips').rotation.z = sway;
  if (t >= nextBlinkAt) { playBlink(vrm); nextBlinkAt = t + 2 + Math.random() * 4; }
}
function setEmotion(vrm: VRM, label: string, weight: number) {
  tween(vrm.expressionManager, label, weight, 300 /*ms*/);
}
```

**验收标准**：
- pytest/Vitest 新增：`test_emotion_vector_match`（后端匹配 Top-1 正确率抽样）、前端 `idle_motion.spec`（呼吸/眨眼参数边界）。
- 手测：静置 60 秒观察呼吸+摇摆+眨眼自然不穿模；发送高兴/难过消息观察表情切换平滑；日/夜主题各验一遍灯光；低端机帧率 ≥ 30fps。

---

### M6: 循环依赖反转

**目标**：解除 orchestrator ↔ app 循环依赖，通过接口下沉与事件解耦实现单向依赖。

**实施路径**：
1. **依赖扫描**：用 `pydeps`/grep 列出 orchestrator 反向 import app 的所有符号（预计 3-6 处）。
2. **接口下沉**：新建 `core/interfaces.py`，定义 `AppServices` Protocol（orchestrator 实际用到的 app 能力子集）。
3. **注入替换**：orchestrator 构造时注入 `AppServices` 实现，删除全部 `from app import ...` 反向引用。
4. **事件化剩余耦合**：无法接口化的调用（如通知 UI）改为 EventBus 发布，app 侧订阅。

**关键代码示例**：
```python
# core/interfaces.py（伪代码）
class AppServices(Protocol):
    def get_session(self, sid: str) -> Session: ...
    def notify_ui(self, event: dict) -> None: ...

# orchestrator.py：不再 import app
class Orchestrator:
    def __init__(self, services: AppServices, bus: EventBus): ...

# app.py：组装根（composition root）
orchestrator = Orchestrator(services=AppServicesImpl(app), bus=bus)
```

**验收标准**：
- pytest 新增：`test_no_reverse_import`（AST 扫描断言 orchestrator 包内无 `import app`）、`test_orchestrator_with_fake_services`（用 Fake 实现独立实例化）。
- 手测：应用冷启动、任务编排、UI 通知全链路正常；import 时间无明显退化。

---

### M7: MCP 生态增强

**目标**：MCP 适配器在 stdio 之外支持 SSE 与 Streamable HTTP transport，可接入远程 MCP Server。

**实施路径**：
1. **Transport 抽象**（`mcp_adapter.py:242-249`）：抽出 `McpTransport` 基类（connect/send/receive/close），现有 stdio 实现迁移为 `StdioTransport`。
2. **新增 `SseTransport`**：httpx 长连接接收 SSE 事件流，POST 回发消息，处理断线重连（指数退避）。
3. **新增 `StreamableHttpTransport`**：单端点 POST + 分块响应，兼容 MCP 2025 规范会话头（Mcp-Session-Id）。
4. **配置扩展**：MCP server 配置增加 `transport: stdio|sse|http` 与 `url` 字段，远程 server 强制过 `evaluate_gateway_request_safety` 拦截。
5. **健康检查与降级**：远程 transport 心跳失败 3 次标记 server 不可用，工具列表自动摘除并事件通知。

**关键代码示例**：
```python
# mcp/transports.py（伪代码）
class SseTransport(McpTransport):
    async def connect(self):
        self._stream = await self._client.stream("GET", self.url, headers=SSE_HEADERS)
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        async for event in parse_sse(self._stream):
            if event.type == "message":
                self._inbox.put_nowait(json.loads(event.data))

    async def send(self, msg):
        await self._client.post(self.post_url, json=msg)  # 过网关安全评估
```

**验收标准**：
- pytest 新增：`test_transport_factory`（三种 transport 按配置实例化）、`test_sse_reconnect`（mock 断线后指数退避重连）、`test_remote_server_gateway_check`（远程调用必过网关安全评估）、`test_health_degrade`（3 次心跳失败摘除工具）。
- 手测：接入一个公开远程 MCP server（如 fetch server 的 SSE 部署），完成一次工具调用；中途 kill server 观察降级与恢复。

---

### M8: 技能扩展自动化

**目标**：新工具/技能从"改 3 处代码"变为"注册即用"，并自动生成工作流节点定义。

**实施路径**：
1. **统一注册入口**：梳理现有 3 处改动点（工具注册表 / 白名单 / 工作流节点表），收敛为 `registry.register_tool(defn)` 一个入口。
2. **声明式工具定义**：工具目录扫描 `tools/*/manifest.json`（id/description/risk/input_schema/entry），启动时自动注册——risk 字段直通 M3 的 ToolAuthzRegistry。
3. **工作流节点自动生成**：注册工具时同步生成 `tool_call` 节点 schema，管理面板工作流编辑器下拉即可见，无需改 `workflow_graph.py`。
4. **热加载（可选）**：文件监听 manifest 变化，运行期增量注册/注销，失败不影响已有工具。

**关键代码示例**：
```python
# skills/auto_register.py（伪代码）
def scan_and_register(root: Path, registry, authz, workflow_catalog):
    for manifest in root.glob("*/manifest.json"):
        defn = ToolDefinition.from_json(manifest)      # 校验 schema，失败跳过并记日志
        registry.register_tool(defn)                   # 1. 工具注册表
        authz.ensure_entry(defn.id, defn.risk)         # 2. 授权表（默认按 risk 策略）
        workflow_catalog.add_node_schema(defn)         # 3. 工作流节点
```

**验收标准**：
- pytest 新增：`test_manifest_scan_register`（放置合法 manifest 后三处注册齐全）、`test_bad_manifest_skipped`（非法 manifest 跳过且不崩）、`test_workflow_node_generated`（节点 schema 与 input_schema 一致）。
- 手测：新建一个 echo 工具目录（仅 manifest + 脚本），重启后对话可调用、面板工作流可拖入，全程零 Python 代码改动。

---

### M9: 基础协议补全

**目标**：补全 DeviceBackend（24 个 `...` 方法）、SearchProvider、流式 ASR 三个占位协议，使其至少各有一个可用实现。

**实施路径**：
1. **DeviceBackend 最小实现**（`devices/base.py`）：先实现 `LocalDeviceBackend`（本机屏幕截图/输入/文件），24 个方法按"必须实现/可 NotImplemented 但有明确错误消息"分层，消除静默 `...`。
2. **ADB DeviceBackend**：Android 场景实现 screencap/input tap/pm list 等核心 8 方法，复用现有 Android 执行器连接管理。
3. **SearchProvider 实现**（`search/base.py:43`）：实现 `DuckDuckGoProvider`（免 key）与 `BraveProvider`（配 key），统一 `SearchResult` 模型，带超时与结果数上限。
4. **流式 ASR 后端**（`streaming_listener.py:39`）：接 `faster-whisper` 本地流式转写（VAD 分段 + 增量解码），保留 raise 分支仅在无任何后端可用时触发，错误消息含安装指引。
5. **协议一致性测试**：为每个 Protocol 写契约测试基类，任何实现继承即得全套用例。

**关键代码示例**：
```python
# devices/contract_test.py（伪代码，契约测试基类）
class DeviceBackendContract:
    backend: DeviceBackend  # 子类提供

    def test_screenshot_returns_png(self):
        img = self.backend.screenshot()
        assert img[:8] == PNG_MAGIC

    def test_unsupported_raises_clear_error(self):
        with pytest.raises(DeviceCapabilityError, match="not supported on"):
            self.backend.set_clipboard("x")  # 允许不支持，但错误必须明确

# streaming_listener.py（伪代码）
async def stream_transcribe(audio_chunks):
    model = WhisperModel("small", compute_type="int8")
    async for chunk in vad_segment(audio_chunks):
        for seg in model.transcribe(chunk, beam_size=1)[0]:
            yield PartialTranscript(text=seg.text, final=seg.is_final)
```

**验收标准**：
- pytest 新增：`LocalDeviceBackend`/`AdbDeviceBackend` 各继承契约基类（~10 用例×2）、`test_search_provider_timeout`、`test_asr_partial_stream`（mock 音频产生增量文本）。
- 手测：语音说一句话看到增量字幕上屏；搜索"今天天气"返回结构化结果；ADB 连真机截图成功。⚠️ 本机 pytest 走 Anaconda 环境（本机工具链）。

---

### M10: 运维自动化补全

**目标**：打通 Remote Worker 真机执行端、Audit/Replay 报告数据管道、PDD RPA 真机验证三条商业链路。

**实施路径**：
1. **Remote Worker 端**（`remote_executor.py` 对端）：实现独立 `worker_daemon.py`——WebSocket 长连接注册、心跳、接收 ExecutionRequest、回传 ExecutionResult；执行前本地也过 `evaluate_execution_safety`（双端双重校验）。
2. **Worker 部署包**：pyinstaller 单文件 + 配置向导（网关地址/token），一台真机部署验证往返延迟与断线重连。
3. **Audit/Replay 数据生成**：前端页面已存在，补后端聚合——从审计 jsonl 按会话聚合生成报告 JSON（⚠️ 增量读取+游标，禁全量重读）。
4. **PDD RPA 真机验证**：既有代码走扩展/RPA 路线真机跑通登录→选品→上架草稿；图片处理（裁剪/水印）接 FFmpeg 执行器；换 IP 环节仅做代理配置接口预留，不做绕过。
5. **运维手册**：worker 部署、RPA 环境准备、故障排查各一节，落 `docs/ops/`。

**关键代码示例**：
```python
# worker_daemon.py（伪代码）
async def main():
    async with websockets.connect(f"{GATEWAY}/worker?token={TOKEN}") as ws:
        await ws.send(register_frame(capabilities=detect_capabilities()))
        async for frame in heartbeat_wrap(ws, interval=15):
            req = ExecutionRequest.from_frame(frame)
            verdict = evaluate_execution_safety(req)   # 真机端二次校验，禁删
            if not verdict.allowed:
                await ws.send(result_frame(denied(verdict))); continue
            result = await run_sandboxed(req, timeout=req.timeout)
            await ws.send(result_frame(result))        # 含 stderr 尾部，供 M4 回喂
```

**验收标准**：
- pytest 新增：`test_worker_register_heartbeat`（mock 网关握手）、`test_worker_safety_check`（危险请求真机端拒绝）、`test_audit_report_incremental`（游标增量聚合，二次调用不重读）、`test_replay_report_schema`（前端页面契约字段齐全）。
- 手测：真机 worker 执行 `echo` 往返 < 2s；拔网线 30s 后自动重连；PDD 上架草稿全流程录屏留档；审计报告页面能打开当日报告。

---

### M11: 配置激活与适配器

**目标**：以最小成本激活已存在但被禁用/占位的功能——Feishu 实发、External Reviewer、persona Agent 工具链、适配器骨架。

**实施路径**：
1. **Feishu dry_run 可配置**（`feishu.py:16`）：硬编码 `True` 改为读配置 `feishu.dry_run`（默认仍 True），实发路径补 webhook 签名与失败重试一次。
2. **External Reviewer 激活**（`agent_management.py:380`）：`enabled=False` 改为配置驱动，接入代码审查工作流（review_gate 节点前置调用）。
3. **persona Agent 补工具链**：`game_development`/`video_animation` 挂接现有执行器（Python/FFmpeg/Browser），manifest 声明式接入（复用 M8）。
4. **LangGraph/CrewAI 适配器骨架**（`agent_adapters.py:39-45`）：实现 `LangGraphAdapter` 最小可用版（graph → agent_task 映射），CrewAI 留清晰 NotImplemented + 文档说明。
5. **训练执行说明**（P1-9）：不做自动微调，文档化"命令生成→人工执行"流程，占位函数补明确提示。

**关键代码示例**：
```python
# feishu.py（伪代码）
class FeishuExecutor:
    def __init__(self, cfg):
        self.dry_run = cfg.get("feishu.dry_run", True)  # 默认安全

    async def send(self, msg):
        if self.dry_run:
            return ExecutionResult.ok(f"[dry_run] would send: {msg.summary()}")
        resp = await self._post_signed(msg)             # 签名 + 失败重试 1 次
        return ExecutionResult.from_response(resp)
```

**验收标准**：
- pytest 新增：`test_feishu_dry_run_default`（无配置时不实发）、`test_reviewer_config_toggle`、`test_persona_agent_has_tools`（两个 Agent 工具列表非空）、`test_langgraph_adapter_minimal`（简单 graph 可执行）。
- 手测：配置打开后飞书群收到真实消息；代码审查任务中 External Reviewer 产出意见；game_development Agent 能实际调用 Python 执行器跑脚本。

---

### M12: 主动交互系统

**目标**：让 SpiritKin 在有充分场景证据、用户允许且不会造成打扰时主动提出一次有价值的建议，而不是只能等待输入；主动交互永远不能直接执行有副作用的动作。

**设计原则**：
- **同意优先**：主动级别默认低，M13 的 `proactive` 边界可以立即关闭全部非必要主动消息。
- **场景驱动**：只有可解释触发源才能生成建议，禁止无上下文随机寒暄占用注意力。
- **建议与执行分离**：主动事件只产生 `suggestion`，用户确认后才进入现有 Planner、Authz 与 Safety Gate。
- **节流可追踪**：每次触发、抑制、展示和用户反馈均记录原因码，不用模型自行决定频率。

**实施路径**：
1. 新建 `backend/proactive/signals.py`：统一 `ProactiveSignal`，接入 Presence、任务完成/失败、长时间空闲、日程临近、设备异常与关系状态变化。
2. 新建 `backend/proactive/policy.py`：按用户边界、安静时段、冷却时间、每日上限、当前会话活跃度和信号优先级做纯函数决策。
3. 新建 `backend/proactive/service.py`：将通过策略的信号转换为 `ProactiveSuggestion`，只允许 `inform/check_in/offer_action/reminder` 四种意图。
4. 事件协议增加 `proactive.suggested`、`proactive.suppressed`、`proactive.feedback`；所有事件带 `signal_id`、`reason_code`、`relationship_stage` 与 `requires_confirmation`。
5. Desktop/Mobile 先复用通知与会话事件入口；M15 完成后，低打扰建议改由开场气泡承载。
6. 用户接受建议后重新构造普通 `InteractionInput`，不得让主动服务直接持有 Executor 或 ToolRegistry。

**核心数据契约**：
```python
@dataclass(frozen=True)
class ProactiveDecision:
    allowed: bool
    reason_code: str
    cooldown_until: float
    requires_confirmation: bool = True

def evaluate(signal, relationship, presence, policy) -> ProactiveDecision:
    # 关系边界 > 安静时段 > 频率限制 > 场景价值
    ...
```

**验收标准**：
- pytest：`test_proactive_boundary_blocks`、`test_quiet_hours_suppress`、`test_daily_limit`、`test_high_value_signal_allowed`、`test_suggestion_has_no_executor`、`test_feedback_adjusts_cooldown`。
- 手测：连续制造 20 个低价值信号只出现一次建议；设置“不要主动提醒”后立即零展示；接受建议后仍弹现有执行确认。

---

### M13: 关系系统

**目标**：建立持久、可解释、边界优先的用户关系状态。系统可以逐步形成熟悉感，但不得把交互次数等同于亲密许可，尤其不能重复触碰用户已经明确表达的边界。

**实施路径**：
1. 新建 `backend/memory/relationship.py`：持久化 `trust`、`familiarity`、关系阶段、正向反馈、纠正次数、最近信号与显式边界。
2. **显式边界提取**：只处理“不要再/别再/不许/我不想聊”等明确表达；保存经清理的短 subject，不把整段对话或任意 Prompt 当作边界。
3. **稳定去重与撤回**：语义相近边界更新重复次数，不重复新增；只有“现在可以/不介意/不用避开”等明确表达才能停用已有边界。
4. **关怀策略**：输出 `focused_support/quiet_presence/boundary_acknowledgement/repair_and_listen/gentle_support/steady_companion`，供 M12/M15/M16 消费。
5. **Soul Phase 注入**：关系阶段和有效边界进入无工具定义的回复阶段；明确边界为高优先级约束，当前输入明确撤回时立即更新。
6. **运行时事件**：能力快照包含 relationship，交互后发送 `relationship.updated`，便于多端同步但不在 UI 暴露原始敏感内容。
7. **本地优先**：默认数据落 `state/relationship.json`，支持 `SPIRITKIN_RELATIONSHIP_PATH`；写入使用临时文件替换，避免半写状态。

**关系阶段规则**：
| 阶段 | 最低交互 | 最低信任 | 行为边界 |
|---|---:|---:|---|
| new | 0 | 无 | 礼貌、克制，不使用昵称 |
| acquainted | 5 | 无 | 可引用已确认偏好，不推断亲密关系 |
| familiar | 20 | 0.62 | 可更自然，但主动级别仍默认低 |
| trusted | 50 | 0.75 | 可提供常规主动建议，硬边界仍拥有最高优先级 |

**验收标准**：
- pytest：`test_explicit_boundary_persists`、`test_boundary_deduplicates`、`test_explicit_release`、`test_scores_clamped`、`test_relationship_prompt_context`、`test_cluster_observes_before_llm`、`test_relationship_event`。
- 手测：说“以后不要叫我宝宝”，重启后再次对话不使用该称呼；重复表达不产生多条边界；明确说“现在可以这样叫”后边界停用。

---

### M14: 定时调度器

**目标**：提供可靠、可恢复、时区正确的提醒与周期任务调度，为主动交互和设备任务提供统一时间触发源。

**实施路径**：
1. 采用成熟调度引擎 APScheduler，禁止自写 cron 解析器；封装为 `SchedulerService`，业务层只依赖项目 DTO。
2. 使用 SQLite JobStore 持久化，一次性、间隔与 cron 三类任务统一为 `ScheduledIntent`，保存原始时区和下一次触发时间。
3. 配置 `misfire_grace_time`、`coalesce`、最大并发和重启恢复；系统睡眠后不得补发大量过期提醒。
4. 触发时先生成事件/建议，涉及工具或设备操作时必须重新通过 M3 Authz 与执行安全双重校验。
5. 暴露列表、暂停、恢复、修改、取消、立即试跑接口；Desktop/Mobile 使用相同命令网关契约。
6. 与 M13 联动：用户关闭主动提醒时，调度任务仍保留但通知类触发被策略抑制，并明确显示原因。

**验收标准**：
- pytest：时区/DST、重启恢复、misfire 合并、幂等触发、并发上限、Kill Switch、边界抑制、危险任务重新确认。
- 手测：创建 2 分钟后提醒并重启服务，提醒只出现一次；跨时区修改后下一次触发正确；睡眠恢复不补发风暴。

---

### M15: 开场气泡系统

**目标**：让 Avatar 在应用首屏用低打扰、可关闭、可追踪的气泡承载问候、恢复提示和 M12 建议，提升生命感但不遮挡主工作流。

**产品约束**：
- 保留现有 Bangboo/Atelier 表现，不重新设计 Avatar 或主布局。
- 气泡是 Avatar 的附属浮层，不是卡片、模态框或营销 Hero。
- 首次可见时只显示一条；用户正在输入、执行确认或查看错误时自动让位。
- `prefers-reduced-motion`、系统低动效和静止模式下只做淡入淡出，不做弹跳/漂浮。

**实施路径**：
1. 新建前端 `opening_bubble.js` 状态机：`hidden -> entering -> visible -> dismissing -> cooldown`，优先级为安全/恢复 > 用户任务 > 关怀 > 问候。
2. 内容来自结构化 `opening_bubble.present` 事件，前端不自行生成文案；事件含 `bubble_id`、`kind`、`text`、`action`、`expires_at`、`motion_policy`。
3. 桌面启动时由后端根据时间、未完成任务、最近失败、M13 关系阶段和 M12 策略选择内容；无足够证据时保持安静。
4. 点击主操作只打开对应会话/任务，不直接执行工具；关闭和忽略回传反馈以调整冷却时间。
5. Three.js Avatar 只接收轻量表情/动作提示，动作必须经过现有语义反应降级和低动效抑制。
6. 响应式验证覆盖 1440×900、1024×768、390×844，气泡不得遮挡输入框、发送按钮、Avatar 面部或系统安全控件。

**验收标准**：
- 前端测试：状态机、优先级、超时、去重、低动效、文本溢出、操作只导航不执行。
- Playwright：桌面/移动截图、Three.js canvas 非空像素、气泡与 Avatar 实际同帧渲染、无横向溢出、静止偏好无位移。
- 手测：连续重启不重复轰炸；存在未完成任务时显示恢复提示；设置“不要主动提醒”后只保留必要安全/恢复消息。

---

### M16: 语音通话窗口

**目标**：在现有 M9 流式 ASR、TTS、打断和 phoneme 事件基础上提供可持续的语音通话窗口，而不是另起一套语音协议。

**实施路径**：
1. WPF 新建独立通话窗口/面板，包含通话状态、麦克风、扬声器、字幕、静音、结束和设备选择；复用既有主题 token 与窗口管理模式。
2. 状态机统一为 `idle/connecting/listening/thinking/speaking/interrupted/reconnecting/ended/error`，后端事件是权威状态。
3. 复用 `StreamingTranscriber`、VAD、TTS 队列和 `speech.phoneme`，用户讲话时按现有打断规则停止播报，不重复播放残余音频。
4. 网络/模型不可用时显示可恢复错误并允许回到文本会话；不把麦克风数据写入长期记忆，除非转写文本进入普通交互链。
5. M13 quiet/care 策略只影响措辞与主动开场，不能自动接听或在未授权时开启麦克风。

**验收标准**：
- 测试：状态转换、VAD 分段、打断、重连、设备切换、结束清理、麦克风权限拒绝、字幕与语音文本一致。
- 手测：连续通话 10 分钟无重复音频；讲话可打断 TTS；拔掉麦克风后可切换设备恢复；结束后无后台采集。

---

### M17: 音乐播放器

**目标**：提供克制、可靠的本地音乐播放能力，服务于陪伴和专注场景，不把完整媒体库管理塞进聊天 Composer。

**实施路径**：
1. 使用成熟 Windows 音频库（优先 NAudio）封装 `MusicPlaybackService`，支持本地文件/目录队列、播放暂停、上一首/下一首、进度、音量和循环模式。
2. UI 采用底部紧凑媒体栏与独立队列抽屉，按钮使用既有图标库；不使用卡片嵌套或大幅封面 Hero。
3. 命令通过结构化 `music.play/pause/seek/queue` 工具进入 ToolRegistry，文件访问遵守 M3 授权与现有路径安全规则。
4. 通话或 TTS 开始时可配置 duck/pause，结束后只在用户允许时恢复；多个音频源由统一 AudioFocus 协调。
5. 不默认联网抓取或下载音乐；远程 URL 属 network 风险，必须显式启用并走网关安全评估。

**验收标准**：
- 测试：队列顺序、seek 边界、音量限制、损坏文件跳过、AudioFocus、目录越界拒绝、远程 URL 授权。
- 手测：播放 30 分钟无资源泄漏；TTS 插入时音乐正确 duck 并恢复；移动/删除当前文件时给出可恢复错误。

---

### M18: 游戏自动化

**目标**：在用户明确选择的游戏和任务范围内提供可暂停、可审计的辅助自动化；该批次为 P3 可选，不以绕过反作弊、验证码或平台规则为目标。

**安全边界**：
- 只支持明确允许自动化的本地/浏览器游戏或测试环境；默认无任何游戏白名单。
- 禁止内存注入、封包修改、反作弊规避、验证码绕过、账号批量控制和无人值守付费操作。
- 所有输入动作有全局停止热键、窗口焦点校验、速率上限和逐步审计。

**实施路径**：
1. 复用 M8 manifest 声明游戏适配器，描述窗口匹配、可见状态、允许动作、停止条件和风险级别。
2. 浏览器游戏优先使用 Playwright；视觉定位复用 M9 截图/识别；规则和物理逻辑使用成熟引擎或游戏提供的 API，不手写通用 bot 内核。
3. 新建 `GameAutomationSession`，只在目标窗口前台且画面状态与预期匹配时发送输入；状态不确定立即暂停。
4. 每个动作先过 Authz、Safety 与适配器白名单；录制关键帧、动作、结果和停止原因，供 Audit/Replay 查看。
5. 首个验收目标选择仓库内可控 Demo/测试游戏，不直接以第三方线上游戏作为首发验证对象。

**验收标准**：
- 测试：窗口焦点丢失停止、Kill Switch、动作速率、未知画面暂停、白名单、危险输入拒绝、审计回放。
- Playwright/手测：在本地 Demo 完成一个 3-5 分钟任务；中途切窗和按停止键均在 200ms 内停止输入；无后台残留进程。

---

## 5. 验收与验证

### 5.1 每批必做

每个批次合并前，依次执行：

```bash
# 1. 全量验证脚本（Anaconda 环境）
python run_verification.py            # 必须全绿

# 2. 静态检查
ruff check .                          # 零新增告警

# 3. 全量回归
pytest -x -q                          # 136+ 存量用例零回归
# ⚠️ 注意管道 exit code 陷阱：不要 `pytest | tee` 后取管道状态，用 tee 时加 `set -o pipefail` 或直接看 pytest 退出码

# 4. 禁改区专项回归（见第 6 节）
pytest tests/test_collaboration_ack.py tests/test_speech_queue_v4.py tests/test_execution_safety.py -q
```

### 5.2 新增测试用例清单

| 批次 | 新增用例（关键项） | 数量约 |
|---|---|---|
| M1 | embedding fallback / 激活度状态机 / Top-K 召回 / reranker | 8 |
| M2 | 路由纯函数 / Soul Phase 无 tools / 门面兼容 | 10 |
| M3 | 迁移等价性×6 / 风险策略 / 双重校验保留 | 10 |
| M4 | 错误分类×3 / 退避 / stderr 回喂 / fatal 零重试 | 8 |
| M5 | 情绪向量匹配 / idle 参数边界（前端） | 4 |
| M6 | 反向 import AST 扫描 / Fake services 实例化 | 3 |
| M7 | transport 工厂 / SSE 重连 / 网关校验 / 健康降级 | 8 |
| M8 | manifest 扫描 / 非法跳过 / 节点生成 | 6 |
| M9 | 设备契约×2 实现 / 搜索超时 / ASR 增量 | 24 |
| M10 | worker 握手 / 真机端安全 / 增量聚合 / 报告契约 | 8 |
| M11 | dry_run 默认 / reviewer 开关 / persona 工具 / 适配器 | 6 |
| M12 | 主动策略 / 安静时段 / 节流 / 建议执行隔离 | 10 |
| M13 | 边界持久化 / 去重撤回 / 关系阶段 / Prompt / 事件 | 9 |
| M14 | 时区 / DST / 恢复 / misfire / 安全门 | 12 |
| M15 | 气泡状态机 / 优先级 / 低动效 / 响应式 | 10 |
| M16 | 通话状态 / VAD / 打断 / 设备 / 清理 | 10 |
| M17 | 播放队列 / AudioFocus / 路径与网络授权 | 8 |
| M18 | 焦点 / 停止 / 速率 / 白名单 / 审计 | 8 |

**M1-M11 合计约 95 个新增用例；M12-M18 追加约 67 个，目标总量 300+。**

### 5.3 手测脚本模板

每批次手测记录统一落 `docs/manual_tests/M{n}_manual_test.md`：

```markdown
# M{n} 手测记录

- **日期**：YYYY-MM-DD
- **环境**：Win11 / Anaconda py312 / 生效主题：Light|Night（⚠️ 先确认，避免主题启动陷阱）
- **前置**：run_verification.py 全绿 ✅

| # | 场景 | 步骤 | 预期 | 实际 | 结果 |
|---|---|---|---|---|---|
| 1 | （场景名） | 1... 2... | ... | ... | ✅/❌ |

- **禁改区抽查**：seen/ack ✅ ｜ 发言队列 v4 ✅ ｜ 安全双重校验 ✅
- **遗留问题**：
- **结论**：通过 / 有条件通过 / 打回
```

---

## 6. 禁改区

以下四个区域在全部 18 个批次中**禁止修改语义**，重构可以移动代码但行为必须逐字节等价，且每批合并前跑专项回归：

### 6.1 协作 seen/ack 语义

- Agent 路由总线的消息 seen/ack 状态机（2026-07-09 双工修复的止损成果）。
- 禁止：改变 ack 时机、合并 seen 与 ack、调整超时重发语义。
- M2 拆分若涉及路由模块，seen/ack 处理代码只允许整体搬移，不允许改一行逻辑。

### 6.2 发言队列 v4

- 多 Agent 发言排队、抢占与顺序保证机制。
- 禁止：改队列优先级算法、插队条件、清空策略。
- M2/M6 涉及事件总线改造时，发言队列的订阅关系必须保持原样。

### 6.3 批次八~十成果

- 多端美术统一（A1~A5 五批次，2026-07-10 已全部验收）：颜色 token、日/夜双主题、组件样式。
- 禁止：任何批次"顺手"改样式/布局。M3/M10 涉及面板新增页面时，必须复用 design/tokens.json 既有 token，新页面走 spiritkin-art-style 规范，不触碰已验收页面。

### 6.4 evaluate_execution_safety 双重校验

- 所有执行路径（本地/远程/worker/MCP 远程）必须经过 `evaluate_execution_safety()`；网关入口必须经过 `evaluate_gateway_request_safety()`。
- 禁止：M3 的 authz 放行**替代**安全评估（只能叠加）；M4 的修复动作绕过评估；M10 的 worker 端省略二次校验。
- 每批新增执行入口时，必须新增对应的 `test_*_safety_check` 用例证明校验在链路上。

---

## 7. 交付时间线

### 7.1 甘特图（文本形式，周为单位）

```
周次        1    2    3    4    5    6    7    8    9    10   11   12   13   14   15   16   17   18   19   20   21   22   23   24
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
阶段一（地基）
M1 记忆语义化  ████████
M2 上帝类拆分  █████████████████
M3 白名单动态化               ████████         (依赖 M2 Scheduler)
M6 循环依赖反转               ████             (依赖 M2)
M8 技能扩展                        ████████    (依赖 M2+M3)

阶段二（扩展，可并行）
M4 执行回路自愈           ████████
M5 Avatar 陪伴感               ████████        (依赖 M1)
M7 MCP 生态增强                     ████████

阶段三（补全）
M9 基础协议补全                          █████████████████
M10 运维自动化                                    █████████████████
M11 配置激活                                                      ████

阶段四（主动陪伴，按价值排序）
M13 关系系统                                                                ████
M1/M2 缺口复核                                                               ████████
M12 主动交互                                                                        ████████
M15 开场气泡                                                                                ████████████
M14 调度器 / M16 语音 / M17 音乐 / M18 可选                                                      █████████████████
───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
里程碑          ▲CP1      ▲CP2           ▲CP3                ▲CP4        ▲CP5      ▲CP6             ▲CP7
```

### 7.2 里程碑检查点

| 检查点 | 时间 | 通过条件 |
|---|---|---|
| **CP1**（第 2 周末） | M1 完成 | 真实 embedding 生效、记忆召回上线、run_verification 全绿 |
| **CP2**（第 4 周末） | M2+M4 完成 | 上帝类拆为 4 模块、旧 API 零破坏、自愈重试上线、禁改区回归全过 |
| **CP3**（第 7 周末） | 阶段一+二收口 | M3/M5/M6/M7/M8 全部合并，新增用例 ≥ 55，手测记录归档 |
| **CP4**（第 11 周末） | M9+M10 主体完成 | 设备契约测试全过、ASR 上屏、worker 真机往返验证、审计报告可查 |
| **CP5**（第 13 周末） | 全量交付 | M11 收尾、总用例 230+、11 份手测记录、运维手册齐全 |
| **CP6**（第 17 周末） | 关系与主动策略 | M13 边界重启后仍生效；M12 主动建议受边界、安静时段与节流控制 |
| **CP7**（第 23 周末） | 主动陪伴交付 | M15-M17 完成；M18 若启用则本地 Demo 验收；总用例 300+、18 份手测记录齐全 |

**风险缓冲**：原版预留第 14 周；扩展版预留第 24 周做全端回归、真机验收与打回修复。任一 CP 未过则后续批次顺延，禁止带伤推进。

---
