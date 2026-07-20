# SpiritKinAI 落地与测试交接中心

本文是后续接手项目的第一入口。每轮开发先更新本文，再改代码。

## 1. 当前落地状态

| 模块 | 当前状态 | 可用性 | 下一步 |
| --- | --- | --- | --- |
| 桌面端 WPF | 已拆分瘦身，管理页较完整；Agent brain profile、Scheduler Benchmark、APK 审批、Android 验收工作流入口已补 | 可用但需真机验收 | 补真实设备/主流程验收 |
| Command Gateway | 可运行，支持 `/command` 和桌面管理接口 | 可用 | 增加更多端到端 smoke |
| Runtime / AgentCluster | 主链路可跑，确认门、Agent 容器 scope、brain profile、cloud planner gate 和事件可用 | 可用但需实测 | AgentAdapter 实装、route replay/audit UI |
| 模型管理 | Provider 同步、assist model、模型目录、scheduler benchmark 历史、WPF/Web/iOS Benchmark 入口和 brain replacement gate 已有 | 可用但需真模型实测 | 真模型 benchmark、score gate 与路由升权 |
| 知识库管理 | Agent/domain/global 元数据、目录打开、导入、索引统计；小型本地 embedding 索引可通过 `JsonVectorStore` 持久化 | 部分可用 | 生产级向量库/混合检索和后端路由深度验收 |
| 模型当前时间 | 已在 Runtime/AgentCluster 注入当前时间上下文 | 可用 | 桌面端展示当前上下文 |
| 云训练包 | 可导出自包含训练包 | 初步可用 | 增加上传脚本和回传注册 |
| 3D Avatar | 可加载 bangboo GLB，动作按钮和事件可用 | 可演示 | 继续精修真实模型骨骼 |
| Workflow 管理 | 定义/组合/运行/回放/归档/旧运行清理已有 | 可用但需实测 | 增加真实任务 replay/eval gate |
| Skill Registry | 候选/审核/导出已有 | 部分可用 | 接 replay gate 和正式升权 |
| Growth Runtime | `/desktop/growth` 与 `/ios/growth` 已提供 Capability Gap、Workflow Mining、Skill/Tool/Code/Model 候选、Builder Artifact、显式确认的静态沙箱预检、只读 Docker daemon 状态探测、审核和候选 Registry；Runtime trajectory 已接入重复失败/Workflow 观察；默认不安装、不写入生产代码、不自动激活；公共快照不暴露内部路径 | 候选与预检报告闭环可用；当前本机 Docker daemon 探测为 unavailable，状态已跨三端对齐 | 接入具备隔离权限的真实沙箱 benchmark、Code/Model Builder 报告和真实 Remote Worker/真机验收 |
| Resource Registry | JSON 持久化、AgentCluster runtime view、`/desktop/resource-registry` 元数据 CRUD 已有；只保存 credential reference，不保存密钥 | 部分可用 | 生产级资源 onboarding UI、凭证绑定策略和真实账号/店铺/浏览器 profile 盘点 |
| 移动端 Bridge | Android 被控端、iOS PWA/SwiftUI 主控、会话/领域/能力/Skill/Workflow/Resource/Ecommerce/监控/Growth API、移动安全摘要、Android WorkerPool 注册、APK promotion gate、Android lifecycle workflow 快捷入口已有 | 可用但需真机验收 | 真机闭环验收、HTTPS/WSS 自动化 |
| Worker Pool / Capability Runtime | 统一 Worker taxonomy 和 capability-based scheduler 已接入；WorkflowRunner 与 `workflow.graph.run_next/run_node` 已消费 schedule 决策；Android selected worker 可自动绑定在线 `device_id`；Browser selected worker 可输出并执行消费 `worker_binding`，local browser 路由到 `browser`，remote browser 路由到 `remote:<node_id>` / `remote_target=browser`；`BrowserWorkerExecutor` 已提供 opt-in JSON stdin/stdout 进程桥；Python/Git/FFmpeg/Service RAG 已提供默认 ready executor 闭环；Android Bridge/Remote Worker/OpenClaw/Browser Automation 可按职责映射到 Worker 类型 | 架构块可验收，生产能力需真机/实装补强 | 配真实 Playwright/浏览器 worker 命令并做环境验收、真机/生产环境验收 |
| 远端 worker | HTTP 最小版已有，定位为 Generic Remote Worker / Remote Runtime Worker；本机独立端口已通过鉴权心跳与只读 `local_pc` 执行 smoke | 本地软件闭环可用，真实远端仍需验收 | 实机部署、鉴权、日志、Workspace 绑定与生产网络 |
| OpenClaw | executor、状态事件、本地 JSON 状态和可配置 HTTP controller transport 已有，定位为 Desktop Device Worker | 软件闭环可用，真机未验收 | 配置 `SPIRITKIN_OPENCLAW_HTTP_BASE_URL` 后做真机动作验收 |
| Paper/Video-to-Skills | 目前是计划 | 不可用 | 先做草稿生成和 dry-run |

