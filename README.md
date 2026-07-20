# SpiritKin Architecture Audit Snapshot

This public repository is an architecture-review snapshot of SpiritKinAI for
GPT-5.5 and human reviewers. It contains the current runtime core, agent,
workflow, skill, worker, storage, API, configuration, client contracts, tests
and architecture documents.

## Audit Manifest

**Purpose:** Architecture review.

**Included:**

- Runtime
- Agent and AI Employee management surfaces
- Workflow
- Skill, Tool and Executor
- Worker and Remote Worker
- Storage, Context and Memory
- API and cross-device interfaces
- UI clients: Desktop, Web, iOS, Android bridge and browser extension
- Configuration, Deployment, Tests and Architecture Docs

**Excluded:**

- Models and model weights
- Large assets and generated media
- Secrets, credentials and signing material
- User data and runtime state
- Build output, caches, local toolchains and temporary repositories

Source snapshot: `KnightOfSky/SpiritKinAI` branch
`codex/project-ui-governance`, commit
`6d4cc5751b13ae5bfeb309fe4177f0c3be4cef91` (2026-07-20).

Start with [AUDIT_SCOPE.md](AUDIT_SCOPE.md), then read
[docs/repository-tree.md](docs/repository-tree.md) and
[docs/architecture.md](docs/architecture.md). The focused entry documents are
[runtime](docs/runtime.md), [workflow](docs/workflow.md),
[agent](docs/agent.md), [deployment](docs/deployment.md), and
[decisions](docs/decisions.md). Runtime data, models, build artifacts, large
visual assets, secrets and user data are intentionally excluded. The history
is squashed because this repository is a review baseline, not the development
source of truth.

The requested first pass is understanding only: establish the actual
architecture, compare implementation with design, and classify mature,
partial, and reserved surfaces before proposing changes.

---

# SpiritKinAI Project Overview

一个面向跨设备个人助理场景的多模态智能体集群项目：
- 语音输入：麦克风监听、热词唤醒、本地 ASR
- 视觉输入：摄像头手势/表情分析、截图 OCR、Qwen-VL 语义理解
- 智能编排：根据用户输入、会话摘要和视觉上下文做路由与回复
- 动作输出：TTS 播报、Live2D 情绪推送、PC/机械臂控制接口

## 当前项目结构

- `backend/main.py`：超薄入口，仅负责启动 runtime
- `backend/app/`：应用运行时与装配层
- `backend/orchestrator/`：智能体中枢层（cluster / planner / session_manager）
- `backend/memory/`：短期记忆与滚动摘要
- `backend/agents/`：专业智能体（base / 电商 / 游戏制作 / 编程 / 视频动画 / 视觉，共 6 个）
- `backend/tools/`：工具定义与注册表（能力目录，不直接绑设备）
- `backend/knowledge/`：知识库摄取、索引、检索骨架
- `backend/executors/`：执行控制层（把任务动作落到 OpenClaw/远端节点/软件执行面）
- `backend/perception/`：感知层（耳朵/眼睛：音频监听、热词、OCR、视觉理解）
- `backend/expression/`：表达层（嘴巴/脸：语音播报、情绪/Live2D）
- `backend/action/`：动作意图层（点击、输入、快捷键等高层动作）
- `backend/devices/`：设备驱动层，当前先提供本地 PC 设备实现
- `backend/services/`：外部服务与少量保留入口
- `backend/tests/unit/`：自动化单测
- `backend/tests/manual/`：人工/硬件验证脚本
- `docs/project_architecture_and_dev_log.md`：当前架构与开发演进记录
- `docs/ai_collaboration_context.md`：给 Codex、Claude Opus、GPT、Gemini、DeepSeek、Qwen 等模型共用的项目协作上下文
- `docs/tool_agent_kb_training_roadmap.md`：Tool / Agent / 知识库 / 训练路线
- `docs/remote_control_and_realtime_voice_plan.md`：实时语音、远程控制、LPM 风格演进方案
- `config/config.yaml`：项目配置
- `deploy/`：Docker 与部署配置

## 当前架构分工

