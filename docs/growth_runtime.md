# Growth Runtime 契约

版本：1.0（2026-07-19）
状态：现行架构规范
实现入口：`backend/capability/growth/`

## 1. 定位

Growth Runtime 是 Evolution 与 Governance 下的受治理研发层。它负责发现能力缺口、形成候选、收集验证证据并提交审核，但不是新的执行平面，也不能绕过 Tool Registry、权限门、Review 或 Registry 激活策略。

它解决的是“系统不会时下一步做什么”，而不是让模型在生产 workspace 中自由安装或改写代码。

固定主链为：

```text
Need
  -> Capability Gap Analysis
  -> Capability / Workflow / Skill / Tool / Code / Model Candidate
  -> Sandbox / Dry Run / Benchmark Evidence
  -> Review
  -> Candidate Registry
  -> Separate Manual Activation
```

所有 Builder 输出都是 Candidate。登记不等于启用，`activation.enabled` 在登记后仍必须为 `false`。

## 2. 六类 Builder

| Builder | 输入信号 | 候选目标 | 不能自动做的事 |
| --- | --- | --- | --- |
| Capability Builder | Planner 的能力缺口 | Capability contract | 注册或启用能力 |
| Workflow Builder | 重复成功轨迹 | Workflow definition | 修改生产 Workflow Registry |
| Skill Builder | 重复失败、能力实现需要 | Skill package | 覆盖现有 Skill 或升权 |
| Tool Builder | 缺失工具、MCP、包或外部 API | Tool binding / install plan | 下载、安装或运行外部代码 |
| Code Builder | 工具仍无法满足的缺口 | Code artifact | 写入生产源码或部署 |
| Model Builder | 模型能力、质量或路由缺口 | Model/eval candidate | 下载、加载或切换生产模型 |

`Human` 不是第七类 Builder。它是无法继续自动形成安全候选时的显式升级终点。

## 3. 候选谱系与升级图

Builder 之间不是六个平行按钮。每次升级必须通过 `escalate_candidate` 建立父子关系：

| 当前候选 | 允许的下一步 |
| --- | --- |
| Capability | Workflow, Skill, Tool, Code, Model, Human |
| Workflow | Skill, Tool, Code, Model, Human |
| Skill | Tool, Code, Model, Human |
| Tool | Code, Model, Human |
| Code | Model, Human |
| Model | Human |

不允许反向升级，例如 Skill 不能升级成 Workflow。技术升级会创建新的子候选，并把父候选冻结为 `escalated`；转人工不会伪造子候选，而是把当前候选标记为 `needs_human`。

每个候选的 `lineage` 至少包含：

```json
{
  "parent_candidate_id": "",
  "root_candidate_id": "growth-capability-...",
  "depth": 0,
  "transition": "skill->tool"
}
```

每个已路由候选的 `resolution` 至少包含目标类型、子候选 ID、理由、证据、操作者和时间。升级必须显式确认，父子候选均保持 `activation.enabled=false`。

## 4. 生命周期

不同候选只经过与自身有关的阶段：

| Kind | 有序阶段 |
| --- | --- |
| Capability | gap_analysis -> research -> design -> benchmark -> review -> registry |
| Workflow | gap_analysis -> design -> dry_run -> benchmark -> review -> registry |
| Skill | gap_analysis -> research -> design -> sandbox -> dry_run -> benchmark -> review -> registry |
| Tool | gap_analysis -> research -> sandbox -> dry_run -> benchmark -> review -> registry |
| Code | gap_analysis -> design -> sandbox -> dry_run -> benchmark -> review -> registry |
| Model | gap_analysis -> research -> benchmark -> review -> registry |

阶段只能按顺序推进，每次必须提交非空 evidence。`registry` 不能通过 `advance_stage` 到达，只能由具名审核者批准后执行 `register_candidate`。

## 5. 自动观察闭环

- Capability Growth：`analyze_gap` 根据 Planner 提供的 required/available capabilities 生成稳定、去重的 Capability 候选。
- Workflow Growth：成功轨迹达到有界阈值后，`observe_trajectory` 形成 Workflow 候选。
- Skill Growth：同类失败达到有界阈值后，`observe_failure` 形成 Skill 候选。
- Tool/Code/Model Growth：由 Planner 或上一级候选以受治理升级动作形成候选。

观察器只创建 Candidate，不推进阶段、不审核、不登记、不激活。

## 6. Builder Artifact 与沙箱

`prepare_builder_artifact` 只盘点本地 Tool、MCP、Model 与 Worker 清单，并生成结构化研究、验证和 Registry 计划。它不联网研究，不动态发现远程 MCP，不安装依赖，不执行候选。

### 6.1 受管公开仓库研究

`research_candidate` 是与 Builder 准备分离的显式治理动作。当前实现只访问固定的 GitHub Repository Search 公开元数据端点，使用 GitHub 默认最佳匹配，单次最多读取 5 条结果，并记录查询、许可标识、仓库状态和 rate-limit 摘要。它不携带认证信息，不接受客户端覆盖 endpoint，不在限流后自动重试。

搜索词必须是可公开发送的技术关键词；WPF、iOS 原生和 iOS/PWA 使用独立输入框，不能复用审核证据。服务端在联网前拒绝常见 credential/secret 赋值形式。留空时可从候选需求生成有界查询，因此操作者确认前仍须把候选需求视为即将公开发送的搜索词。

研究报告写入受管 Growth artifact root，并以有界摘要挂入候选 evidence。动作不克隆、不下载、不安装、不执行外部代码，也不推进阶段、不审核、不登记、不激活。随后重新运行 `prepare_builder_artifact` 时，已审阅的仓库元数据可以成为 declared source；真实源码获取、许可验收、安装、执行和 benchmark 仍属于尚未开放的隔离沙箱流程。