## 1.1 Worker 命名与架构验收口径

当前方向按“能力和职责”验收，不再只按底层实现名验收：

| 旧名称 | 新定位 |
| --- | --- |
| Android Bridge | Android Device Worker |
| OpenClaw | Desktop Device Worker |
| Remote Worker | Generic Remote Worker / Remote Runtime Worker |
| Browser Automation | Browser Worker |
| ADB | Android Worker Capability |
| Playwright | Browser Worker Capability |
| FFmpeg | Media / Execution Worker |
| Python Runtime | Python / Execution Worker |

这不是重构目标，而是现有结构的自然演化层。现有执行路径仍保持：

```text
Agent / Skill
  -> Tool / Capability
  -> Review / Permission / Confirmation Gate
  -> WorkerPool
  -> Device Worker / Browser Worker / Execution Worker / Service Worker / Remote Worker
```

验收时检查：

- `runtime.capabilities.tooling.worker_pool.taxonomy` 存在。
- `WorkerPool.schedule()` 能按 `needs`、`worker_type`、`workspace`、`prefer_remote` 给出 selected/candidates/rejected 和 matched/missing needs。
- Android worker 的 `worker_type=device_worker`，`worker_subtype=android_device_worker`。
- Android worker 的 `legacy_names` 包含 `Android Bridge` 和 `ADB`。
- Remote Node heartbeat 可以通过 `NodeRegistry.worker_descriptors()` 进入 WorkerPool，并被 `needs: browser` / `prefer_remote` 选中。
- `WorkflowRunner` 和 `workflow.graph.run_next/run_node` 已可写入 `worker_schedule`；注入 WorkerPool 时缺 Worker 会阻断，未注入时保持兼容并记录 `worker_pool_not_configured`。
- Android lifecycle workflow 的 `workflow.android_step` 节点包含 `needs`，运行输出包含 `worker_requirement`、`worker_schedule` 和 `device_selection`；未显式传 `device_id` 时会优先绑定 selected Android worker 的在线设备，注入 WorkerPool 且未配对/无可用 Android Device Worker 时会阻断。
- Browser-capable `tool_call` 节点运行输出包含 `worker_binding`；local browser 绑定为 `execution_target=browser`，remote browser 绑定为 `execution_target=remote:<node_id>`，并会透传进工具参数。
- `ExecutionTool` 已消费 browser `worker_binding`：local browser 执行请求目标为 `browser`，remote browser 执行请求目标为 `remote:<node_id>`，并附带 `remote_target=browser`；能力图和确认门仍按原 browser tool 风险/能力记录解析。
- `browser.worker_health` / `browser.worker_open_url` / `browser.worker_search`
  通过 `BrowserWorkerExecutor` 进入 WorkerPool；未配置
  `SPIRITKIN_BROWSER_WORKER_COMMAND` 时不会作为 ready Browser Worker 出现在默认
  AgentCluster 中，避免把未部署进程误报为可执行能力。
