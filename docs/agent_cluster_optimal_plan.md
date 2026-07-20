# SpiritKinAI Agent 集群性能最优方案

本文是 Agent 集群后续开发的主计划。目标不是把所有框架混在一起，而是形成一个统一安全内核，允许每个专业 Agent 使用最合适的模型和 Agent 框架。

## 1. 核心结论

采用 `SpiritKin Runtime / AgentCluster` 作为全局总控。

总控分两层：

- 安全内核：确定性代码，负责权限、确认门、审计、工具白名单、pending 状态、预算、超时、回退和最终执行许可。
- 协调 LLM：只负责 route、plan、结果汇总和澄清问题，不直接执行高风险动作。

专业 Agent 使用 `AgentAdapter` 统一协议接入，可以混搭：

- native SpiritKin Agent
- LangGraph 子图
- CrewAI 团队
- Codex / Claude Code CLI
- MCP 工具服务
- 远端 worker
- GUI 自动化 Agent，如 AutoGLM / LangManus 类能力

所有真实工具执行必须回到：

```text
ToolRegistry -> ExecutionGuard -> Executor -> Device/Connector
```

LLM 或外部 Agent 不能绕过这个链路。

## 2. 目标架构

```text
User / Voice / Desktop / Mobile / Web
  -> Runtime
     -> Security Kernel
        - auth / permissions
        - confirmation gate
        - audit
        - context and session state
        - budget and timeout
     -> Coordinator Router LLM
        - route JSON
        - plan JSON
        - clarification
        - synthesis
     -> AgentCluster
        -> AgentAdapter
           -> Native Agent
           -> LangGraph subgraph
           -> CrewAI team
           -> Codex/CLI reviewer
           -> MCP or remote worker
        -> ToolRegistry
        -> ExecutionGuard
        -> Executor
        -> Workflow Memory / Audit / Events / Avatar
```

## 3. AgentAdapter 合同

所有 Agent 不论内部框架，都必须遵守统一输入输出。

输入：

```json
{
  "task": "...",
  "session_context": {},
  "memory_context": {},
  "allowed_tools": [],
  "risk_policy": {},
  "budget": {},
  "attachments": []
}
```

输出：

```json
{
  "text": "...",
  "plan": [],
  "tool_calls": [],
  "result": {},
  "confidence": 0.0,
  "requires_confirmation": false,
  "metadata": {},
  "events": []
}
```

硬规则：

- `requires_confirmation` 只是建议，最终由 `ExecutionGuard` 重算。
- `tool_calls` 必须命中 `ToolRegistry`。
- 文件、剪贴板、窗口关闭、外部消息、设备动作、知识库覆盖写入等高风险动作必须确认。
- AgentAdapter 可以失败，但失败必须返回结构化错误，供 Repair Advisor 和 SelfImprovementLoop 使用。

## 4. 推荐模型与框架分工

| Agent | 首选模型 | 内部框架 | 作用 |
| --- | --- | --- | --- |
| Coordinator / Router | 云端长上下文优先 Claude/Gemini/GPT/Kimi/GLM/DeepSeek；本地可选 Llama/Mistral/Qwen | native structured planner | 全局 route、plan、澄清、汇总 |
| Programming Agent | Claude/GPT/Codex/DeepSeek/Kimi 作强评审；本地可选 Qwen-Coder/Llama/Mistral Coder | Codex/CLI adapter + native fallback | 代码修改、测试、调试、PR 评审 |
| Vision Agent | Gemini/GPT/Claude VLM；本地可选 Qwen-VL | native VLM adapter | 屏幕、图片、视频帧理解 |
| Ecommerce Agent | 长上下文云模型 + 电商知识库；本地 Qwen/Llama/Mistral 只作离线备选 | native RAG agent | 商品、投放、运营、复盘 |
| Game Agent | Claude/GPT/Kimi/GLM + coder 模型组合 | CrewAI 或 native | 玩法、UI、脚本、测试分工 |
| Video/Animation Agent | Gemini/Claude/GPT VLM + 长上下文文本模型 | LangGraph candidate | 分镜、素材、时间线、多步状态机 |
| Skill Runner | 不需要 LLM | native deterministic runner | 执行已验证 Skill |
| Reviewer | DeepSeek Reasoner / GPT / Claude / Gemini / Qwen3.7-Max API | remote/API reviewer | 裁判、修复建议、训练样本审核 |

注意：

- Qwen3.7-Max 属于云端 API 候选，不作为本地 Hugging Face 基底模型假定。
- Qwen 系列只保留为混合矩阵的一组候选；如果实测上下文不足，应放到低延迟/离线备选位，而不是主控唯一模型。
- “性能最优”不是单模型最强，而是长上下文主控、专业模型、知识库、Skill、评审模型的组合。

当前代码已补：

- `ManagedAgentConfig.framework`
- `ManagedAgentConfig.adapter`
- `build_managed_agent_runtime_snapshot().adapter_contract`
- Runtime capabilities 中的 `model_catalog`

## 5. 外部框架落地边界

### Symphony / OpenAI Agents SDK

公开可直接落地的是 OpenAI Agents SDK 风格能力：工具、handoff、trace。`Symphony` 若是内部或非公开工程流名称，不作为项目硬依赖。可吸收它的思想：任务系统作为控制面，PR、CI、Review、Audit 形成闭环。

项目落地方式：

- 把任务系统能力做成 `Project/Task/Review` 控制面。
- 把 PR/CI/Review 作为 Tool/Skill。
- 使用 Codex/CLI 或远端 reviewer 做工程评审。

### OpenClaw

OpenClaw 是执行框架，不是基底模型。它只能处在 worker/executor 层。

落地方式：

