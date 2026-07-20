## SpiritKinAI 代码地图（当前版）

### 1. 先看启动链路
项目主入口非常薄：

1. `backend/main.py`
   - 只负责导入并调用 runtime 的 `main()`。
2. `backend/app/runtime.py`
   - 创建 `SpiritKinRuntime`
   - 默认装配 `AgentCluster()`
   - 加载语音、热词、视觉、TTS、表情依赖
   - 进入“热词唤醒 -> 收音 -> 路由 -> 回复 -> 播报”的主循环
3. `backend/orchestrator/agent_cluster.py`
   - 真正的中枢入口，负责调 planner / agent / tool / executor

### 2. 核心文件作用（按重要度优先）

#### 2.1 主入口与运行时
- `backend/main.py`
  - 项目最外层入口
  - 正常运行时执行 `python -m backend.main` 就会到这里
- `backend/app/runtime.py`
  - 当前“可运行产品形态”的装配层
  - 负责把耳朵、眼睛、嘴巴、脸和中枢串起来
  - 当前是回合式语音交互，不是全双工持续对话

#### 2.2 中枢层（最关键）
- `backend/orchestrator/agent_cluster.py`
  - 当前第一版智能体集群协调器
  - 输入用户请求，构建上下文，交给 planner 决策，再路由到 builtin / tool / executor / agent / general
  - 现在默认挂了 `VisionAgent`、`ProgrammingAgent`、`EcommerceAgent`
- `backend/orchestrator/planner.py`
  - 当前第一版规划器
  - 负责决定这次请求该走哪条路
  - 目前以规则/关键词/轻量意图判断为主，后续可升级多步规划
- `backend/orchestrator/session_manager.py`
  - 负责把短期记忆整理成 `AgentContext`
  - 会注入近期历史、会话摘要、额外 metadata（如知识命中）

#### 2.3 Agent 协议与专业 agent
- `backend/agents/base.py`
  - 定义 `AgentContext`、`AgentReply`、`BaseAgent`
  - 是所有 agent 的共同协议文件
- `backend/agents/programming_agent.py`
  - 处理编程、报错、代码排查类问题
- `backend/agents/ecommerce_agent.py`
  - 处理商品、运营、客服、投放、转化等电商问题
- `backend/agents/vision_agent.py`
  - 处理视觉/OCR/屏幕理解相关问题
- `backend/agents/__init__.py`
  - agent 导出入口

#### 2.4 记忆层
- `backend/memory/short_term.py`
  - 保存最近若干轮对话
- `backend/memory/summarizer.py`
  - 把较早对话压成短摘要，避免 prompt 爆掉

#### 2.5 Tool 层
- `backend/tools/base.py`
  - 定义 `ToolSpec`、`ToolCall`、`ToolResult`、`ExecutionTool`
  - 这是“能力语义层”，不是设备驱动层
- `backend/tools/registry.py`
  - 工具注册中心
  - `build_default_tool_registry()` 会把桌面工具、机械臂工具、知识工具挂起来
- `backend/tools/desktop_tools.py`
  - 本地桌面语义工具，如鼠标、点击、输入、按键
- `backend/tools/openclaw_tools.py`
  - 机械臂 / 夹爪语义工具
- `backend/tools/knowledge_tools.py`
  - `kb.search` 工具定义

#### 2.6 Knowledge 层
- `backend/knowledge/base.py`
  - 定义文档、切块、检索命中等基础数据结构
- `backend/knowledge/ingest.py`
  - 负责把文本文档切块并入库
- `backend/knowledge/loader.py`
  - 负责从文件/目录批量 ingest
  - 也提供从项目 `docs/` 一步构建 retriever 的 helper
- `backend/knowledge/store.py`
  - 当前轻量知识库存储与检索实现
  - 现在还是内存版轻检索，不是正式向量库
- `backend/knowledge/retriever.py`
  - 对外统一的检索器包装
- `backend/knowledge/indexer.py`
  - 当前最小索引骨架，后续适合升级向量索引
- `backend/knowledge/registry.py`
  - 多 store / retriever 注册表骨架

#### 2.7 执行层
- `backend/executors/base.py`
  - 定义 `ExecutionRequest` / `ExecutionResult` / `BaseExecutor`
- `backend/executors/local_pc_executor.py`
  - 执行本地 PC 鼠标、键盘、输入类操作
- `backend/executors/openclaw_executor.py`
  - 执行机械臂相关动作
- `backend/executors/remote_executor.py`
  - 远程节点执行骨架
- `backend/executors/node_registry.py`
  - 节点注册与查找
- `backend/executors/remote_protocol.py`
  - 远端控制协议对象

#### 2.8 动作 / 设备层
- `backend/action/arm_operations.py`
  - 机械动作意图封装
- `backend/action/device_actions.py`
  - 面向设备的高层动作入口
- `backend/devices/base.py`
  - 设备后端协议
- `backend/devices/local_pc.py`
  - 本地 PC 设备适配
- `backend/devices/openclaw.py`
  - OpenClaw 设备适配
- `backend/devices/registry.py`
  - 设备后端注册与选择

#### 2.9 感知 / 表达 / 服务层
- `backend/perception/audio/*`
  - 麦克风、热词、监听
- `backend/perception/vision_analyzer.py`
  - 视觉分析入口
- `backend/expression/speech.py`
  - TTS 播报
- `backend/expression/avatar.py`
  - 情绪 / Live2D 风格表情触发
- `backend/services/conversation_engine.py`
  - 默认 LLM 调用入口
- `backend/services/openclaw.py`
  - 对外的 OpenClaw 服务入口

#### 2.10 测试层
- `backend/tests/unit/`
  - 自动化单元测试
- `backend/tests/manual/`
  - 人工 / 硬件链路验证脚本

### 3. 目前你最该先读的文件顺序
如果你想快速理解项目，建议按这个顺序看：

1. `README.md`
2. `docs/project_architecture_and_dev_log.md`
3. `backend/main.py`
4. `backend/app/runtime.py`
5. `backend/orchestrator/agent_cluster.py`
6. `backend/orchestrator/planner.py`
7. `backend/agents/base.py`
8. `backend/tools/base.py`
9. `backend/knowledge/loader.py`
10. `backend/executors/base.py`

### 4. 当前要特别注意的事实
- 当前主入口会启动“本地语音交互 + 中枢处理”这条主链路
- 但不会自动启动未来所有子系统，例如：远端 worker、向量数据库、多 agent 协同工作流
- `kb.search` 已接入工作流，但底层目前还是轻量检索
- 项目已经是“有中枢的系统”，但还不是完整成熟的平台

### 5. 这份地图怎么用
- 看不懂一个文件时，先看它属于哪一层
- 再问它回答的是哪一个问题：
  - 理解？`agents`
  - 路由？`orchestrator`
  - 能力语义？`tools`
  - 知识检索？`knowledge`
  - 真正执行？`executors`
  - 底层驱动？`devices`

后续如果目录继续扩展，建议持续维护这份文件，而不是只靠 README。