- `WorkflowRunner` 的 tool/skill 节点现在会把成功/失败结果写入 `state/evolution/trajectories.jsonl`，并在节点输出 metadata 中附带 `trajectory_record`，用于 eval case 和训练数据导出；`/desktop/evolution` 可把失败轨迹派生的 eval case 导出为 `state/evolution/eval_cases.jsonl`，训练 dataset card 会关联该 eval report 路径。
- 协作 route bus 的终态 worker event 会写入同一 trajectory log，source 为 `collaboration.worker_event`；stdout/stderr/token stream 仍只作为 UI/work event 展示，不进入训练数据。
- Android command result 会写入 `android.command_result`，RemoteWorker execute/package/rollback 结果会写入 `remote.worker_result`；真机/生产环境仍需验收。
- taxonomy 的 `legacy_positioning` 包含 Android Bridge、OpenClaw、Remote Worker、Browser Automation、ADB、Playwright、FFmpeg、Python Runtime 的映射。
- 仍需注意：Browser binding 已进入执行请求和 RemoteExecutor 路由，Browser worker
  进程协议已有单测闭环，Python/Git/FFmpeg/Service RAG 已有默认 ready executor
  闭环；真实 Playwright/浏览器 profile、移动端、OpenClaw 和远端节点仍需按业务
  逐个做生产环境验收。

## 2. 启动方式

本机桌面控制台：

```powershell
python scripts/start_desktop_console.py
```

或单独启动实时面板：

```powershell
python scripts/start_realtime_panel.py
```

常用地址：

- Frontend: `http://127.0.0.1:8787/desktop_console.html`
- 3D Avatar: `http://127.0.0.1:8787/avatar_3d.html?config=models/spirit3d/manifest.json`
- Command API: `http://127.0.0.1:8788/command`
- Event WS: `ws://127.0.0.1:8765`

## 3. 新增管理接口

### 模型目录

读取本地模型目录：

```http
GET /desktop/model-catalog
```

联网刷新 Hugging Face 模型信息并写入 `state/model_catalog.json`：

```http
POST /desktop/model-catalog
{
  "action": "refresh",
  "model_ids": ["Qwen/Qwen3-VL-8B-Instruct"]
}
```

说明：

- 这解决的是“基底模型信息过期”的问题。
- 这不解决“今天几号/现在几点”问题；当前时间已由 Runtime 注入 LLM 上下文。
- 桌面端无网时使用 bundled catalog，不阻塞系统。

### 训练数据

从文档生成训练集：

```http
POST /training/dataset
{
  "documents": [{"path": "docs/demo.md", "text": "内容"}],
  "output_path": "state/training/demo.jsonl",
  "base_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct"
}
```

### 云训练包

导出可上传 GPU 主机的包：

```http
POST /training/cloud-package
{
  "dataset_path": "state/training/demo.jsonl",
  "base_model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
  "package_id": "coder-router-lora-001"
}
```

输出目录默认在：

```text
state/cloud_training_packages/<package_id>/
```

里面包含：

- `train.jsonl`
- `manifest.json`
- `README.md`
- Unsloth LoRA/QLoRA 命令

## 4. 云训练落地流程

推荐先 LoRA/QLoRA，不做全参微调，更不要从零训练基底模型。

流程：

```text
本地收集任务轨迹/失败样本/文档
  -> 导出 JSONL
  -> 导出 cloud training package
  -> 上传租卡 GPU 主机
  -> 安装 unsloth / trl / datasets
  -> 运行 manifest 里的 command
  -> 下载 adapter 输出
  -> 本地注册 LoRA adapter
  -> 跑 eval gate
  -> 通过后启用到指定 Agent
```

训练优先级：

1. Router / Intent：工具路由、ASR 纠错、参数补全。
2. Programming Agent：代码修复、测试、PR 输出格式。当前已接入只读 GitWorker
   工作区上下文（`git.status` / 可选 `git.diff`），但真实编辑、提交、PR
   仍必须走工具和 review gate。