- **大脑 / 中枢**：`orchestrator + memory`
  - `AgentCluster` 负责统一入口和执行协调
  - `Planner` 负责路由选择（工具 / 专业 agent / 通用回答）
  - `SessionManager` 负责近期历史和长上下文摘要注入
- **专业智能体**：`agents/`
  - 当前已落：`base` / `ecommerce`（电商）/ `game_development`（游戏制作）/ `programming`（编程）/ `video_animation`（视频动画）/ `vision`（视觉），共 6 个
  - 电商能力由 `ecommerce_projects.py`、`ecommerce_task_queue.py` 与 `browser-extension/pdd-product-extractor` 协作：手机回传 PDD 网页链接，登录态扩展生成 productData Artifact，agent 负责编排与审核
  - planner / retrieval / executor 的职责由 orchestrator 模块与执行层承担，后续可继续细分为独立 agent
- **耳朵 / 眼睛**：`perception/`
  - 音频监听、热词唤醒、本地语音识别
  - OCR、屏幕理解、手势分析
- **嘴巴 / 脸**：`expression/`
  - TTS 输出
  - Live2D/情绪表现
- **执行控制层**：`executors/`
  - 将高层动作翻译成对具体节点的可执行调用
  - 后续可挂 OpenClaw、远端 worker、软件自动化节点
- **手脚**：`action/ + devices/`
  - `action` 表示高层动作意图
  - `devices` 表示具体设备驱动适配
  - 这样后续接远端 PC、移动端、机械臂会更自然

## OpenClaw 接入建议

- 不建议把 OpenClaw 直接塞进当前 `local_pc` 那套屏幕/鼠标设备接口
- 更合理的方式是：
  - `backend/devices/openclaw.py`：封装 OpenClaw SDK/客户端
  - `backend/action/arm_operations.py`：暴露抓取、回零、移动等高层动作
  - `backend/executors/openclaw_executor.py`：作为软件控制层 / 执行面入口
  - `backend/services/openclaw.py`：对外统一导出入口
- 这样 PC 控制和机械臂控制不会继续混成一个设备协议
- 当前推荐接法：
  1. 先写一个 OpenClaw 客户端适配器，最少实现 `home()` / `move_to()` / `set_gripper()` 或 `open_gripper()` / `close_gripper()`
  2. 用 `create_openclaw_arm(client=...)` 或 `create_openclaw_arm(client_factory=...)` 包成 `OpenClawArm`
  3. 再由 `OpenClawExecutor` 把任务请求下发到物理机械臂或软件节点
  4. 业务里仍调用 `move_arm_to()`、`move_arm_home()`、`open_gripper()`、`close_gripper()` 这些高层动作
  5. 集群后续接执行链路时，优先接 executor，而不是硬塞进 `DeviceBackend`

- 一个最小接入形态大致是：
  - OpenClaw SDK/HTTP/WebSocket 客户端 -> `OpenClawArm` -> `arm_operations` -> `OpenClawExecutor` -> orchestrator

## 建议运行方式

1. 进入项目根目录
2. 创建环境：`conda env create -f environment.yml`
3. 激活环境：`conda activate spirit_kin_env`
4. 或使用 pip：`pip install -r requirements.txt`
5. 确认 llama.cpp 的对话服务 `http://127.0.0.1:8080/v1` 和向量服务 `http://127.0.0.1:8081/v1` 可用
6. 运行：`python -m backend.main`

本地模型默认由 llama.cpp 承载。桌面端启动后会按 `.env.example` 的路径自动启动两个 `llama-server` 实例；安装目录、模型覆盖变量、健康检查和手动启停方式见 [`docs/ops/llama_cpp_runtime.md`](docs/ops/llama_cpp_runtime.md)。LM Studio 仅保留为可选兼容 Provider，不再是默认运行时。

## 当前整理原则

- 优先统一 `backend.*` 导入路径
- 将“应用运行时 / 智能体中枢 / 感知 / 表达 / 动作意图 / 设备驱动”职责分开
- 避免模块导入时执行重型初始化
- 给语音、视觉、Live2D 通道增加基础异常兜底
- 为跨设备运行保留统一设备接口，先落本地实现、后接远端节点
- 逐步删除纯重复兼容壳，只保留确有迁移价值的对外入口
