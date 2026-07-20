# 协作修复与项目维护方案（交 GPT 实现，Claude 验收）

> 2026-07-06 由 Claude 起草。分工：GPT 按本方案实现，Claude 负责验收与兜底。
> 实现前先读"禁改区"一节。所有落点 file:line 均已在当前代码核实。

---

## A. P0 修复项

### A1. 被吞回复三件套（轮次护栏可见化 + 自动续轮）

**现象**：辩论进行中 DS/Spirit 的回复凭空消失（卡片 Completed 但无气泡）。
**根因（已确证，勿重复排查）**：每 thread 模型互聊上限 6 次（`SPIRITKIN_COLLABORATION_TURN_CAP`，
[backend/app/collaboration_turn_guard.py:35](../backend/app/collaboration_turn_guard.py)）。用满后：
- 后端 [backend/app/collaboration.py:496-508](../backend/app/collaboration.py) 抛 `turn_cap_reached` 拒收；
- worker [scripts/collaboration_agent_worker.py:649-656](../scripts/collaboration_agent_worker.py) 捕获后**静默丢弃**（仅 stdout 日志）;
- 桌面无任何 refill / turn_guard 引用，用户毫无感知。

网关已有现成 action（无需新增后端逻辑）：
- `refill_turns` / `turn_refill`：[collaboration.py:1804](../backend/app/collaboration.py) → `refill_collaboration_turns(payload)`（payload: `thread_id`、可选 `additional`、`actor`）；
- `turn_guard_status` / `turn_status`：[collaboration.py:1808](../backend/app/collaboration.py) 返回余额快照。

