## SpiritKinAI 当前项目架构与开发记录

### 1. 项目目标
- 从单体脚本整理为“可跨设备工作的个人智能体集群”
- 当前优先级不是堆很多 agent，而是先把中枢、执行链路、记忆、感知与表达边界理顺

### 2. 当前总架构
- `backend/app/`：运行时装配层
- `backend/orchestrator/`：中枢层，负责规划、会话、调度
- `backend/memory/`：短期记忆与滚动摘要
- `backend/agents/`：专业 agent，目前有 `programming`、`vision`
- `backend/tools/`：工具定义层，负责统一能力语义与注册表
- `backend/knowledge/`：知识摄取、索引、检索骨架
- `backend/perception/`：耳朵/眼睛，音频监听、热词、OCR、屏幕理解、视觉分析
- `backend/expression/`：嘴巴/脸，TTS 与 Live2D/情绪输出
- `backend/action/`：高层动作意图
- `backend/executors/`：执行控制层，把动作请求发给具体执行节点
- `backend/devices/`：设备驱动适配层
- `backend/services/`：剩余服务入口与少量保留 facade
- `backend/tests/unit/`：自动化单测
- `backend/tests/manual/`：人工/硬件验证脚本

### 3. 当前主链路
1. `runtime` 获取语音/视觉输入
2. `AgentCluster.process()` 构建 `AgentContext`
3. `Planner` 决定走：
   - builtin
   - executor
   - agent
   - general
4. `SessionManager` 负责最近历史与摘要注入
5. 最终结果返回 `AgentReply`
6. `expression` 层负责播报与表情输出

### 4. 当前模块边界
#### 4.1 orchestrator 是“大脑控制面”
- `AgentCluster`：统一入口与调度协调
- `Planner`：做路由选择，后续可演进多步计划
- `SessionManager`：管理上下文窗口、摘要、近期历史

#### 4.2 agents 是“会思考的角色”
- `ProgrammingAgent`：偏工程问题分析
- `VisionAgent`：偏屏幕/OCR/视觉理解
- agent 负责判断与推理，不应该直接绑定底层 SDK

#### 4.3 action / executors / devices 三层分工
- `action`：定义“想做什么”
- `executors`：定义“交给谁执行”
- `devices`：定义“底层怎么驱动”

### 5. OpenClaw 当前定位
- 不再只看成“机械臂驱动”
- 现在更适合作为：
  - 机械执行入口
  - 软件/硬件统一执行节点语义
  - 后续多节点控制平面的雏形

当前链路：
- `OpenClawArm`：设备适配器
- `arm_operations`：机械动作意图层
- `OpenClawExecutor`：执行控制层入口

### 6. 当前已经完成的整理记录
#### 第一阶段
- 梳理 backend 结构与入口
- 确立 perception / orchestrator / agents / expression / action / devices 分层
- 增加 planner、session_manager、memory

#### 第二阶段
- 移除 `spiritkin_ai_engine.py`
- 移除语义不一致的 `pedagogy_agent.py`
- 把音频能力迁到 `perception/audio`
- 把语音/表情迁到 `expression`

#### 第三阶段
- 清理 `action` 中纯兼容壳：
  - `speech_synthesizer.py`
  - `emotion_display.py`
  - `pc_operations.py`
- 增加 `executors/`
- 新增 `OpenClawExecutor`

#### 第四阶段
- `Planner` 新增动作型 `ExecutionPlan`
- `AgentCluster` 开始支持 executor 路由
- 新增 `LocalPCExecutor`
- `AgentCluster` 支持按需挂载 `OpenClawExecutor`
- 初步打通“中枢 -> 执行器 -> 设备/节点”闭环

#### 第五阶段（当前）
- 新增 `backend/tools/`，补 `ToolSpec / ToolRegistry / ExecutionTool`
- 新增 `backend/knowledge/`，补摄取、切块、索引、检索最小骨架
- 新增 `RemoteExecutor / NodeRegistry / remote_protocol`
- 为“手机 -> 本地控制面 -> 公司电脑执行节点”预留结构

### 7. 当前仍未完成的关键能力
- 多 agent 协同
- 多步任务拆解
- 工具注册表接入 planner / agent 决策
- 知识库检索接入 agent 工作流
- 长期记忆检索
- 远端节点发现、鉴权与调度
- 执行器超时/重试/取消

### 8. 现在适合怎么继续开发
#### 近期
- 让 planner / agent 开始消费 `tool registry`
- 增加 `kb.search` 与 retrieval agent
- 补远端 worker 心跳、鉴权、状态同步

#### 中期
- 接入知识库（RAG）
- 增加 retrieval agent / retrieval tool
- 把 OpenClaw 扩成多节点执行协议

#### 远期
- 多 agent 协同工作流
- 训练面向本项目的策略/风格模型
- 构建可评估、可回放、可蒸馏的数据闭环

### 9. 目前建议的目录演进
- `backend/tools/`：已新增，定义工具接口与注册表
- `backend/knowledge/`：已新增，知识库索引、检索、摄取骨架
- `backend/evals/`：后续新增，评测集与自动评测脚本
- `docs/`：持续记录架构、开发路线、接入规范

### 10. 当前总判断
- 项目已经不再是单体 agent
- 也还不是成熟的“多智能体集群平台”
- 当前更准确的定位是：
  - 有中枢
  - 有专业 agent
  - 有记忆骨架
  - 有执行控制面雏形
  - 正在向真正集群平台演进