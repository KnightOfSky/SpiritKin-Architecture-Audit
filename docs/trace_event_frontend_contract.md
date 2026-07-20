# Trace Event 前端字段契约 v1（桌面端消费侧）

本文件是前端（WPF 桌面工作链）对后端 trace event schema v1 的消费契约。
后端按 GPT 给出的 schema v1 落地；本文件说明前端**实际会读哪些字段、怎么用、哪些当前还消费不了、需要后端补什么**。

对齐原则（已确认）：
- 排序只看 `seq`。
- 层级只看 `parent_id` / `span_id`。
- 最终状态只看 terminal event：`run.completed` / `run.failed` / `run.cancelled`。
- `title` / `summary` 仅用于显示，**不用于判断状态**。
- `payload` 仅作详情扩展，不作核心状态依赖。
- 不依赖中文文案判断任何状态或类型。

---

## 1. 前端当前真实结构（接入前的现状）

桌面端工作链的数据模型（`DesktopWorkStep`）当前只有 4 个字段：

| 字段 | 用途 |
| --- | --- |
| `Kind` | 驱动视觉样式，5 个桶：`thinking` / `command` / `result` / `diff` / `permission` |
| `Title` | 块标题 |
| `Detail` | 块正文（终端块或纯文本行） |
| `CreatedAt` | 排序键 |

渲染分桶（`WorkStepViewModel`）：
- `thinking` → 浅色纯文本行（`·` 字形）
- `command` → 深色终端块（`⌘`，等宽字体）
- `result` → 深色块（`⤳`，绿色强调）
- `diff` → 深色块（`±`，橙色强调）
- `permission` → 红色警示块（`⚠`）

**当前限制（接入 schema v1 时要解决的）**：
1. 扁平列表，无层级嵌套。
2. 排序靠 `CreatedAt`（秒级，易撞），将切换为 `seq`。
3. 无 run/step/tool 的生命周期状态（running→completed 是靠新增独立步骤，不是同一行状态跃迁）。
4. 事件只在 `/command` HTTP 返回后一次性灌入，无流式、无 replay。

---

## 2. schema v1 → 前端字段映射

前端会消费的字段（其余字段忽略但不报错）：

| schema v1 字段 | 前端用途 | 必需性 |
| --- | --- | --- |
| `seq` | **唯一排序键**，单调递增 | 必需 |
| `run_id` | 关联一次任务的所有事件到同一 run 卡片 | 必需 |
| `event_id` | 去重幂等（replay/stream 重叠时） | 必需 |
| `type` | 主类型，驱动 Kind + 生命周期语义 | 必需 |
| `phase` | 辅助分组（run/plan/step/tool/execution/system） | 必需 |
| `status` | 状态徽章（queued/running/completed/failed/...） | 必需 |
| `span_id` | 同一逻辑单元的生命周期合并键（started/output/completed 同 span 合并为一行） | 必需 |
| `parent_id` | 层级父节点，用于还原 run>plan>step>tool 树 | 必需 |
| `step_id` | 步骤分组（同 step 下的多个 tool） | 可选 |
| `title` | 行标题显示 | 必需 |
| `summary` | 行正文显示 | 可选 |
| `actor` | 执行者标签（worker-1 等） | 可选 |
| `tool.name` | 工具块标题 | tool 事件必需 |
| `tool.operation` | 工具命令体（等宽显示） | 可选 |
| `tool.input_summary` | 工具输入摘要（折叠） | 可选 |
| `tool.output_summary` | 工具输出摘要（折叠） | 可选 |
| `tool.exit_code` | 成功/失败判定与显示 | 可选 |
| `at` | 仅显示用（耗时/时间戳），不参与排序 | 可选 |

---

## 3. type → Kind 视觉映射（前端内部，不依赖文案）

