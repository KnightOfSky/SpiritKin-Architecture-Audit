## Tool / Agent / 知识库 / 训练路线详细流程

### 1. 总原则
后续开发不要把所有能力都做成 agent，也不要把底层 SDK 直接塞进 agent。

推荐固定分层：
- `Agent`：负责理解、规划、组合能力
- `Tool`：负责定义标准能力接口
- `Executor`：负责执行请求
- `Device`：负责底层驱动
- `Knowledge`：负责知识摄取、检索与召回

### 2. 什么时候做成 Tool，什么时候做成 Agent
#### 做成 Tool
适合：
- 输入输出稳定
- 行为确定
- 不依赖复杂推理
- 偏查询/执行

例子：
- `pointer.move`
- `screen.capture`
- `ocr.read`
- `arm.home`
- `arm.move_to`
- `kb.search`

#### 做成 Agent
适合：
- 需要策略选择
- 需要多步推理
- 要组合多个 tool
- 要维护复杂上下文

例子：
- `programming_agent`
- `vision_agent`
- 后续的 `retrieval_agent`
- 后续的 `planner_agent`
- 后续的 `executor_agent`

### 3. 后续新增 Tool 的标准流程
#### 第一步：定义工具语义
先定义语义名，而不是实现名。

推荐命名：
- `pointer.move`
- `pointer.click`
- `text.input`
- `arm.home`
- `kb.search`

不要直接暴露：
- `pyautogui_move`
- `sdk_call_openclaw`

#### 第二步：定义统一字段
每个 tool 最好至少有：
- `name`
- `description`
- `target`
- `risk_level`
- `read_only`
- `schema`（参数定义）

#### 第三步：把 tool 转成 `ExecutionRequest`
tool 本身不要直接操作设备，应转换成统一请求：
- `target`
- `operation`
- `params`

#### 第四步：由 executor 执行
本地桌面走 `LocalPCExecutor`
机械动作走 `OpenClawExecutor`
以后远端节点走 `RemoteExecutor`

#### 第五步：补三类测试
- 参数解析测试
- 执行器路由测试
- 失败兜底测试

### 4. 后续新增 Agent 的标准流程
#### 第一步：明确角色职责
每个 agent 必须回答清楚：
- 它负责什么任务
- 它不负责什么任务
- 它主要会调哪些 tool

#### 第二步：实现 `can_handle()`
先做低风险版本：
- 关键词
- 意图规则
- 少量 prompt routing

后面再升级为：
- 语义分类
- 多标签路由
- planner-agent 协调

#### 第三步：实现 `handle()`
要求：
- 输入 `AgentContext`
- 输出 `AgentReply`
- 不直接深耦合底层设备

#### 第四步：为 agent 设计可观察指标
至少记录：
- 命中率
- 成功率
- 常见失败类型
- 是否误路由

### 5. 知识库一定要接，而且优先于重训练
答案是：**要接，而且建议尽早接。**

原因：
- 长上下文不能只靠大 prompt 硬塞
- 项目知识、设备文档、操作手册都更适合做检索
- RAG 的迭代成本比训练低得多

### 6. 知识库推荐怎么做
#### 目录建议
- `backend/knowledge/ingest.py`
- `backend/knowledge/indexer.py`
- `backend/knowledge/retriever.py`
- `backend/knowledge/store.py`
- `backend/knowledge/chunking.py`

#### 第一版知识源
- 项目代码说明
- README / docs
- OpenClaw 接口文档
- 设备操作手册
- 常见故障处理手册
- 开发日志与设计决策

#### 第一版能力
- 文档切块
- 向量索引
- 关键词回退
- Top-K 检索
- 引用来源返回

#### 与 agent 的配合
- planner 发现问题需要知识支持
- 交给 retrieval agent 或 `kb.search` tool
- 检索结果注入 `AgentContext.metadata`
- 再交给专业 agent 生成回答

### 7. 训练模型怎么搞：不要一上来就训大模型
推荐顺序：

#### 阶段 1：先做评测集
先准备你自己的任务评测，不然训练后也不知道有没有变好。

评测集至少分四类：
- 问答类
- 工具调用类
- 多步任务类
- 失败兜底类

#### 阶段 2：先做知识库 + 工具闭环
在很多场景下，RAG + tool routing 就能解决 60%~80% 的问题，没必要立刻微调。

#### 阶段 3：收集轨迹数据
收集：
- 用户输入
- 规划结果
- 调用的 tool
- 执行结果
- 最终回复
- 人工是否认可

这些数据以后既能做分析，也能做训练。

#### 阶段 4：做 SFT 微调
优先做：
- 风格统一
- 路由更稳
- 工具选择更准
- 项目专有术语理解更好

推荐先走 LoRA / QLoRA，不建议自己从零训练基础模型。

#### 阶段 5：再考虑偏好优化
当你已经有：
- 稳定任务定义
- 清楚的成功标准
- 足够多对比样本

再考虑：
- DPO
- RLAIF
- 拒答/安全策略优化

### 8. 训练集怎么准备
#### 数据来源
- 真实多轮对话日志
- agent/tool 执行轨迹
- 常见故障排查样本
- OpenClaw / 设备操作样本
- 代码库问答样本
- 人工编写的高质量示范数据

#### 数据结构建议
至少包含：
- `task_id`
- `user_input`
- `context`
- `plan`
- `tool_calls`
- `tool_results`
- `final_answer`
- `label` / `score`

#### 数据质量原则
- 少而准，好过多而杂
- 先覆盖高频任务
- 明确保留失败案例
- 保留“为什么失败”的标签

### 9. 当前最推荐的开发顺序
1. 完善 executor 路由
2. 建立 `backend/tools/` 与注册表
3. 接入知识库（RAG）
4. 增加 retrieval agent / retrieval tool
5. 建立 evals
6. 收集真实轨迹数据
7. 再做 LoRA / SFT 微调
8. 最后再做多节点总线（B 阶段）

### 10. 你这个项目的现实建议
如果目标是把 SpiritKinAI 做成长期可扩展的智能体集群，正确顺序应是：

- 先把中枢、工具、执行器边界立住
- 再接知识库
- 再形成数据闭环
- 最后再做训练与多节点平台化

一句话总结：
**先把系统做对，再把模型训强。**