**改动 1 —— 桌面发消息自动续轮**（`desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs`）
- 在 `SendCollaborationMessageFromComposerAsync`（[:1307](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）确定走协作发送路径后、post_message 之前，
  调网关 `{"action":"refill_turns","thread_id":<当前会话 thread>,"actor":"human_desktop"}`。
- 语义依据：护栏报错原文就是 "a human must refill"——人类发新消息即为 refill 的天然时机。
- 失败不阻塞发送（try/catch 吞掉，仅状态栏提示）。

**改动 2 —— worker 被拒时发 lifecycle 事件而非只打日志**（`scripts/collaboration_agent_worker.py`）
- `post_reply` 的拒收分支（:649-656）：`return` 前调 `record_worker_event`（[:1237](../scripts/collaboration_agent_worker.py)，注意 transport!=route_bus 时它直接 return，保持该行为），
  事件 lifecycle 取 `turn_cap_reached` 或 `auto_reply_disabled`。
- 桌面 `LocalizeCollaborationLifecycle`（[CollaborationPanel.cs:515](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）加两行中文：
  - `turn_cap_reached` → `双工轮次已达上限，发送一条新消息可继续`
  - `auto_reply_disabled` → `模型互聊开关已关闭，本条回复未投递`
- 参考现有写法 `:534` `turn_wait`。

**改动 3 —— 面板显示轮次余额 + 上限可调**（`desktop/SpiritKinDesktop/Controls/ManagementPanelsView.xaml` 协作区）
- 加一行只读文本 `CollaborationTurnGuardText`（"双工余额 n/cap"），在协作面板刷新时调 `turn_guard_status` 回填；
- 加一个数字输入（上限），保存时写 env 或调用后端（后端 cap 目前只读 env `SPIRITKIN_COLLABORATION_TURN_CAP`；
  最小实现：桌面写进程环境 + 提示重启 worker 生效即可，不必新增后端存储）。
- CheckBox/控件范式参照 [ManagementPanelsView.xaml:2514](../desktop/SpiritKinDesktop/Controls/ManagementPanelsView.xaml) `CollaborationWorkerDryRunBox` 及其在 `MainWindowBootstrap.cs` 的绑定。
- 坑：C# ↔ 网关 JSON 是 snake_case；回填时用抑制标志避免触发写回。

### A2. 辩论身份漂移 + persona 锁定

**现象（已取证）**：main_text 在辩论中把"### 问题归因…"诊断文当辩论发言发出（`state/collaboration/messages.jsonl` message-f4d1b963e673）。
**根因**：`build_prompt`（[scripts/collaboration_agent_worker.py:1529-1610](../scripts/collaboration_agent_worker.py)）硬编码通用协作提示，
未显式锁定"你是谁 + 你的立场"，模型解析多方上下文时身份漂移。

**改动**：
1. `build_prompt` 开头注入不可协商身份块（中文）：
   - `你是 {label}（agent_id={agent}）。你只能以这个身份发言，禁止扮演或复述其他参与者的身份/立场。`
   - 辩论/立场类消息（现有立场提示分支）追加：`你的立场自始至终不变；不要输出对系统故障的诊断分析，除非人类明确要求。`
2. persona 可配置：辅助模型记录已有自由 JSON `request_params`（worker :2304-2305 `payload.update(request_params)` 生效）。
   新增约定字段 `persona`（string）：`build_prompt` 若在 assistant 配置中读到 `persona`，拼接在身份块之后。
   这样 UI（AssistModelEditor 的 request_params 编辑框）**零改动**即可给每个模型配人设。
3. 截断问题：桌面显示截 4000（`CollaborationDisplayContent`）是显示层截断非丢失，不改；
   若验收时发现正文层截断再单独归因，本轮不做。

### A3. 安全补漏（两处，全项目盘点发现）

1. **8765 事件桥零鉴权**：`backend/app/realtime_bridge.py` `handle_connection` 无 token 校验，任意本机进程可连上发布伪造事件（会直接驱动 avatar/桌面状态）。
   改动：握手首条消息要求携带与 8788 网关同源的 token（`SPIRITKIN_DESKTOP_TOKEN` 体系），校验失败即关连接；
   前端 `frontend/avatar_3d.html` 与桌面 WS 客户端同步带上 token。允许 env 开关 `SPIRITKIN_BRIDGE_AUTH`（默认开）以便本地调试。
2. **任意路径写文件**：`backend/devices/local_pc.py:469` `write_file_text` 无白名单。
   对齐网关上传限制（[command_gateway.py:1884](../backend/app/command_gateway.py) 限 state/data/runs）：越界路径拒绝并返回明确错误。

### A4-DONE-PLACEHOLDER

### A4. 双工交互翻转：持续互聊 + 人工软打断（2026-07-06 用户拍板）

**背景**：用户实测认为固定 6 轮自动停不合理（正是被吞回复的根源）。改为
**默认持续互聊、人类随时喊停**。打断粒度取**软打断**（当前正在生成的这条自然收尾，
之后不再接新一轮），不做硬打断——本项目已知"桌面被杀留孤儿进程"痛点，
不宜再引入 kill 正在跑的推理子进程的第二处风险。

**① 无限轮次（后端 `backend/app/collaboration_turn_guard.py`）**
- `_default_turn_cap`：`SPIRITKIN_COLLABORATION_TURN_CAP` 允许 `0` 或空 = **无上限**（不再 `max(1, cap)`）。
- `check_turn_allowance` / `record_turn_and_check`：`cap <= 0` 视为无限——直接 `allowed=True`，
  `turns_used` 仍照常累加供展示，但**不因用尽而 `awaiting_refill`**。
- **安全熔断**：新增 `SPIRITKIN_COLLABORATION_TURN_HARD_CAP`（默认 **40**，设 `0` 关闭）。
  即使 cap 无限，单 thread 连续自动互聊达硬熔断值时仍强制 `awaiting_refill` 并发 lifecycle
  `turn_hard_cap_reached`（中文"双工已连续互聊 N 轮自动暂停，点继续或发新消息"）。
  防无人值守时模型互相追问悄悄烧 token。硬熔断计数在人类发消息 / refill / pause-resume 时清零。

**② 即时改上限（不必重启 worker）**
- 现状短板：面板改 cap 只写进程 env，只对新线程生效（cap 在线程首次创建时快照进 record）。
- 网关新增 action `set_thread_turn_cap`（payload `thread_id`、`cap`）：直接改该 thread record 的 `cap`
  并置 `status=active`（复用 `refill_turns` 的写盘路径），**即时生效**。
- 桌面 `SaveCollaborationTurnCapFromPanel`（[CollaborationPanel.cs:821](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）：
  除写 env 外，若有当前会话 thread 则同时调 `set_thread_turn_cap` 应用到进行中的会话；
  上限框接受空/0 → 显示"持续（无上限）"。

**③ 停止双工按钮（软打断）**
- 网关新增 action `pause_turns`（payload `thread_id`、可选 `actor`）：把该 thread record
  的 `status` 置 `awaiting_refill` 且 `turns_used = cap`（cap 无限时置一个 ≥ 当前值的哨兵），
  使后端 persist 闸门与 worker 预检 `turn_allowance_ok` 立即拦截后续模型互聊。
  与既有 `refill_turns` 天然成对（暂停/继续）。
- worker 侧**无需改**：我已加的轮次预检 `turn_allowance_ok`（[collaboration_agent_worker.py](../scripts/collaboration_agent_worker.py)
  主循环，读 `turn_guard_status.thread.allowed`）会在下一轮开始前读到 `allowed=False`，
  **连思考都不启动**，正在生成的那条自然收尾——这正是"软打断"。
- 桌面协作面板加按钮"停止双工"（调 `pause_turns`）+ 已有的续轮/发新消息即恢复；
  按钮落点参照 `ManagementPanelsView.xaml` 协作区 `CollaborationTurnCapBox` 一行，
  wiring 参照 refill（[CollaborationPanel.cs:1298](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）。
- lifecycle 本地化补 `turn_paused` → "双工已暂停（人工打断）"。

**默认值变更**：`auto_reply` 保持默认开（A2/本次已定）；`TURN_CAP` **默认改为 0（无限）**，
硬熔断 40 兜底。即"开箱即持续互聊，靠停止按钮和硬熔断收口"。

**A4 验证**：
- 无限模式连发 >6 轮模型互聊不被拦（现有 turn_guard 集成测试补一个 cap=0 用例）；
- 硬熔断=3 时第 4 轮被 `turn_hard_cap_reached` 拦、人类发消息后计数清零可继续；
- `pause_turns` 后 `turn_guard_status.allowed=False`、worker 预检返 False（不渲染工作卡）、
  `refill_turns`/新消息后恢复；
- `set_thread_turn_cap` 即时改中途会话上限、无需重启 worker。

### A5. 工作卡与回复配对彻底修复（2026-07-06 实测回归，P0）

**现象（用户截图 + state.json 取证）**：辩论会话里后续轮次的回复以裸气泡出现（无思考链卡），
且有孤儿工作卡悬在时间线顶部与回复分离。active session dump：仅首轮有 work 卡，
后 4 条 assistant 回复完全无卡。

**根因（已确证三条，均在 CollaborationPanel.cs）**：
1. **投影只对激活会话生效**：worker 事件处理（[:84-89](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）
   `UpsertActiveSessionCollaborationWorkChains` 仅当事件 thread == 当前激活会话 thread 才把卡投进会话。
   用户切到新窗口期间，后台辩论线程的卡全部丢投影；回复投影（`ProjectCollaborationMessageToSession`）
   却对任意可解析会话生效 → 裸气泡。
2. **迟到的卡锚在回复之后**：`UpsertSessionCollaborationWorkChain` 新建投影卡时锚定
   "最新普通消息之后"（[:404-416](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）；
   若回复先落（时间线末尾），卡后到 → 卡排在自己回复下面，且已存在的回复不会重新锚定 → "卡片与回复分开"。
3. **源卡只留 8 轮**（[:362-365](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs)）：
   长辩论（>8 轮/agent）中未及投影就被淘汰的轮次永久丢卡。

**改动**：
1. 事件到达即按**事件自身 threadId** 解析目标会话投影（新方法，内部 `TryResolveSessionForCollaborationThread(threadId)`），
   不再要求是激活会话；`RenderActiveMessages` 仍只在激活时调用（避免后台刷屏，:91-97 注释语义保留）。
2. 双向锚定：新建投影卡时，若会话中已存在 `collab-reply-*` 且其 parent_message_id == 本卡轮次键，
   则卡锚 `回复.CreatedAt - 0.0005`（插到自己回复正上方）；否则维持现锚定。回复侧原逻辑（:1274-1286）不动。
3. 源卡淘汰保护：`cards.Count > 8` 淘汰前先对该轮执行一次投影（跨会话投影落地后此处天然满足，补一行注释即可）。

**A5 验证**：双开两个协作会话，A 会话辩论进行中切到 B 会话停留 ≥2 轮再切回 →
A 会话每条模型回复上方都有自己的思考卡（含中途轮次）；无孤儿卡、无裸气泡。

### A6. 本地模型多会话兼顾（P1，物理串行下的公平调度）

**现象**：辩论持续互聊（A4 无限轮）时，新开会话 @main_text 长时间不回复。
**根因**：每 agent 单 worker 主循环顺序处理消息 + 本地推理本身串行；
A4 后辩论线程持续产生互聊消息，把新会话的人类消息饿死。

**改动（最小）**：
1. worker 主循环批内排序：**人类消息优先**（`is_human_collaboration_agent(sender)` 先处理），
   同级按 created_at 升序。落点 [collaboration_agent_worker.py 主循环](../scripts/collaboration_agent_worker.py) `for message in messages` 前。
2. 排队可见化：当批内有人类消息但 worker 正忙时（即人类消息不是第一条被处理），
   对该消息先发 lifecycle `queued`（桌面本地化"本地模型正忙，已排队"），再照常处理。
3. 不做多实例并发（本地推理无并发收益，且引入锁竞争）。

**A6 验证**：A 会话辩论进行中，B 会话发"@main_text 你好" → main_text 下一轮优先回 B（先于辩论续轮）。

---

## B. P1 改进项（可与 A 分批交付）

### B1. 工作流引脚补真（backend/orchestrator/workflow_graph.py）
1. **数据流**：支持 `{{node.<node_id>.outputs.<key>}}` 模板——`run_node` 完成时把 outputs 存入 run 状态，
   模板解析（:421、:883 一带）先查节点 outputs 再查全局 inputs。
2. **必填校验**：`start_run` 时按节点 `interface_contract` 的 `required:true` 输入检查，缺失直接拒绝并报节点/端口名。
3. **后端校验端口兼容**：`validate()`（:200-228）复用桌面 `WorkflowConnectionRules.ArePortsCompatible`
   （`desktop/.../Features/Workflows/WorkflowGraphRules.cs:48`）的同一套 kind 规则（Python 侧重实现一份，以后端为准源，桌面注释指向后端）。
4. **自动推进器**：后台循环对 RUNNING run 调 `run_next`；agent_task 认领超时（默认 10 分钟，env 可调）自动重派或标记失败。
5. branch 支持结构化条件（`==/!=/>/<` 数值比较），保留字符串 truthy 兼容。

### B2. MCP 真实执行
`backend/tools/mcp_adapter.py:62-68` invoke 目前只返回 `pending_mcp_execution:True`（纯占位）。
实现 stdio 子进程 MCP client（initialize→tools/list→tools/call），HTTP 传输可后置。
这是"后续不写代码、靠插件扩展自进化"的关键通道，优先级高于 B1。

### B3. 运行期工具注册
`backend/tools/registry.py` ToolRegistry 启动时固定。新增受控注册通道：
把工作区脚本（python_worker 已有 root 约束）或 MCP tools/list 结果动态注册为 ToolSpec，默认关、面板开关控制。

### B4. 占位清理
- 假 embedding：`backend/app/search_management.py:145`（hashing 占位）→ 接 config.yaml 已声明的 embedding 模型。
- `backend/model/training/workbench.py` 曾引用不存在的 `peft_lora_train` 模块 → 补实现或移除该训练路径并明确报错。
- 流式 ASR 占位（`backend/perception/audio/streaming_listener.py:39`）→ 本轮不做，挂 backlog。

---

## F. 2026-07-07 新批次（流式呈现，用户拍板"做流式显示吧，包括思考链"）

> 交 GPT/OPUS 实现，Claude 验收。实现前先读 C 禁改区（本批已扩充）。

### F1. 协作回复流式显示（含思考链）P0

**现状（已核实，别重复造）**：worker 侧流式基建已完整——
- 模型 API 参与者已按 token 流式请求（[collaboration_agent_worker.py:2142-2161](../scripts/collaboration_agent_worker.py) `request_streaming_model_reply`，`on_token` 回调）；
- `StreamTokenBatcher`（[:1936-1967](../scripts/collaboration_agent_worker.py)）按 0.7s/400 字合批，经 `record_worker_event` 发出 `status="stream"`、`stream="token"|"reasoning"`、`metadata.lifecycle="token"` 的 worker 事件（[:2129-2141](../scripts/collaboration_agent_worker.py)）；
- 网关把它们包装成 work 事件文本 `"{stream}: {output}"`（[command_gateway.py:446-453](../backend/app/command_gateway.py)）→ 桌面已作为工作卡步骤展示。

**缺口**：token 流只进了工作卡步骤，聊天气泡要等 worker 整条 post_message 才一次性出现。

**改动（纯桌面侧投影，不改 worker/网关协议）**：
1. `CollaborationPanel.cs` 工作链 upsert 路径（[:356](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs) 源卡创建、[:371](../desktop/SpiritKinDesktop/Features/Context/CollaborationPanel.cs) `UpsertCollaborationWorkChainsForThread` 一带）：
   - `stream=="token"`：为该 (thread, 父消息 id, agent) 维护一条**草稿气泡**（普通 assistant 消息、标记 streaming draft），每批 token 追加正文刷新（走现有差量渲染 upsert，禁 Clear+Add）；
   - `stream=="reasoning"`：仍进工作卡"思考"步骤，但同一轮内**连续追加**成完整思考链，而非只见最后一批。
2. 最终 answer 投影（`ProjectCollaborationMessageToSession` 锚点）到达时：按 (父消息 id + agent) 匹配，用权威消息**替换**草稿气泡，保持配对锚点语义（CreatedAt=工作卡+0.0005，禁改区）；不得出现草稿+正式双气泡。
3. 兜底：收到 `request_failed` lifecycle → 草稿气泡标"生成中断"，不留半截无状态文本。
4. 自动滚动：draft 更新走现有 `_messageAutoScrollSticky` 逻辑，别新加滚动调用。

**F1 验证**：辩论中 DS/Spirit 回复逐段出现在气泡（约 0.7s 粒度）；思考链在卡内连续增长；最终文与流式拼接一致（抽 2 条对比）；中断场景无孤儿草稿。

### F2. 消息 Markdown 轻量渲染 P1

**现象**：模型输出的 `#`、`**`、`*`、反引号在气泡里裸露（用户点名）。
**改动**：桌面新增 `MarkdownLite`（不引第三方包）：字符串 → WPF `Inline` 集合，支持 `**粗体**`、`*斜体*`、`` `行内代码` ``、`#/##/###` 标题（加粗+放大+独立行）、`-`/`*` 列表（• 缩进）、``` 代码块（等宽+底色）。落点：消息气泡 DataTemplate 正文 TextBlock（`MessageViewModel` 正文绑定处）；F1 草稿气泡同样走该渲染。表格/链接/嵌套本批不做，原样输出。
**F2 验证**：单测覆盖 6 类语法 + 未闭合容错；实测一条含标题/粗体/代码块的回复无裸符号。

### F3. 工作卡每轮独立计时 P1

**现象**：卡计时按协作线程累计（出现过 28 分钟）。用户要每轮独立。
**根因**：运行中卡时长 = now − 卡 CreatedAt（[MessageComposerViewModels.cs:535-542](../desktop/SpiritKinDesktop/ViewModels/MessageComposerViewModels.cs)），而协作源卡按线程复用不终结。
**改动**：源卡按"轮"生命周期，键 = (thread, parent_message_id, agent)：answer 投影/终态 lifecycle 到达时置终态并**冻结用时**；下一轮（新的父消息 id）新开卡（新 CreatedAt）。配对锚点语义保持。
**F3 验证**：连续两轮辩论，第二轮卡计时从 0 开始；终态卡时长不再随墙钟增长。

### F4. 远端模型跨线程并行 P2（本地保持串行）

**结论**：本地 main_text 受物理推理限制保持串行 + A6 人类优先；远端 API（DeepSeek）可并行，但**同一线程内仍串行**（发言权锁语义），收益是多个会话同时用 DeepSeek 不互相排队。
**改动**：worker 主循环对 `transport==model_api` 参与者按 thread 分组，不同 thread 用小并发池（默认 2，env `SPIRITKIN_COLLABORATION_REMOTE_CONCURRENCY`）；同 thread 严格顺序；本地参与者不进池。
**F4 验证**：A/B 两会话同时 @DeepSeek → 两边几乎同时开始回复；同一会话内两条消息仍按序。

### F5. 已由 Claude 直接完成（不要重复实现，验收即可）
- 双工开关唯一化：面板复选框已删，唯一开关在聊天头部，旁挂实时余额（不限模式显示"熔断前 n/40"）；
- 双工开关虚线焦点框已去（FocusVisualStyle=null）；
- @提及预览从底部状态行改为输入框上方悬浮气泡（失焦自动收起）；
- `turn_guard_status` 高频轮询已入网关 work 事件免记名单；
- 重复/孤儿 worker 已清，worker 归桌面 PID 注册表统一管理；
- **双工开关 worker 端实时化**（2026-07-07 事故修复）：worker 原来只在启动时快照 `collaboration_auto_reply_enabled()`（:67），
  桌面开关后开→worker 仍按关把对方回复整条 `skipped(auto_reply_disabled)` 静默吞掉（有 worker_events.jsonl 取证）。
  已改为主循环每轮轮询前重读开关（[collaboration_agent_worker.py 主循环](../scripts/collaboration_agent_worker.py)），
  切换即时生效，无需重启 worker。

### F6. worker 自愈重启静默失败（P0 排障，交实现方带调试现场复现）

**现象（2026-07-07 02:00 实测取证）**：外部 `taskkill /F` 同时杀掉 main_text 与 model_deepseek 两个 worker（模拟异常退出，exit=1）：
- 两个 worker 日志都写了 `exited ... code=1`（Exited 处理器前半段执行了）；
- main_text 的 PID 注册表条目被清（`UnregisterCollaborationWorkerPid` 执行到了），
  但 `TryAutoRestartCollaborationWorker` 的任何日志（skipped/suppressed/auto-restart in Ns）都没出现；
- model_deepseek 的注册表条目**没被清**（它的 `Dispatcher.Invoke` 整段疑似未执行）；
- 等待 >60s 无任何重启；桌面进程 Responding=True，未崩溃；项目无全局 DispatcherUnhandledException 吞没器。

**排查方向**（[CollaborationWorkerRuntime.cs:175-267](../desktop/SpiritKinDesktop/Features/Context/CollaborationWorkerRuntime.cs)）：
1. 两个 Exited 处理器并发进 `Dispatcher.Invoke` 时的执行断点——在 :184-199 逐行加带时间戳的文件日志复现一次双杀；
2. `SyncCollaborationWorkerControls`（:591）在 Invoke 内是否抛出（怀疑控件访问异常被某处静默吃掉）；
3. 修复后补验收：双杀 → 两个 worker 都在退避窗口内自动重启、注册表条目先清后登。

**兜底语义（已存在，勿破坏）**：worker 死光后，用户发下一条协作消息时 `EnsureCollaborationWorkersForAgents` 会重新拉起——自愈失败不致永久失联，但用户会白等一轮。

---

## G. 2026-07-07 自愈批次（用户拍板："自愈自修复能力等于0，需要改进"）

> 背景事故：网关瞬断一次 HTTP 响应 → 桌面整个进程闪退（无全局异常兜底）；
> 桌面空白（后端服务死了）时不会自动拉起缺失服务。交 GPT/OPUS 实现，Claude 验收。

### G0. 已由 Claude 直接完成（勿重复）
- 全局异常兜底（`App.xaml.cs`）：`DispatcherUnhandledException`（Handled=true 继续运行）+
  `UnobservedTaskException` + `AppDomain.UnhandledException`，全部落 `state/logs/desktop_unhandled.log` 尸检日志；
- 协作发送路径定点捕获 `HttpRequestException` → 状态栏"发送失败请重发"（CollaborationPanel.cs post_message 段）；
- **G1/G2 已由 Claude 直接实现（2026-07-07，勿重复）**：
  - 新增 `Features/Shell/ServiceHealthWatchdog.cs`：60s DispatcherTimer 探测 `CommandGatewayHealthy()`，
    连续 2 次不健康 → `Task.Run(EnsureLocalServices)` 后台恢复；10 分钟窗口 ≥3 次仍失败 → 停手红字报警；
    恢复成功后联动 `LoadServicesAsync()` + `SyncCollaborationWorkerControls()`；
  - `MainWindow.xaml` 加 `ServiceHealthBannerElement` 顶部横幅（可关闭，恢复后 6s 自动收起）；
  - 新增 `Services/ServiceHealthSignals.cs`：`DesktopApiRuntime.cs` Get/Post 统一入口埋点，
    连续 3 次 401 → 横幅提示"token 不匹配，请用启动桌面.bat 重启"（任意成功请求清零）；
  - **附带修复**：`CommandGatewayHealthy()` 原探 `/desktop/workflows`（带 token 时必 401 误判离线，
    会让看门狗反复杀健康网关），改探无鉴权 `/health` 端点。

### G1. 服务看门狗：接活死代码 EnsureLocalServices（P0）

**关键发现（已核实，省一半工作量）**：`LocalServiceRuntime.cs:120` `EnsureLocalServices()`
已实现全套"健康检查→抢占坏端口→重启 bridge/command_gateway/frontend"，但**全项目零调用**。

**改动**：
1. 桌面加一个 60s 看门狗 Timer（挂现有 `_contextController.StartSyncTimer()` 同类范式，
   `MainWindowBootstrap.cs` 初始化区）：调 `CommandGatewayHealthy()` 轻量探测，
   连续 2 次不健康 → 后台线程调 `EnsureLocalServices()`（注意它内部有 `Thread.Sleep`，
   **禁止在 UI 线程直接调**，用 `Task.Run` + 完成后 Dispatcher 回 UI 刷状态）。
2. 拉起前后在 `WorkspaceSidebar.ConnectionStatusText` 提示："检测到后端服务离线，正在自动恢复… / 已恢复"。
3. 退避限次：10 分钟窗口内自动恢复 ≥3 次仍不健康 → 停止尝试并红字提示"请查看日志"，防拉起风暴。
4. 恢复后联动：网关恢复后自动重跑 `LoadServicesAsync()` + 协作 worker 存活检查
   （worker 依赖网关，网关死过一次后 worker 大概率也断了推送）。
5. **坑**：`EnsureLocalServices` 会 kill 占端口进程再重启（EnsureCommandGatewayService :135-140）——
   与启动器拉起的服务是同一套端口，看门狗恢复语义与启动器一致，不冲突；但 token 必须沿用
   当前进程环境的 `SPIRITKIN_MOBILE_TOKEN`，不得生成新 token（否则新网关与桌面对不上，禁改区）。

### G2. 空白界面自检横幅（P1）

**现象**：后端死了桌面只是空白/静默 401，用户不知道发生了什么。
**改动**：主窗口顶部加一条可关闭的状态横幅（新增 `x:Name="ServiceHealthBannerElement"`，默认 Collapsed）：
- 看门狗判定网关不健康时显示"后端服务离线，正在恢复（第 n 次）"；
- 401 连续出现时显示"token 不匹配，请用启动器/启动桌面.bat 重新启动"（401 计数在 `PostJsonAsync`/`GetJsonAsync` 统一入口埋点）；
- 恢复后自动隐藏。

### G3. 失败自诊断回路（P2，第一阶段只产出建议）

把已有的尸检与失败信号喂给模型形成"自愈回路"（用户长期诉求"自进化"）：
1. 数据源：`state/logs/desktop_unhandled.log`（G0 新增）、worker `request_failed` 摘要、
   看门狗恢复失败记录；
2. 通道：复用协作消息（`role=question` 发给 `main_text`/`programming`），
   开关 `SPIRITKIN_SELF_HEAL_DIAGNOSIS`（默认关，面板 CheckBox 控制）；
3. **只产出诊断+修复建议文本，不自动改代码**（自动改码需要另一轮方案+用户拍板）；
4. 节流：同一错误指纹（异常类型+顶帧）1 小时内只发一次诊断请求。

### G 验证
- 手杀 command_gateway 进程 → 60-120s 内桌面横幅出现并自动恢复，网关端口重新监听、面板服务列表变绿；
- 手杀 frontend → 同上恢复；连杀 3 次验证退避停手 + 红字提示；
- 恢复后发一条协作消息全链路正常（token 未变）；
- 摘 token 改错 → 横幅提示 token 不匹配而非静默 401；
- 全程 `desktop_unhandled.log` 无新增未捕获异常。

---

## C. 禁改区（稳定区，实现时不得触碰）

以下均为近期修完并实测通过的部位，**任何本方案改动不得改变其行为**：

| 部位 | 位置 | 说明 |
|---|---|---|
| 发言权锁 | worker `speak_turn` 段（try_acquire/acquire/release/fetch_thread_replies_since/enter_speak_turn） | 非阻塞 try-acquire（2026-07-08 v3：没抢到 deferred=True 先并行起草，草稿完成后 `revise_with_finalized_replies` 阻塞抢锁+修订一次成稿）；O_EXCL 原子抢锁、900s stale 接管 |
| 发言队列 | worker `enter_speak_queue`/`claim_speak_slot`/`SpeakSlot` 段 | 2026-07-08 v4"谁先想完谁先发言"：进队只登记；首个产出可见正文者写 `speaking_at` 抢席位现场直播，其余 token 改道 reasoning 泳道转后台草稿，前位定稿后 `revise_with_finalized_replies` 修订上屏——已上屏发言绝不回收；与发言权锁互斥使用（queue_dir 为 None 才走锁） |
| fan-out 收件人 | worker `reply_recipients`（:584-625） | 含广播熔断 `FAN_OUT_MODEL_LIMIT`（N² 风暴保护，2026-07-05 实测教训），不得移除 |
| 卡片正文配对 | `CollaborationPanel.cs` `ProjectCollaborationMessageToSession` 锚点（CreatedAt=工作卡+0.0005） | 回复挂在自己思考卡正下方 |
| 消息差量渲染 | 桌面消息列表 upsert 路径 | 修的是选中复制被打断问题，禁止回退成 Clear+Add |
| 话题锚点 | `TopicAnchorNavigation.cs` | 内容不变不重建（否则 Click 失效） |
| 参与者登记/回包保护 | `RegisterSessionCollaborationAgents` 及竞态修复 | |
| 轮次护栏语义 | `collaboration_turn_guard.py` 判定逻辑 | A1 只加"续轮调用 + 可见化"，不改护栏本身的计数/上限判定 |
| agent_id | 全链路 | `main_text` 等 id 不改，Spirit 只是展示层名称 |
| work 事件免记名单 | `command_gateway.py` `_build_collaboration_work_events` 白名单集合 | 只可加只读 action，不可删已有项 |
| 双工开关/余额链路 | `ChatDuplexToggle`/`ChatDuplexBalanceText`（头部）、`collaboration_auto_reply` action、`auto_reply.json` | 全局唯一开关在聊天头部；面板不得再加开关 |
| worker 生命周期管理 | `CollaborationWorkerRuntime.cs`（PID 注册表 `worker_pids.json`、孤儿清理、自动重启退避） | worker 只允许桌面拉起；任何人不得再手动起 worker 叠加 |
| 网关鉴权 | `backend/security/http.py`、`X-SpiritKin-Token` 头 | |
| 状态文件格式 | `state/desktop_console/state.json`、`state/collaboration/*.json`、消息 envelope 字段 | 只增字段不改语义；改前先加迁移 |
| 启动器 | `scripts/start_desktop_console.py` 服务拉起顺序/端口/token 传递 | |
| 桌面控件命名契约 | 所有 `x:Name="...Element"` → codebehind 属性映射 | 重命名会静默断多文件引用 |

## D. 验证标准（验收时 Claude 会实测，不只看代码）

1. `python -m pytest backend/tests/unit/test_collaboration_worker_script.py backend/tests/unit/test_runtime_contracts.py -q` —— 基线 **104 passed**，新增测试只增不减。
2. 新增单测：post_reply 拒收→lifecycle 事件；refill 请求体构造；build_prompt 含身份块与 persona 注入；write_file_text 越界拒绝；bridge 鉴权失败关连接。
3. `dotnet build desktop/SpiritKinDesktop` 0 warning 0 error；`dotnet test`（桌面基线 118）。
4. 官方启动器 `python scripts/start_desktop_console.py --token <t> --open-mode wpf` 全栈手测：
   - 辩论打满 6 轮后，人类再发一条消息 → 模型互聊**继续**（refill 生效）；
   - 中途关闭双工开关 → 出现"本条回复未投递"提示而非静默；
   - 面板能看到"双工余额 n/6"；
   - 辩论中 main_text 不再输出诊断文、立场不漂移（发起一场正反方辩论实测 ≥8 轮）；
   - 无 token 的 WS 客户端连 8765 被拒，avatar 正常工作。
5. 环境注意：pytest/ruff 在 Anaconda 环境；`PYTHONIOENCODING=utf-8`。

## E. 交付顺序

A1（体验最痛）→ A2 → A3 → B2（MCP，自进化关键）→ B1 → B3/B4。
每批独立提交，禁止跨批混改；每批附自测证据（命令+输出摘要）。