3. Ecommerce Agent：电商术语、表格、投放复盘。
4. 个人风格：回答格式和长期偏好。

不要把事实更新全靠训练解决。事实、模型信息、软件清单、项目状态优先走：

```text
联网刷新 / RAG / 本地状态 / Workflow Memory
```

## 5. 必跑测试清单

代码改动后至少跑：

```powershell
python -m py_compile backend\app\command_gateway.py backend\app\runtime.py backend\orchestrator\agent_cluster.py
python -m unittest backend.tests.unit.test_command_gateway backend.tests.unit.test_runtime backend.tests.unit.test_training_workbench -v
dotnet build D:\SpiritKinAI\desktop\SpiritKinDesktop\SpiritKinDesktop.csproj
```

Agent 架构相关：

```powershell
python -m unittest backend.tests.unit.test_architecture_layers -v
python -m unittest backend.tests.unit.test_agent_cluster.AgentClusterTests.test_programming_agent_uses_managed_model_policy_and_metadata -v
python -m unittest backend.tests.unit.test_agent_cluster.AgentClusterTests.test_programming_agent_injects_git_workspace_context -v
```

Worker / Capability Runtime 相关：

```powershell
python -m unittest backend.tests.unit.test_architecture_layers.ArchitectureLayerTests.test_worker_pool_taxonomy_maps_old_runtime_names_to_worker_responsibilities -v
python -m unittest backend.tests.unit.test_architecture_layers.ArchitectureLayerTests.test_worker_pool_scheduler_selects_by_capability_needs_health_and_queue -v
python -m unittest backend.tests.unit.test_workflow_graph.WorkflowGraphTests.test_workflow_graph_tool_uses_injected_worker_pool_for_run_next -v
python -m unittest backend.tests.unit.test_workflow_graph.WorkflowGraphTests.test_workflow_graph_tool_binds_remote_browser_worker_for_tool_call -v
python -m unittest backend.tests.unit.test_workflow_graph.WorkflowGraphTests.test_android_workflow_step_binds_default_device_from_scheduled_worker -v
python -m unittest backend.tests.unit.test_browser_worker_executor -v
python -m unittest backend.tests.unit.test_tooling_and_remote -v
python -m unittest backend.tests.unit.test_remote_worker -v
```

确认门相关：

```powershell
python -m unittest backend.tests.unit.test_agent_cluster.AgentClusterTests.test_confirmation_control_rejects_mismatched_pending_context backend.tests.unit.test_agent_cluster.AgentClusterTests.test_confirmation_control_blocks_duplicate_confirmation_reply -v
```

3D Avatar 相关：

```powershell
python tmp\verify_avatar3d_voice_motion.py
```

## 6. 实机验收清单

### 桌面文本

- 发送“现在几点”，回复必须使用当前真实日期时间，不得回答训练截止日期。
- 发送“打开飞书”，应路由到 app launch 或明确缺少软件。
- 发送“关闭火豹浏览器”，必须弹确认。
- 点击确认按钮，只执行一次，不再重复弹确认。

### 3D Avatar

- 普通回复不晃动。
- 点头、摇头、挥手、前进、后退、左移、右移按钮都有动作状态。
- `assistant.message` 无 explicit action 时保持 `motion: idle`。

### 模型目录

- 无网时 `GET /desktop/model-catalog` 返回 bundled catalog。
- 有网时 `POST /desktop/model-catalog` 能写入 `state/model_catalog.json`。
- Runtime capabilities 能看到 `model_catalog.model_count`。
- bundled catalog 必须是混合矩阵，不允许只推荐 Qwen。

### 知识库管理

- Agent 管理页进入“知识库”。
- 新增/选择知识库后点“导入”，选择 md/txt/json/yaml/csv 等文本文件。
- 文件应复制到对应 `state/knowledge_bases/...` 目录。
- 点“索引”后应生成 `.spiritkin_kb_index.json`，并显示文档数量。
- 对应 Agent 下次被路由时，AgentCluster 会按知识库路径检索并注入 prompt。

### 云训练包

