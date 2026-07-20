# 电商多租户 SaaS 基座实施方案

> 方案制定：Claude（Fable 5，2026-07-04）。执行：GPT。每切片提交后由 Claude 审查 diff + 验收。
> 配套背景见 `docs/ai_collaboration_context.md`、`docs/mobile_link_bridge.md`、`docs/light_cloud_control_plane.md`。
> 本方案取代同日的截断草案 `docs/archive/multi_tenant_account_plan.md`，其独有设计已合并至「数据模型与 token 设计」一节。
> 本方案是把"自己用"升级为"可收费多租户"的基座。电商采集/图片/上架流水线（换 IP、抖店上架、图片处理）是骑在这个基座上的**独立后续 track**，本方案只负责地基，不含流水线实现。

## 现状事实（2026-07-04 调研核实）

- **workspace 已是隔离单位，但其上没有账户层**。`scripts/control_plane_store.py:3916 _new_workspace` 只有 `workspace_id/name/status/created_at/updated_at/allowed_domain/execution_policy/runtime_profile/artifact_policy`——没有 `owner/account` 字段。grep `account_id/tenant_id/owner_id/max_workspaces` 全空。计费主体（一个用户拥有多个 workspace）**不存在**。
- **配额只有存储维度**。`default_artifact_policy`（`:385`）有 `max_workspace_bytes/max_workspace_artifacts/max_file_bytes`，经 `_assert_artifact_quota_available`（`:3519`）强制。`default_execution_policy`（`:318`）有单次运行预算 `max_runtime_seconds/max_artifacts/max_android_commands/max_retries`。**没有** worker 数量上限，**没有**抓取数量配额/计数器/周期重置。
- **状态迁移框架现成**。`_state_version` + `_migrate_state`（`:3928` 起）+ `STATE_VERSION` 常量，迁移按 `previous_version` 递增追加、记录到 `state["schema"]["migrations"]`。新增账户层就是加一个迁移步骤，**不需要删库重传**。
- **配额调整模式现成**。`update_workspace_policy`（`:675`）= 载入→白名单键归一化→校验→写回→`_append_event`→save。新配额照抄这套三段式。
- **worker 分发/更新全套现成**（`scripts/control_plane_worker.py`）：`build_worker_package`（`:465`，打 zip 含 `setup-worker.ps1`/`install-worker-gui.ps1`/`update-worker.ps1`/`run-worker.cmd`/计划任务安装器/签名 manifest）、`worker_install_gui_ps1`（`:352`，WinForms 引导：Server URL/Workspace/Worker ID/pairing token/安装目录/计划任务）、`check_and_apply_update`（`:594`，比对 manifest → 校验 SHA-256 → 覆盖安装 → 记录 → 退出待重启）。control plane 提供 `GET /worker/package/manifest` + `GET /worker/package`。
- **配对已按 device_role 绑死 workspace**。`create_pairing_token`（`:908`）+ `bind_device`（`:1215`）+ 角色不符 `PermissionError`（`:1236`）。`remote_worker` token 覆盖调用方 workspace/worker，越不了权。
- **管理面双入口**：Web `GET /ios/terminal`（`scripts/mobile_link_receiver.py` 提供，`POST /ios/control/action` 派发动作）+ 桌面 WPF（`backend/app/mobile_management.py` → 桌面 `MobileManagementController`）。二者共用 control plane 动作。
- **凭据现状**：SpiritKin 侧尚无任何 PDD/抖店登录态处理代码（采集流水线还没落地），所以"凭据不出本机"是**从零建立的设计约束**，不是要拆除已有的集中存储——现在就把红线钉死，成本最低。

## 目标与非目标

**目标**：让 control plane 支持"一个账户 = 一个用户 = 多个 workspace（其店铺群）"，账户级配额可调且可扩展（本轮落 workspace 数 / worker 数 / 抓取数三个收费点，架构上留口给后续收费点）；用户能在管理界面自助管理自己 workspace 下的资源；worker 跑在用户本机、登录凭据永不上云且有强制校验；worker 一键安装 + 全部相关更新经云端下发。

