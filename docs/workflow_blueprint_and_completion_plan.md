# 工作流引擎重构方案（类 UE5 蓝图）+ 全项目补完计划——交 GPT 实现

> 状态：方案定稿 2026-07-10，作者 Claude（方案+验收+兜底），实现方 GPT。
> 背景：用户 2026-07-09 反馈工作流"不太能用、不能自由组合"。本方案基于全量代码调查（15 条限制清单，见 §1），目标是把现有 DAG 引擎升级为**可自由组合、可真实执行、可自动推进**的类 UE5 蓝图系统；同时附全项目半成品/空壳盘点与补完优先级（§4），供后续批次排期。

## 0. 现状一句话
引擎骨架（workflow_graph.py 定义/运行态/9 种节点/端口校验）、存储（workflow_store.py 版本+审计）、WPF 蓝图画布（Features/Workflows 29 文件 5693 行：撤销重做/连线/框选/缩放）都是真的；**假的是执行语义**——分支不生效、失败不能重试、参数几乎传不动、没有后台推进、UI 提交的数据写死。修的是语义层，不推倒重来。

## 1. 已查明的 15 条限制（修复目标对照表）

| # | 限制 | 位置 | 本方案处置 |
|---|---|---|---|
| 1 | branch 算出 selected_route 后无人消费，两条分支都执行 | workflow_graph.py:969-991 | W1-1 修 |
| 2 | retry_policy 只序列化不执行；无 retry/reset action | workflow_graph.py:74,88 / workflow_management.py:943-959 | W1-2 修 |
| 3 | 无循环/迭代节点 | SUPPORTED_NODE_TYPES:37-47 | W3-3 加 foreach |
| 4 | 参数只支持整串 `{{...}}` 替换，无内嵌插值 | workflow_graph.py:993-1040 | W1-3 修 |
| 5 | 端口只做连线校验，不是数据管道 | :419-437, 1081-1101 | W1-3 一并接通（端口连线生成参数引用） |
| 6 | 节点类型硬编码白名单，新增要改三处 | :37-52 + WPF 两文件 | W3-1 修 |
| 7 | agent_task 立即 BLOCKED，必须人工闭环 | :582-584 | W2-2 修（接协作 worker） |
| 8 | 无后台调度，auto_advance 靠外部反复调用 | workflow_graph_tools.py:182-252 | W2-1 修 |
| 9 | subgraph 子 run 完成不自动回 signal 父节点 | workflow_graph_tools.py:274-307 | W2-1 顺带修 |
| 10 | UI 提交 outputs 写死 `{submitted_from, at}` | WorkflowRunActions.cs | W4-2 修 |
| 11 | 泳道强制钳制 Y 坐标，不能自由摆放 | WorkflowGraphRules.cs ClampNodeYToLane | W4-1 修 |
| 12 | 连线规则 Python/C# 双份硬编码，易漂移 | :419-437 vs WorkflowConnectionRules | W3-2 修 |
| 13 | 存储整文件读改写，无并发控制 | workflow_store.py 全文件 | W2-3 修 |
| 14 | 旧路径 workflow.execute.auto_listing 绕过图引擎 | control_plane_worker.py:1100+,1256 | W5 收编 |
| 15 | 内置模板 Python 函数硬编码，WPF 只能恢复 2 个 | :1152-1879 / WorkflowDefinitionActions.cs | W3-3 数据化 |

## 2. 批次任务（按依赖顺序交付，每批独立可验收）

### W1：执行语义三补（引擎核心，先做）
1. **branch 生效**：runnable 判定（run_next 的 pending 筛选）增加"祖先 branch 已 SUCCEEDED 且本节点不在其 selected_node_ids 可达集内 → 置 SKIPPED"。新增节点状态 `SKIPPED`（序列化兼容：旧 run 无此状态照常加载）。SKIPPED 节点视作满足下游 depends_on（UE5 语义：分支未选路径整条灰掉）。
2. **retry/reset**：run_node 对 FAILED 节点读 retry_policy（`max_attempts`/`backoff_seconds`，attempts 计入节点运行态）自动重试；action 映射表加 `retry_node`（FAILED→PENDING 重跑）与 `reset_run`（全部非 SUCCEEDED 节点回 PENDING）；治理契约照走权限检查。
3. **参数模板升级**：`_resolve_argument_value` 支持内嵌插值（`"前缀{{node.a.outputs.x}}后缀"` 用正则逐段替换，非整串引用时取值转 str）；支持 `{{input.k}}`/`{{node.<id>.outputs.<path>}}` 两族保持不变，不引表达式引擎（防注入，企业级克制）。端口连线落库时自动在下游节点 arguments 生成对应引用（连线即传数据，端口从"仅校验"升级为"生成数据引用"——UE5 数据引脚语义）。