`verify_builder_artifact` 当前执行的是受管静态沙箱预检，检查：

- artifact/candidate/workspace 完整性；
- 写入范围与受管目录；
- 网络、安装、执行和激活护栏；
- 本地 Registry 匹配与需人工补源项。

Docker Runtime 使用两阶段探测：先读取 `docker info` 元数据，再在操作者显式确认后，以受批准 immutable 镜像启动一次固定的受信命令。固定探针同样禁止网络、host mount 和自动拉取，并使用只读、非 root、drop capabilities 与资源限额；只有执行探针通过时 `candidate_execution_enabled=true`。探针不运行候选代码，也不推进阶段或激活能力。

### 6.2 容器候选执行

当操作者在 `config/config.yaml` 或环境变量中明确启用 Growth Sandbox，并配置一个本地存在的 immutable `repository@sha256:<digest>` 镜像后，`prepare_sandbox_bundle` 可以为 Skill/Tool/Code 候选写入有界 UTF-8 文本 Bundle。Bundle 只允许安全相对路径、最多 40 个文件/256 KiB、argv 命令和无明文凭据内容；每个文件与 manifest 都有 SHA-256。

`execute_builder_sandbox` 只接受已通过静态预检的 Bundle，并要求独立确认字符串。执行器不会拉取镜像，不接受客户端镜像覆盖，不使用 host bind mount；它先写入一次性 Docker managed volume，再以只读方式挂载到候选容器。容器使用 `--network none`、只读 root、非 root `65534:65534`、drop all capabilities、`no-new-privileges`、256 MiB/0.50 CPU/64 PID 和 30 秒上限，执行结束强制删除容器和 volume。输出只保留有界摘要。

容器测试结果写入受管 execution report 和候选 evidence，永远不自动安装依赖、不推进阶段、不审核、不登记、不激活。静态预检、容器测试和人工 Review 是三个独立事实；执行器不可用时客户端必须显示不可用并保留重试入口。

### 6.3 Benchmark Runtime 与 Promotion Gate

统一评测实现位于 `backend/evaluation/`。Growth 的六类候选都必须到达 `benchmark` 阶段，并提交结构化 Before/After 测量；服务端统一计算 `overall_score`、delta 与 Promotion Gate，客户端不能直接声明通过。

统一字段包括：`benchmark_id`、target/type/version、baseline、dataset、success rate、latency、cost、retry/review count、quality/overall score、measurement source、workspace 与操作者。成功率或质量回退、总分未严格提升、低于最低阈值时禁止进入 Review。

Skill、Tool 和 Code Benchmark 必须关联最近一次通过的隔离执行；Workflow 必须先有 Dry Run evidence；Model 还必须通过至少两个不同具名来源的 Model Jury。Benchmark 只产生 Candidate evidence，不会推进阶段、批准、登记、切换生产组件或激活能力。

## 7. API

桌面与移动主控消费同一 Runtime：

- `GET/POST /desktop/growth`
- `GET/POST /ios/growth`

主要动作包括：

- `analyze_gap`
- `mine_workflow`
- `propose_skill|tool|code|model`
- `observe_failure|trajectory`
- `escalate_candidate`
- `research_candidate`
- `prepare_builder_artifact`
- `verify_builder_artifact`
- `prepare_sandbox_bundle`
- `execute_builder_sandbox`
- `record_candidate_benchmark`
- `run_model_jury`
- `advance_stage`
- `review_candidate`
- `register_candidate`
- `probe_sandbox_runtime`

公共 API 不接受 event、registry、artifact 或 sandbox 文件路径覆盖。workspace 由认证绑定或管理端权限确认；普通 iOS workspace 不能治理全局候选和其他 workspace 候选。

## 8. 多端呈现

WPF、iOS 原生和 iOS/PWA 必须从快照读取：候选状态、阶段、风险、谱系、服务端允许的升级目标、Builder 工件、沙箱状态和 Registry 状态。客户端不得各自维护第二份状态机。

三端治理动作都要求证据与确认。客户端只能创建下一级 Candidate 或转人工，不得提供“直接启用”按钮。

## 9. 持久化与审计

- Candidate event log：受管 `state/growth/events.jsonl`
- Candidate registry log：受管 `state/growth/registry.jsonl`
- Builder artifacts：受管 Growth artifact root
- Sandbox runtime status：受管 Growth sandbox state

对外快照移除内部路径。候选使用稳定 `candidate_id` 去重；谱系通过 `root_candidate_id` 和 `parent_candidate_id` 追踪。

## 10. 验收边界

本机可验证：候选生成、去重、workspace 隔离、有序阶段、谱系升级、转人工、静态预检、审核、登记不激活，以及三端契约/桌面构建。

仍需外部环境验证：

- Docker 或其他隔离执行器中的真实候选运行与 benchmark；
- 外部 Tool/MCP/package 的许可、下载和安装审批；
- Code/Model 候选的真实编译、评测、资源预算和回滚；
- macOS/Xcode/iPhone 与真实 Remote Worker 的端到端操作。

Growth 的评测事实源见 `benchmark_runtime.md`；它运行在共享 Runtime 中，不绑定 Desktop Host。Host Election、Checkpoint 和 Resume 见 `runtime_host_and_checkpoint.md`，Growth 候选不能借迁移绕过 Sandbox、Benchmark、Review 或 Registry。

这些外部项未通过前，只能标记为 candidate/preflight/partial，不能显示为 ready 或 completed。