**非目标（明确不做）**：
- 不定价、不接支付网关。只做"配额可调 + 用量计量 + 超限拒绝"的闸门，价格/套餐由运营在管理端填数。
- 不实现电商采集/图片/上架流水线本身（那是后续 track，且换 IP 的代理源尚待用户决定）。
- 不做集中式凭据托管（明确拒绝这条路线，见硬性规则 #2）。
- 不改 Caddy/MinIO/compose 拓扑，不引入新数据库（沿用 JSON 状态 + 迁移框架）。

## 硬性规则

1. **状态向后兼容**。所有新字段经 `_migrate_state` 从旧 `control_state.json` 迁移，加载旧状态不得报错、不得丢用户配对。每加一个迁移 bump `STATE_VERSION` 并在 `migrations` 留名。验收会拿"旧版 state 文件 → 新代码加载"做回归。
2. **凭据不出本机是红线，且要可强制**。PDD/抖店登录 cookie、浏览器 profile、账号密码**永不**出现在 `/worker/result`、`/android/*`、任何 artifact、任何 control plane 状态里。落三道闸：(a) worker 结果 payload 走**字段白名单**（只允许 productData/artifact 引用/状态码，未知敏感键直接不发）；(b) control plane 入站再加**拒绝过滤**（payload 含 `cookie/session/password/token`(非配对token)/`profile_path` 形状的键即 400，防御纵深）；(c) 一个契约测试断言 worker 结果模型不含凭据键。红线写进 `docs/mobile_link_bridge.md` 的 Artifact access boundary 段。
3. **配额设计可扩展**。不要硬编码三个字段。账户挂一个 `plan.quotas` dict + `usage` dict + `period`（计费周期起止），新收费点 = quotas 加一个键 + 一处 `_assert_*` 强制点，不改数据结构。照抄 `update_workspace_policy` 的白名单归一化模式。
4. **每切片一轮验证后提交**：后端 `pytest`（新增用例必过，基线 1215 不回归）+ `ruff`；桌面切片 `dotnet build/test`（基线 0 警告、110 测试不回归）。一切片一提交，message 说明内容。
5. **动作走既有派发**。管理动作全部经 `POST /ios/control/action` + 桌面 `mobile_management.py`，不新开鉴权面。账户级运营动作需 management token（owner 角色）；用户自助走新增的 `account_console` token（见下节）；workspace 级自助动作也可由该 workspace 绑定的 `ios_terminal` token 在自己作用域内执行。
6. **更新经云端**。凡"用户侧要升级的东西"（worker 包、APK、管理页）都走已有下发通道（`/worker/package`、`/android/apk`、control plane 直接 serve 的页面），不靠手动拷贝。
7. **双网关同步**。`scripts/mobile_link_receiver.py`（云端 Docker 部署面）与 `backend/mobile/link_receiver.py`（app 侧镜像面）路由改动必须两处检查同步，任一侧漏改即验收不过。

## 数据模型与 token 设计

### accounts（新增 state 顶层键）

```json
"accounts": {
  "<account_id>": {
    "account_id": "acct-xxxx",
    "name": "用户可读名",
    "status": "active | disabled",
    "created_at": "...", "updated_at": "...",
    "plan": {
      "tier": "default",
      "quotas": { "max_workspaces": 1, "max_workers": 1, "max_scrapes_per_period": 500, "scrape_period_days": 30 },
      "usage": { "scrapes_this_period": 0 },
      "period_start": "...", "period_end": "..."
    },
    "workspace_ids": [],
    "notes": ""
  }
}
```

- `quotas` 走 `default_account_quotas() → normalize_account_quotas() → _assert_*` 三段式（照抄 `artifact_policy` 模式）。**`0` 或缺省 = 不限**（与现有 quota 语义一致）；迁移生成的默认 `owner` 账户 quotas 全 0，保证现有行为零变化。
- `normalize_account_quotas` 对未知 key **保留透传**——未来收费点（如 `max_android_devices`）只加 key + 对应 assert 点即生效，不改数据结构。
- workspace 增加 `account_id` 字段（`_new_workspace` 补参数，默认归 `owner` 账户）。