### W2：自动推进与并发安全
1. **后台推进守护**：命令网关内起单线程守护循环（daemon thread，间隔 `SPIRITKIN_WORKFLOW_ADVANCE_INTERVAL` 默认 5s）调既有 auto_advance_runs；同一循环内做 subgraph 对账（子 run 终态 → 自动 signal 父节点，成功/失败都回传 outputs）。开关 `SPIRITKIN_WORKFLOW_AUTO_ADVANCE=1`（默认开，可关回手动模式）。
2. **agent_task 自动派工（可选实验位）**：BLOCKED 的 agent_task 若 assigned_agent 是协作集群成员，自动 post 协作消息（复用 collaboration post_message 通道），worker 回帖即 complete_agent_task（outputs.reply=回帖正文）。默认关（`SPIRITKIN_WORKFLOW_AGENT_DISPATCH=0`），先跑通 W2-1 再开。
3. **存储加锁**：JsonWorkflowStore 全部读改写包进程内 RLock + 文件锁（Windows 用 msvcrt.locking 或原子替换写：写临时文件+os.replace）。注意 jsonl 热路径教训：**runs.json 不做全量高频重读**，守护循环持有内存态、按需落盘。
4. **失败回喂（对齐执行回路缺口）**：节点 FAILED 时把 error/stderr 摘要写入节点 outputs.error_detail，重试时注入参数 `{{node.<self>.outputs.error_detail}}` 可被提示词引用——为后续"stderr 回喂模型"打底。

### W3：节点开放化（自由组合的地基）
1. **执行器注册表**：SUPPORTED_NODE_TYPES 白名单改为"内置 9 种 + 注册表"：新增 `list_node_catalog` 工具——由 ToolRegistry 全量工具（desktop/feishu/android/browser/git/python/ffmpeg/MCP…）+ skills.jsonl 技能自动生成节点目录（名称/参数 schema/端口默认值），tool_call/skill_call 节点从目录选型即可用，**新工具接入零代码变更**。
2. **规则单源**：后端新增 `GET /desktop/workflows?action=schema` 返回节点目录+类型兼容矩阵+端口 kind 表；WPF `WorkflowConnectionRules`/`WorkflowTemplates`/`WorkflowNodeDefaults` 改为启动时拉 schema 缓存（拉失败降级用现有硬编码，fail-open），双份规则收敛为单源。
3. **模板数据化 + foreach**：4 个 Python 函数模板导出为 `state/workflows/templates/*.json`（数据化，WPF"恢复内置模板"改为列目录全量可选）；新增 `foreach` 节点类型（输入数组 → 逐项展开为子图迭代，串行执行，上限 `max_iterations` 默认 20 防失控）。

### W4：UI 自由化（类 UE5 手感）
1. **去泳道钳制**：ClampNodeYToLane 改为可开关（工具栏"对齐泳道"toggle，默认关=自由摆放；开=辅助对齐而非强制）。既有拖拽/连线/撤销重做/框选全保留。
2. **outputs 编辑表单**：complete_agent_task / signal_node 弹 JSON 编辑框（预填 `{submitted_from, at}` 骨架），提交前 JSON 校验；下游才引用得到真实产出。
3. **节点目录面板**：右键"添加节点"菜单从 W3-2 的 schema 目录生成（按 tool/skill/流程控制分组，带搜索框）；节点参数面板按目录里的参数 schema 渲染输入行（string/number/bool/json 四类）。
4. Web 端 desktop_console.html 工作流 tab 不做图形化（保持 JSON 管理），仅补 retry/reset 按钮。

### W5：旧路径收编
`control_plane_worker.py` 的 `workflow.execute.auto_listing` 保留兼容一版，但内部改为"启动图引擎 ecommerce.auto_listing.v1 run"并注明 deprecated；避免双执行语义漂移。