- 先调用 `/training/dataset` 生成 JSONL。
- 再调用 `/training/cloud-package` 生成包。
- 包内 `manifest.json` 的 command 可在 GPU 机器工作目录运行。

### 远端/手机

- LAN 模式手机能打开前端。
- Token 开启时公网请求未授权必须 401。
- 高风险命令仍需要确认。
- iOS 终端临时 Web/PWA 入口能打开 `/ios/terminal`，能刷新 `/ios/control/snapshot`，能保存组合工作流、启动运行、上传图片到 artifact store。
- iOS PWA 顶部能直接执行 `批准 APK`、`启动验收`、`Benchmark`；Android 命令下拉包含 `android.ui_snapshot`、`android.screenshot.request_permission`、`pdd.launch`、`pdd.share_image`、`pdd.create_listing`。
- iOS 原生 App 后续验收：`ios/SpiritKinTerminal/` 已有 SwiftUI 源码骨架和 XcodeGen 配置；原生端已接入会话、领域/能力开关、Skill/Workflow/Resource CRUD、Workspace/Remote Worker 监控与低风险自愈、Ecommerce/Growth API、照片头像和非对话 Avatar 暂停；仍需要 macOS/Xcode/签名环境编译安装，并验证 Base URL/token、真机网络、通知/后台刷新边界。
- Android Bridge 真机验收：先确认 `/android/apk/manifest` 的 `promotion_gate.status=approved` 且 `serving_allowed=true`。当前批准版本是 APK `2026.06.25.4`，`sha256=1b29c93c66e4d643674fcac1c87870ad7262b30467da2a8537f58b35647d6992`。安装后完成 workspace pairing，开启 heartbeat/command sync，桌面/iOS 投递 `app.launch`、`url.open`、`clipboard.write`、`android.ui_snapshot`、`android.screenshot.request_permission`、`pdd.launch`，以及带 `target_operation` 的 `workflow.android_step` wrapper，设备回传 `completed`/`failed` command result。
- Android lifecycle workflow 验收：保存/启动 `android.command_lifecycle_acceptance.v1`，检查 `workflow.android_step` 节点输出包含 `lifecycle_acceptance`，且真机命令具备 queued/delivered/completed 时间戳证据。
- 端到端延后验收：iPhone 创建组合工作流 -> 桌面保存并启动 run -> run 中的 Android 步骤投递命令 -> Android Bridge 执行并回传结果 -> 桌面/iOS 可见状态和 artifact。

## 7. 功能分级

### 可作为用户可见能力

- 本地/桌面命令
- 3D Avatar 展示
- 确认门
- 模型/Provider 管理
- 训练集导出
- 云训练包导出

### 可作为工程预览

- Skill 审核/导出
- 远端 worker
- Workflow Memory 候选 Skill
- 多模型评审
- 知识库候选自动沉淀
- Harness replay 自动生成候选 Skill / eval case

### 暂不宣称可用

- 完全自动自我进化
- Paper2Agent 自动上线
- Video-to-Skills 自动上线
- 知识库无人审自动覆盖生产
- AutoGLM/LangManus 完整接管电脑或手机
- OpenClaw 真机复杂动作

## 8. 下一轮开发入口

优先顺序：

1. 桌面端增加模型目录页：刷新、查看、选择推荐基底模型。
2. 桌面端增加云训练包按钮：从现有训练集一键导出。
3. 为 `AgentAdapter` 增加 LangGraph/CrewAI/Codex/remote worker 具体实现。
4. 补后端 `/desktop/knowledge-base` 路由和持久向量索引。
5. 增加 LoRA adapter 注册表。
6. 做一次端到端实机验收，按本文第 6 节打勾；当前 mobile/workflow 真机闭环已明确延后，不要仅凭 mock/unit test 宣称生产可用。
7. 在 macOS 上生成并实机验证 `ios/SpiritKinTerminal` 原生控制终端；当前源码已复用 authenticated HTTP API，编译、签名、通知和后台刷新验收待做。