### 抓取计量钩子

- 新增 `consume_metered_quota(account_id, kind="scrape", amount=1)`：惰性周期重置（检查时 `period_end` 已过则 `usage` 归零并推进周期）、超限抛 `PermissionError`、递增写 state 并 `_append_event("account_quota_consumed", ...)`。
- 挂载点：`start_workflow_run` 对**标记了 `metered: "scrape"` 的工作流模板**调用（`built_in_workflow_templates` 给电商模板加标记）；同时导出该函数供后续采集流水线 track 直接调用。

### 账户 token（用户自助控制台登录）

- 新增 `device_role: "account_console"`，复用现有 pairing 机制：management 端为账户签发，token 绑 `account_id`（而非单 workspace）。
- 网关 gating：`account_console` token 只能访问自助视图页与自助动作，动作范围限自己账户下的 workspace；**不能**调管理动作、不能跨账户、不能签发其他 token。越权拒绝写法参照现有 `ios_terminal` token 的 workspace 越权拒绝。
- 账户 `status: disabled` = 冻结该账户全部配对生成与工作流启动，不销毁数据。

## 切片顺序

| 切片 | 内容 | 规模 | 交付物 |
|---|---|---|---|
| 0 | **账户实体 + 迁移**。`control_plane_store` 加 `accounts` 顶层集合：`account_id/name/status/created_at/plan{tier,quotas,usage,period_start,period_end}/workspace_ids[]`。workspace 加 `account_id` 反向引用。`_migrate_state` 加一步：把现存 workspace 归到一个默认 `owner` 账户（保证旧状态平滑升级）。bump `STATE_VERSION`。加 `create_account/get_account/list_accounts/assign_workspace_to_account`。 | 中 | 账户 CRUD + 迁移单测（旧 state→新 state 断言） |
| 1 | **三配额定义 + 计量 + 强制**。`default_account_quotas()` = `{max_workspaces, max_workers, max_scrapes_per_period, scrape_period_days}`（默认宽松，0=不限）。强制点：`ensure_workspace`/开新 workspace 时校验 `max_workspaces`（统一收口 `_assert_can_create_workspace`）；worker `bind_device`（role=remote_worker）成功绑定前校验该账户活跃 worker 数 ≤ `max_workers`；抓取走 `consume_metered_quota`（见「抓取计量钩子」，含惰性周期重置 + 超限拒绝）。 | 中 | 三个 `_assert_quota` + 计量单测（含周期重置、超限拒绝、0=不限、disabled 账户冻结） |
| 2 | **配额可调动作**。加 `update_account_plan(account_id, quotas)`（白名单归一化 + 未知 key 透传，仿 `update_workspace_policy`）、`set_account_status`（disable 冻结）、`list_accounts`（含用量汇总），仅 management token 可调，全部 `_append_event` 审计。`/ios/control/action` + `mobile_management.py` 暴露 `update_account_plan`/`get_account_usage`。用量快照（配额 vs 已用）进 snapshot 输出。 | 小 | 运营改配额/冻结动作 + 用量查询 |
| 3 | **凭据红线三道闸**。定义 worker 结果字段白名单模型；control plane 入站敏感键拒绝过滤；契约测试。更新 `docs/mobile_link_bridge.md` Artifact access boundary 段写明红线。 | 中 | 白名单+拒绝过滤+契约测试 |
| 4 | **用户自助管理面**。新增 `account_console` token（见「账户 token」）+ Web `/ios/terminal` 加"我的账户"视图：账户下 workspace 列表、每个 workspace 的店铺群/worker/artifact/用量（配额进度条）、绑定/解绑自己的 worker、配自己的代理（代理值只写到发给自己 worker 的任务里、不落库明文——见红线）。桌面 `MobileManagementController` 同步同一视图（分工里 UI 美化归我，GPT 先把数据接线做通）。越权用例必测：跨账户、调管理动作、签发其他 token 全拒。 | 大 | account_console token + Web/桌面自助视图（读为主 + 绑定/解绑/配代理写操作）+ 越权测试 |
| 5 | **worker 一键安装包增强**。`build_worker_package`/`worker_install_gui_ps1` 引导里加：账户/workspace 选择、pairing token、代理配置、"登录你自己的 PDD/抖店（浏览器 profile 留本机）"引导文案。装完自动绑定 + 起后台。 | 中 | 增强 GUI 安装器 + 引导文档 |
| 6 | **全链路云端更新**。确认 worker 自更新（`check_and_apply_update`）覆盖新版包；APK 走既有促进门；管理页由 control plane 直接 serve 保证改完即生效。补一份"发新版 = bump 版本 + build + 审批促进"的运维清单到 `docs/`。 | 小 | 更新通道贯通 + 运维清单 |