- OpenClaw 动作继续走 `ToolRegistry -> ExecutionGuard -> OpenClawExecutor`。
- 高风险机械动作继续强制确认。
- OpenClaw 状态进入 memory、audit 和 event bridge。

### LangGraph

适合复杂状态图、断点恢复、长任务子图、自我进化实验。

优先接入场景：

- 视频/动画时间线流程
- Paper-to-Skill 实验流程
- Video-to-Skills 提取流程
- 多步训练数据构建流程

边界：

- LangGraph 只能是某个 AgentAdapter 内部实现，不能替代全局安全内核。

### CrewAI

适合角色分工明显、快速搭团队的场景。

优先接入场景：

- 游戏策划/开发/测试小队
- 电商选品/文案/投放/复盘小队

边界：

- CrewAI 输出必须回到 AgentAdapter 合同。
- CrewAI 内部工具也必须映射到 SpiritKin ToolRegistry。

### AutoGLM / LangManus

定位为 GUI 自动化 Agent 或参考实现，不作为全局执行权限源。

落地方式：

- 作为 `gui_agent_adapter` 或远端 worker。
- 只能调用白名单 UI 操作。
- 屏幕点击、输入、窗口切换等动作进入 `screen/window/input` 原子操作。

### Paper2Agent / Agent Laboratory

定位为“读论文转技能/实验”的候选能力，不直接自动上线。

落地流程：

```text
论文 -> 提取方法/算法/工具边界 -> 生成 Skill 草稿 -> mock/replay 验证 -> 人审 -> 入 Skill Registry
```

### Video-to-Skills

定位为“看操作视频提取 UI 流程”的候选能力。

落地流程：

```text
视频 -> VLM 抽帧理解 -> 操作序列 -> UI 元素映射 -> Skill 草稿 -> dry-run -> 人审 -> 正式 Skill
```

## 6. 自我进化闭环

目标闭环：

```text
任务轨迹
  -> 规则/裁判模型打分
  -> 高分轨迹沉淀为 Skill
  -> 低分轨迹进入 Failure DB
  -> 生成 eval cases
  -> 导出训练集
  -> LoRA/QLoRA 微调 Router 或专业 Agent
  -> 回归测试
  -> 注册新 adapter/model
```

当前项目已有：

- Workflow Memory
- Failure DB / trajectory
- SelfImprovementLoop
- Training dataset builder
- Unsloth LoRA/QLoRA trainer
- Cloud training package export

### 6.1 知识库自动进化

```text
运行轨迹 / 用户纠错 / 文档导入
  -> 候选知识片段
  -> 去重、过期检测、来源标记
  -> 写入 Agent/domain/global 知识库
  -> RAG 检索回归测试
  -> 通过后成为默认上下文
```

落地边界：

- 知识库写入属于高风险或中风险动作，覆盖/删除必须确认。
- 每条知识必须保留来源、时间、适用 Agent、过期策略。
- 不允许 LLM 直接改生产知识库；只能生成候选，由规则或人审批准。

### 6.2 基于 Skills 的 Agent 进化

```text
高频成功操作轨迹
  -> Skill 草稿
  -> dry-run / replay harness
  -> 风险分级和工具白名单
  -> 人审或阈值 gate
  -> active Skill
```

Agent 的“进化”优先表现为 Skill 库增强，而不是频繁微调模型。

### 6.3 多 Agent 拆分方式

采用两种拆分同时存在：

- 基于角色：Coordinator、Planner、Specialist、Reviewer、Executor、Memory Curator。
- 基于上下文：项目上下文、设备上下文、知识库上下文、会话上下文、权限上下文。

角色拆分决定“谁负责”；上下文拆分决定“拿哪些资料、允许哪些工具、需要哪些确认”。

### 6.4 Harness 自动进化

Harness 不自动上线能力，只自动生成候选和证据：

```text
失败轨迹 / 成功轨迹
  -> replay case
  -> 规则评测 + 裁判模型评测
  -> diff 报告
  -> Skill/知识/LoRA 候选
  -> 人审或 gate
```

缺口：

- LangGraph/CrewAI/Codex adapter 实装
- Skill 候选自动 replay 和升权 UI
- 模型/LoRA adapter 注册入口
- 云训练结果回传和评估 gate

## 7. 迭代计划

### P0：稳定当前可用闭环

- 当前时间上下文注入，避免模型回答训练截止日期。
- 桌面端模型目录可联网刷新到本地 `state/model_catalog.json`。
- 云训练包可导出，用户手动上传 GPU 主机训练。
- Agent 管理状态暴露 `framework/adapter`。

### P1：AgentAdapter 抽象

- 已新增 `backend/orchestrator/agent_adapters.py`。
- 已实现 `NativeAgentAdapter`。
- 已将现有专业 Agent 经 adapter 入口运行。
- 已在 AgentRuntime metadata 暴露 `control_plane`、`framework`、`adapter`。
- 下一步：为 LangGraph/CrewAI/Codex/remote worker 增加具体 adapter 实现。

### P2：工程 Agent

- 接入 Codex/CLI reviewer adapter。
- 增加 PR/CI/测试命令 Skill。
- 失败结果进入 Failure DB 和训练样本。

### P3：LangGraph / CrewAI 子系统

- LangGraph 先接视频/论文/训练工作流。
- CrewAI 先接游戏或电商团队。
- 子系统输出必须经 AgentAdapter 合同。

### P4：Paper/Video-to-Skills

- 论文导入生成 Skill 草稿。
- 视频抽帧生成 UI 操作草稿。
- Harness dry-run、人审、正式入库。

### P5：云训练闭环

- 桌面端一键导出云训练包。
- 云端训练完成后下载 LoRA adapter。
- 本地注册 adapter，跑 eval gate，通过后启用。