## 3. 验证与验收（每批必做）
1. 停栈（`powershell -NoProfile -ExecutionPolicy Bypass -File "d:/SpiritKinAI/state/tmp/stop_stack.ps1"`）→ `PYTHONIOENCODING=utf-8 python -X utf8 scripts/run_verification.py --note "工作流Wx：..."`。当前基线 pytest 136 / dotnet 134 / ruff pass / build 0 警告 0 错误，**只增不减**。
2. 新增 pytest（backend/tests/unit/）每批至少：W1—branch 裁剪 SKIPPED/重试计数/内嵌插值/端口连线生成引用；W2—守护循环推进一个双节点 run 到 SUCCEEDED、subgraph 自动回传、并发写不互相覆盖（两线程各写一个 definition）；W3—目录含 MCP 工具、schema 端点返回兼容矩阵、foreach 展开上限；W5—旧 action 转发图引擎。
3. 手测脚本（我验收用）：建"tool_call(echo)→branch→两路 tool_call→汇合 agent_task"图 → 自由摆放节点 → 连线自动生成参数引用 → start_run 后**不点任何按钮**看守护循环推进 → 未选分支变 SKIPPED → 故意让一节点失败 → retry_node 恢复 → complete_agent_task 填真实 outputs → 下游引用到该值。
4. 禁改区：协作 seen/ack 语义与 collaboration_agent_worker 主循环（除 W2-2 的 post_message 调用方）；发言队列 v4；DesktopRenderRuntime 差量同步；批次八~十全部成果；`evaluate_execution_safety` 双重校验不许绕过。

## 4. 全项目半成品/空壳盘点与补完优先级（2026-07-10 调查）

面板层结论：桌面 23 个管理面板全部有真实后端 handler，无纯 UI 空壳；缺口在"最后一公里"。

**空壳**（有入口无实现）：MCP sse/http transport（mcp_adapter.py:242-249 只支持 stdio）；流式 ASR（streaming_listener.py:39 直接 raise）；remote worker 8790 对端执行端（真机侧未落地）。
**半成品**（主链能跑缺关键环节）：Embedding/Reranker 默认 hashing 占位未接真模型；训练执行只出 unsloth 命令文本；audit/replay 报告页依赖的导出文件不存在（页面现成，缺数据管道）；Feishu executor 默认 dry_run；PDD 无障碍 RPA 未真机端到端验证、无失败回报；external_reviewer Agent 注册但 disabled；ecommerce/game_dev/video_animation Agent 仅 persona 无工具链；执行回路（stderr 不回喂/无自动重试）；langgraph/crewai 执行为 CLI 薄包装未验证。
**弃用**（2026-07-10 用户确认，不投入）：live2d.html 及其模型资产链路。
**断头**（保留为手动工具即可）：pdd_extension_storage_audit、demo_lpm_duplex_effect、Blender 3D 脚本群。旧小程序 probe 已删除，PDD 商品数据统一走手机网页链接与浏览器扩展。

### 补完优先级 Top10（商业价值×工作量，供排期）
| # | 事项 | 工作量 | 说明 |
|---|---|---|---|
| 1 | 执行回路：stderr 回喂 + 自动重试 | 中 | 所有 Agent 任务成功率的杠杆；W2-4 已打底 |
| 2 | Embedding/Reranker 接 LM Studio 真模型 | 中 | RAG/记忆召回地基，代码已留配置路径 |
| 3 | audit/replay 报告数据管道自动化 | 小 | 1 天让两个现成页面复活 |
| 4 | 协作 worker 常驻部署 + Feishu 关 dry_run | 小 | 只差部署开关 |
| 5 | 本方案 W1~W4（工作流可用化） | 大 | 用户点名缺口 |
| 6 | remote worker 真机执行端 | 大 | 运维自动化唯一卡点，需硬件联调 |
| 7 | PDD 上架 RPA 真机验证 + 图片处理 + 换IP | 大 | 电商变现直接路径，依赖 #6 部分成果 |
| 8 | 种子 Skill 审核晋升 + external_reviewer 启用 | 小 | 纯配置/运营，激活进化闭环 |
| 9 | MCP sse/http transport | 中 | 打开远程 MCP 生态 |
| 10 | 流式 ASR 后端适配器 | 大 | 语音实时性天花板，可先靠非流式链路过渡，排最后（Live2D 已弃用，从本表移除） |

交付顺序建议：GPT 本轮做 §2 的 W1→W2→W3→W4→W5；Top10 的 #1~#4 小/中项可穿插（#3、#4、#8 各一天内）。多端美术方案另行由 OPUS 执行（docs/multi_client_art_plan.md），互不冲突（文件集不相交）。