## 当前执行进度（Codex, 2026-07-04）

- **切片 0 已落地**：`accounts` 顶层集合、默认 `owner` 迁移、workspace `account_id`、账户 CRUD、workspace 归属调整、snapshot 账户视图、迁移单测已补。
- **切片 1 已落地**：账户 quotas/usage/period 模型、workspace 数/remote worker 数/scrape 计量与周期重置、disabled 账户冻结、run 记录冗余 `account_id`、模板 `metered` 标记已补。
- **切片 2 已落地**：`update_account_plan`、`set_account_status`、`get_account_usage`/`list_accounts` 管理动作，`/ios/control/action`、`backend/app/mobile_management.py`、`backend/mobile/ios_endpoint.py` 白名单已接线，`account_console` 读自己账户用量的 scope 测试已补。
- **切片 3 已落地**：`backend/security/sensitive_payload.py` 统一入站敏感键拒绝，`worker_result`/Android heartbeat/artifact 上传/`MobileArtifactStore` 均接入；`scripts/control_plane_worker.py` 出站 result 白名单 + 嵌套敏感键 redaction 已补；`docs/mobile_link_bridge.md` Artifact access boundary 已写红线。
- **切片 4 已落地**：`account_console` token 走既有 pairing 机制并按账户 scope 过滤 snapshot/action/pairing；Web `/ios/terminal` 新增"我的账户"视图，显示账户 workspace、worker、artifact/用量与自助绑定 Worker 操作；桌面 `MobileManagementController` 透传并渲染账户自助摘要；`workflow.graph.*` 对 account_console 显式拒绝并有越权测试。
- **切片 5 已落地**：Worker 包示例配置、`setup-worker.ps1`、`install-worker-gui.ps1` 增加 account/workspace/pairing token/local proxy 引导；worker heartbeat 只暴露 `proxy_configured`，不回传代理 URL；CLI/LangGraph/CrewAI 子进程任务在本机环境注入代理变量。
- **切片 6 已落地（文档/通道层）**：`docs/cloud_update_release_checklist.md` 记录"发新版 = bump 版本 + build + 审批促进 + up -d --build"清单，覆盖 worker 自更新、APK promotion gate、管理页 control-plane 直接 serve、重部署保留账户/配对/用量。本机 Docker owner-only 云控 smoke 已跑通：`docker compose --env-file .env.cloud up -d --build` 后 control-plane healthy，`/android/health`、`/ios/terminal`、`/ios/control`、`/worker/package/manifest` 可访问，account/workspace/remote-worker pairing/heartbeat 通过，重复 `up -d --build` 后账户、workspace、worker 记录仍保留。
- **本轮验证（Codex, 2026-07-04）**：`python -m unittest tests.test_control_plane_worker tests.test_mobile_link_receiver tests.test_control_plane_store -v` -> 158 passed；`python -m py_compile backend/app/mobile_management.py scripts/control_plane_worker.py scripts/mobile_link_receiver.py scripts/control_plane_store.py` -> passed；`dotnet build SpiritKinAI.sln -c Release --no-restore` -> 0 warnings/0 errors；`dotnet test SpiritKinAI.sln -c Release --no-build` -> 112 passed；`python -m ruff check .` -> passed；`python -m pytest -q` -> 1236 passed, 8 subtests passed；Docker 本机云控 smoke -> passed。
- **仍未声明通过的边界**：公网真实云 VM 的 DNS/TLS/firewall smoke、真实 Android 设备桥接验收、原生 iOS app 编译/签名/真机运行仍需 owner 在对应环境执行并记录结果。2026-07-05 已补脚本版云控 PWA 静态资产路由：`/ios/terminal.webmanifest`、`/ios/service-worker.js`、`/ios/icon.svg`、touch icon 在本机 Docker control-plane 重建后均返回 200，并由 `tests.test_mobile_link_receiver` 覆盖。