| type | 渲染 Kind | 说明 |
| --- | --- | --- |
| `run.started` / `run.queued` | run 卡片头 | 顶层容器，显示 run 状态 |
| `run.completed` / `run.failed` / `run.cancelled` | run 卡片终态 | **terminal**，决定整体状态 |
| `run.retried` | run 卡片状态更新 | |
| `plan.created` | `thinking` | 计划摘要，浅色 |
| `step.started` | step 行（status=running） | 同 span 后续状态跃迁 |
| `step.completed` | step 行（status=completed） | |
| `step.failed` | step 行（status=failed） | 红色 |
| `tool.started` | `command` | 深色终端块，显示 operation |
| `tool.output` | 追加到同 span 的 output_summary | 不新增行 |
| `tool.completed` | `result`（exit_code=0）否则 `permission` | 同 span 状态收尾 |
| `tool.failed` | `permission` | 红色，显示错误摘要 |
| `execution.assigned` | execution 行 | 显示 actor |
| `execution.reclaimed` | execution 行状态更新 | |

> 注意：`tool.started` 与 `tool.completed/failed` **必须共享同一 `span_id`**，前端据此把"开始→完成"合并成一行带状态跃迁，而不是两行。这是 Codex-like 体验的关键。

---

## 4. 层级还原规则

前端按 `parent_id`/`span_id` 构建树：

```
run (span_run)
├── plan (parent=span_run)
├── step (span_step_1, parent=span_run)
│   ├── tool (span_tool_a, parent=span_step_1)
│   └── tool (span_tool_b, parent=span_step_1)
└── step (span_step_2, parent=span_run)
```

要求后端保证：
- 顶层 run 事件 `span_id` 稳定（如 `span_run_<run_id>`），其 `parent_id` 为空。
- 容器事件（step/tool）的 `parent_id` 指向其父 span。
- 同一逻辑单元的 started/output/completed 事件 `span_id` 一致。

无 `parent_id` 的事件挂到 run 根下，按 `seq` 平铺。

---

## 5. 需要后端明确的 5 个开放点（阻塞前端接入）

1. **run_id 关联**：`/command` 的 reply 是否回带 `run_id`？前端需要它来定位/轮询/replay 对应 timeline。
   - 期望：reply 顶层或 `data` 内含 `run_id`。

2. **传输方式**：是否提供
   - (a) `/command` 返回事件批（兼容现状）；
   - (b) 按 `run_id` 的 replay 端点（断线恢复用）；
   - (c) 可选 SSE/WebSocket 流（实时）？
   - 前端最低要求：(a)+(b) 可先上扁平实时近似；(c) 才能做到真正逐步流式。

3. **span 配对保证**：started/output/completed/failed 是否严格共享 `span_id`？（决定能否合并为单行状态机）

4. **tool.output 语义**：增量输出是多条 `tool.output` 事件追加，还是 `completed` 时一次性给 `output_summary`？前端两种都能做，但需明确以免重复拼接。

5. **timeline_summary 兼容字段**：snapshot 返回 recent runs 时，是否带 `timeline_summary`（每个 run 的最终状态 + 计数），让前端在不拉全量事件时也能渲染折叠态？

---

## 6. 前端接入计划（schema 确认后，Opus 侧）

1. `DesktopWorkStep` 扩展为 `TraceEvent`：新增 `seq / runId / eventId / type / phase / status / spanId / parentId / stepId / actor / tool*`，保留旧 `Kind/Title/Detail` 作为派生显示字段。
2. `BuildSteps` 排序键从 `CreatedAt` 改为 `seq`；按 `eventId` 去重。
3. 新增 span 合并：同 `spanId` 的 started/output/completed 折叠为单行状态机。
4. 新增层级渲染：按 `parentId` 构树，run>step>tool 缩进/折叠。
5. terminal event 驱动 run 卡片最终状态徽章。
6. 接 replay：断线后按 `run_id` 拉全量 timeline 重建。
7. 空 timeline 显示"等待后端事件"，**绝不造假步骤**。
