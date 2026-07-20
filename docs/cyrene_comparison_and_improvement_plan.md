# Cyrene-Agent 对比与 SpiritKinAI 改进基线

状态：2026-07-17 已核验，作为 M1-M18 后续工作的当前基线。

## 1. Cyrene-Agent 源码核验

| 项目 | 核验结果 | 可借鉴点 |
| --- | --- | --- |
| 两阶段 FC | 成立。`TOOL_PHASE` 注入工具定义并执行调用，`SOUL_PHASE` 不携带工具定义，只生成最终人格回复。 | SpiritKin 应继续把执行编排与人格回复从 `AgentCluster` 拆成明确阶段。 |
| DMAE | 成立。Worldbook 维护 activation、用户/模型沉默计数和 Active/Dormant/Archived 状态，并按内在价值保护重要记忆。 | 记忆需要“召回 + 激活 + 衰减 + 状态”，不能只有持久化。 |
| 向量贴纸匹配 | 成立。BGE-M3 路径为 1024 维，支持预计算缓存和实时余弦相似度。贴纸总数是内容库存，不应写成稳定架构常量。 | 语义表情/动作匹配可复用同类 embedding 服务，但不应为 Avatar 单独复制一套模型运行时。 |
| ToolRegistry | 成立。内置工具和 MCP 工具进入统一 `ToolDefinition`，包含 schema、risk、context 和 execute。 | SpiritKin 现有 `ToolSpec/ToolRegistry` 方向一致，应收敛重复策略而不是重做注册表。 |
| MCP transport | “三种 transport”不成立。当前适配器配置和实现只包含 `stdio` 与 `sse`。 | SpiritKin 的配置层已有 `stdio/sse/http`，真正缺口是把 SSE/HTTP 执行路径做实。 |
| 轻量依赖 | 运行时依赖约 25 个，可以称为相对轻量；总依赖还包含开发依赖。 | 继续控制核心依赖，但不要只用包数量衡量运行时复杂度。 |
| BGE-M3 570 MB | 本次未从仓库源码确认固定文件大小。 | 模型尺寸应由实际发布制品清单和 hash 记录，不写死在架构判断里。 |

源码依据：