## ⚠️ 增补（2026-07-04 追加，GPT 执行切片时必须带上）

> 以下 4 条是方案定稿后经工作流/蓝图审查追加的，**不是可选项**。前 3 条并入对应切片的验收口径。

1. **run 记录写 `account_id`**（并入切片 0/1）：`start_workflow_run` 产出的 run 记录除 `workspace_id` 外冗余存 `account_id`，计费查询不走反查。
2. **统一工作流模板 schema**（并入切片 1）：`built_in_workflow_templates` 的 4 个模板补齐统一必填键 `input_schema` / `metered` / `category`（现状电商模板缺 `input_schema`），加 `metered` 标记时不做特判。
3. **蓝图流暂不对租户开放**（并入切片 4）：`backend/orchestrator/workflow_graph.py` 的蓝图流（`workflow.graph.start_run` 等）不经控制面、会绕过抓取计量。在蓝图↔控制面桥接完成前：`account_console` token 的动作白名单**不得**包含任何 `workflow.graph.*` 动作（网关校验 + 越权测试各一条），并在本文档与 `docs/ecommerce_blueprint_workflow_plan.md` 各留一句声明。
4. **后续流水线 track 的前置项（本方案不做，记录路线）**：蓝图将是面向用户的多工作流引擎，流水线 track 开工前必须先完成：
   - **蓝图↔控制面桥接**：蓝图节点经控制面派发 worker_task（计费/监控/执行同一条路），届时计量收口从模板流上移到桥接层。
   - **数据引脚连线**（2026-07-04 读码核实的引擎缺陷，`backend/orchestrator/workflow_graph.py`）：参数模板 `{{var}}` 目前只从 run 级全局 inputs 取值（`:421`/`:883`），上游节点 outputs 流不进下游参数——需支持节点输出→下游输入的数据流（如 `{{node_id.outputs.key}}` 语法或节点完成时回写共享黑板）。
   - **必填引脚启动校验**：节点声明的 `inputs=[{required: True}]` 目前 `validate()`（`:200-228`）不检查，缺参数静默解析为 None 跑到中途才炸——需在 start_run 时校验必填引脚齐备、模板变量缺失即拒绝启动。
   - **branch 最小表达式**：条件目前只有字符串 truthy 判断（`:851-870`），需支持比较运算（如 `gt/lt/eq` 结构化条件），做不到"价格>50 走 A 支路"。
   - **节点调度器**：agent 任务节点靠外部认领、无人认领永远挂起——需自动推进器（派发+认领超时+重派）。



- **迁移安全**：造一个旧版（无 accounts 字段）`control_state.json`，新代码加载后 accounts/usage 齐备、旧 workspace 归属默认账户、无配对丢失。
- **配额强制**：三个收费点各写一条"超限被拒"用例；抓取计数跨周期重置一条用例。
- **凭据红线**：契约测试 + 手工构造含 `cookie` 键的 worker result payload 应被 400 拒。
- **不回归**：后端 pytest 1215 基线、桌面 110 测试基线、0 新增警告。
- **端到端冒烟**：起云端 compose（不加 `-v`）→ 建账户 → 建 workspace → 装 worker 绑定 → 改配额 → 用量可见 → `up -d --build` 重部署后配对/账户/用量全部保留。