- [two-phase-fc-loop.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/orchestrator/two-phase-fc-loop.ts)
- [worldbook.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/rag/worldbook.ts)
- [embedding.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/rag/embedding.ts)
- [sticker-embedder.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/rag/sticker-embedder.ts)
- [tool-registry.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/orchestrator/tool-registry.ts)
- [mcp-adapter.ts](https://github.com/Playa-0v0/Cyrene-Agent/blob/master/src/main/orchestrator/mcp-adapter.ts)

## 2. SpiritKinAI 痛点复核

| 原判断 | 复核 | 当前结论 |
| --- | --- | --- |
| `agent_cluster.py` 是上帝类 | **主体拆分已完成，仍需继续瘦身** | 模型路由、执行循环、人格/计划/目标回复和受管 Agent 名册已拆出；正式构造签名由 42 个显式参数收敛为 4 个，并由 `AgentClusterWiring` 组装。Cluster 已由 2301 行降至 1055 行，剩余主要债务是构造器装配和显式门面转发。 |
| 6 处白名单硬编码 | 部分过时 | Agent/Skill allowlist 是配置字段，工具已有统一 registry 和 safety gate。仍需消除跨模块重复策略，但不是“从零动态化”。 |
| broad `except Exception` 多 | 成立 | `AgentCluster` 内仍有多处异常吞噬或无结构降级。执行事件包装器已先补齐异常终态；其余按模块逐步收敛。 |
| orchestrator 与 app 循环依赖 | **本轮已修复** | orchestration 目录内 `backend.app` 顶层及 deferred imports 均已清零；文件装配和 Collaboration task 写回由 app wrapper/port 注入。 |
| 长时记忆只有存储无召回 | 表述不准确、结果成立 | `long_term.py` 原本已有关键词 `recall`，但请求主链未调用，且中文无空格文本匹配差。本轮已接入主链并增加激活状态。 |
| `knowledge/retriever.py` 17 行占位 | 不成立 | 它是 facade；实际已有 embedding retriever、vector store、reranker、ingest/index。缺口应按效果、降级和运维验证，不按 facade 行数判断。 |
| Avatar idle 完全静止 | **已关闭** | `avatar_3d.html` 已有 `idleLife()`、idle wander 和动作队列；回复语义现由共享 embedding 匹配 emotion/action，失败时走可追踪关键词降级。Bangboo GLB 的真实骨骼动作与桌面/移动画布像素已验收。 |
| 自愈能力缺失 | 已过时 | 已有 retry、failure log、服务健康、token banner、Worker 失败分类。仍需把 broad exception 统一成可观察的降级策略。 |

## 3. M1-M8 修订状态

| 批次 | 状态 | 下一验收点 |
| --- | --- | --- |
| M1 记忆系统语义化 | **M1-A/M1-B 已完成主体** | 主请求已召回长时记忆；支持中文字符/bigram、activation 三态与结构化降级。知识库、记忆、reranker、Avatar 已共用进程级 EmbeddingService；真实 LM Studio 基线保留原始 embedding 指标，并由 Qwen reranker 将最终 Recall@1/Recall@3/MRR 提升至 1.0。 |
| M2 上帝类拆分 | **M2-A/M2-B 已完成主体** | 已抽 `ModelCallCoordinator`、`ExecutionPhase`、不持有工具/执行依赖的 `SoulResponsePhase` 和 `AgentRoster`；计划/目标回复也进入 Soul phase。正式构造签名从 42 个显式参数降为 4 个，旧参数由兼容 wiring 接收。文件从本轮开始时 2301 行降至 1055 行。 |
| M3 白名单动态化 | 基线已存在 | 统一 Tool/Skill/MCP 的 risk vocabulary、owner scope 和 capability policy，删除重复判断。 |
| M4 执行回路自愈 | 基线已存在，本轮增强 | 命令执行包装器现在无论成功、失败、等待确认、无 execution payload 或抛异常，都有开始和终态事件。后续统一错误分类与重试预算。 |
| M5 Avatar 陪伴感 | **已完成** | 复用统一 embedding 服务选择 emotion/action；显式回复协议优先，provider 不可用时走版本化关键词降级；事件携带不含原文的 reaction trace。前端支持 full/reduced/static，static 保留状态与表情但抑制身体动作。 |
| M6 循环依赖反转 | **已完成** | `agent_cluster.py` 使用 `AgentClusterAppPort`；context mirror 文件装配移回 app；workflow finalizer 使用显式 Collaboration task port。层级测试要求整个 orchestration 目录对 `backend.app` 零导入。 |
| M7 MCP 生态增强 | **已完成** | `stdio` 保持兼容；Streamable HTTP 与 legacy HTTP+SSE 均支持 initialize、动态发现、调用、超时、有限重试/重连和运行审计。远程敏感 header 必须通过 `header_env` 注入。 |
| M8 技能扩展自动化 | 大部分基线已存在 | 已有 Skill store/source 扫描、声明式加载、lock、promotion/review。下一步统一目录覆盖优先级与冲突报告。 |

## 4. 本轮已落地

1. 会话 composer 只展示自动主路由和真实、启用、已配置模型，删除假 API cloud presets。
2. 快速会话和正式会话都有模型与 reasoning 选择。
3. 后端重新校验模型 ID，从真实配置解析 provider/model/endpoint/key；密钥不进入会话 metadata。
4. 主 Spirit 模型调用进入 Agent/模型调用卡，显示真实 provider/model。
5. 命令执行包装器强制产生前后事件，并覆盖异常与无 execution metadata 的返回。
6. 长时记忆接入主请求上下文，加入中文匹配、激活度和结构化降级。
7. 模型路由从上帝类抽到 `ModelCallCoordinator`，主模型 override 不再污染专业 Agent 模型策略。
8. 最终人格、计划和目标回复抽到 `SoulResponsePhase`；该阶段不持有 ToolRegistry、SkillRegistry 或 Executor，工具 schema 不会进入人格 prompt。
9. confirmation、policy、Worker 执行、模型重试和结果装配抽到 `ExecutionPhase`。
10. orchestration 的 app 反向依赖全部改为注入端口或 app wrapper，层级 allowlist 已清空。
11. `AgentClusterWiring` 将正式构造签名从 42 个显式参数收敛为 4 个，同时保留旧调用兼容。
12. Avatar 回复链新增版本化语义 reaction library、共享 embedding 匹配、关键词降级与 trace；Bangboo/Atelier 原表现保留，并补齐 reduced/static 动效偏好。
13. MCP 远程执行新增 Streamable HTTP 与 legacy SSE 的真实发现/调用路径，覆盖 JSON/SSE 响应、session/protocol header、超时、重试/重连、审计和 secret header policy。
14. 受管 Agent 名册从 `AgentCluster` 抽到 `AgentRoster`，集中处理 profile、启停/优先级、mention 路由记录和 adapter 构建；门面文件进一步降至 1055 行。

## 5. M2/M6 实施顺序

1. 为现有 `AgentCluster.process()` 建立 characterization tests，冻结外部行为。
2. ~~抽出模型调用协调器，拥有模型选择、reasoning 和 route 生命周期。~~ 已完成。
3. ~~抽出执行阶段，拥有 confirmation、policy、retry 和结果装配。~~ 已完成。
4. ~~抽出回复阶段，工具定义不进入最终人格 prompt。~~ 普通人格、计划和目标回复均已完成。
5. ~~用 Protocol/app wrapper 清除 orchestrator 对 `backend.app.*` 的反向依赖。~~ 已完成，层级测试的 deferred allowlist 已为空。
6. ~~用 wiring dataclass 收拢构造参数。~~ 已完成；后续继续下沉构造器装配和显式门面转发，不一次性重写剩余 1055 行。

## 6. 2026-07-17 最新版差距复核

本轮重新核验 `Playa-0v0/Cyrene-Agent` 的 `master` 提交
`fc58408a314c42e40ed8827751847944edcde159`。Cyrene 已在早期基线之外加入多会话、
飞书/微信渠道、邮件与文档工具、L0/L1/L2 记忆冲突消解、关系画像、主动投递、
调度、语音通话、音乐和游戏 Bot。

| 最新能力 | SpiritKinAI 当前状态 | 结论 |
| --- | --- | --- |
| 多会话与项目会话 | 已有持久化 session/project、归档与跨项目移动 | 不重复实现 |
| 关系、主动交互、调度、开场气泡 | 已接运行时、事件和策略边界 | 定向测试通过 |
| 语音通话、音乐、游戏自动化 | 已有后端/桌面主链与安全门控 | 定向测试通过；真机/长时手测仍需继续 |
| 记忆证据、冲突与审计 | **本轮补齐后端闭环** | 弱规则只建 `pending_review`；显式消解后才归档旧记忆 |
| 微信 iLink Bot | 已补 Python iLink HTTP 客户端、`getupdates` 长轮询、`sendmessage` 回传和会话游标；默认关闭，认证/过期可观测 | 已补齐，需真实凭据做外部联调 |
| 邮件工具 | 已接入统一 `ToolRegistry` 的 `email.send`，支持 SMTP、抄送、HTML、工作区附件和显式确认 | 已补齐，需真实 SMTP 做外部联调 |
| 记忆冲突复核 UI | 已有受认证 `/desktop/memory` API；WPF 提供列表、证据对比、来源/归属、处置与审计视图 | 已补齐 |
| 独立桌面宠物窗口 | 当前是工作台内嵌 3D Avatar | P2 产品形态选择，不阻塞 Agent 主链 |

### 本轮记忆闭环

- 新增证据元数据：`source`、`attribution`、`evidence_quotes`。
- 新增同主题正反语义冲突候选；本地规则不自动覆盖用户画像。
- 新增五种显式处置：采用新记忆、采用旧记忆、上下文并存、驳回、请求澄清。
- 采用新/旧记忆属于破坏性处置，必须填写理由；已关闭冲突不能反向重写。
- 被取代条目 activation 归零并标记 archived，不再进入人格回复 prompt。
- 冲突记录独立写入 `*.conflicts.jsonl`，重启后保留候选、处置和取代链。
- 新增记忆审计：缺失证据、绝对化过度概括、未决冲突、可召回的已取代条目、断裂处置链。
- 运行时快照暴露冲突与审计摘要；受认证 `GET/POST /desktop/memory` 支持查询和人工消解。

### 后续顺序

1. 为 iLink 增加真实账号扫码/凭据管理与媒体收发联调；当前文本双向链路已完成，默认保持关闭。
2. 邮件能力继续接入真实 SMTP/失败重试观测；工具已按高风险网络能力进入统一 ToolRegistry、授权和审计，发送仍要求本次显式确认。
3. 独立桌面宠物窗口按产品决定不实施；保留 WPF 工作台内嵌 Avatar。
