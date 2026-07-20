# 多租户账户层与配额收费点方案（账户 / 配额 / 用户自助控制台 / 凭据不出本机 / worker 一键安装）

> **已归档（2026-07-04）**：本文档为截断草案（切片表写到一半中止，正文引用的「控制台功能清单」「凭据不出本机」「一键安装」章节不存在）。独有设计已合并进 `docs/ecommerce_saas_foundation_plan.md`「数据模型与 token 设计」一节，执行以该方案为准。
> 方案制定：Claude（2026-07-04）。执行：GPT。完成后由 Claude 验收。
> 配套背景见 `docs/ai_collaboration_context.md` 与 `docs/mobile_link_bridge.md`。

## 商业背景（为什么做）

- 平台将开放给外部用户，每个用户有自己的店铺群。PDD/抖店登录由用户自己管理（凭据留在用户机器），平台方只发放/管理 Bridge 手机配对码与执行编排。
- 收费点是配额：**workspace 数量、worker 数量、抓取数量**。具体价格未定，因此本方案只做「可调配额闸门 + 用量计量」，**不做任何计费/支付集成**。后续还会有其他收费点，配额模型必须可扩展（加一个 key 就能变成新闸门）。

## 现状事实（2026-07-04 调研核实，均已读码确认）

- `scripts/control_plane_store.py` 是控制面状态核心：
  - workspace 是当前最高隔离单位（`DEFAULT_WORKSPACE_ID` :52，`_new_workspace` :3916 含 `execution_policy`/`runtime_profile`/`artifact_policy`）。**workspace 之上没有账户/租户实体**（grep `account_id|tenant_id|max_workspaces|owner_id` 为空）。
  - 配额三段式模式已存在且可照抄：`default_artifact_policy` :385 → `normalize_artifact_policy` :407 → `_assert_artifact_quota_available` :3519（超限抛 `PermissionError`，含清晰错误文本）。
  - **没有** worker 数量上限、**没有**抓取计数/周期配额。
  - 配对 token 按 `device_role` 区分（`android_bridge`/`ios_terminal`/`remote_worker`），`bind_device` :1215 角色不符抛 `PermissionError`（:1236-1240）。新增角色 = 新字符串 + 网关侧 gating，store 天然支持。
  - 状态迁移链已成熟：`_migrate_state`（:3930 附近）带 `schema.migrations` 记录、懒迁移、向后兼容。账户层作为下一个 STATE_VERSION 迁移加入。
  - 管理动作统一走 action dispatch（:2522 附近 `update_runtime_profile` 等），审计走 `_append_event`。
- `scripts/control_plane_worker.py`：worker 包分发/安装/自更新已全套存在——`build_worker_package` :465（zip 含 `setup-worker.ps1`/`install-worker-gui.ps1`/计划任务安装器）、`check_and_apply_update` :594（manifest SHA-256 校验后原地升级并退出重启）、`WorkerConfig.auto_update` :57 默认 False。配对 JSON 已含 `package_manifest_url`/`setup_command`/`gui_install_command`。
- 网关面：`scripts/mobile_link_receiver.py`（Docker 云端部署的就是它）承载 `/pairing`、`/ios/terminal`、`/worker/*`、`/android/*`；`backend/mobile/link_receiver.py` 是 app 侧镜像面——**改动网关路由时两处要检查同步**。
- 云端部署为 Docker Compose + 具名卷（`control-state`/`minio-data`），代码更新 = `docker compose --env-file .env.cloud up -d --build`，卷保留、用户无需重配对。**严禁 `down -v`**。
- worker 心跳/task lease/结果 outbox 机制成熟（见 `docs/mobile_link_bridge.md`「Worker task leases / Worker result outbox」）。

## 目标与非目标

**目标**：
1. **账户层**：workspace 之上新增 account 实体；一个账户拥有多个 workspace；现有单机主数据无损迁移到默认 owner 账户。
2. **三个配额闸门（收费点）**：账户级 `max_workspaces`、`max_workers`、抓取数量周期配额（计量 + 超限拒绝 + 周期重置），全部可由管理端动态调整；模型可扩展（未来收费点只加 key）。
3. **用户自助控制台**：账户 token 登录的 Web 页（挂在现有 receiver 上），用户只能看/管自己账户下的资源：配额用量、workspace、Android 手机配对、worker 配对与安装、artifact 用量。
4. **「凭据不出本机」硬保证**：控制面与 worker 两端的进入式守卫——凭据形状的字段进不了控制面状态，worker 配置/上报里也不可能带出。
5. **worker 一键安装 + 云端更新**：控制台一键拿到预填好配对信息的安装命令；`auto_update` 在用户安装路径默认开启；控制台可见 worker 版本与可更新状态。

**非目标（明确不做）**：
- 不做计费/支付/套餐定价（价格未定，配额就是普通可调数字）。
- 不做 PDD/抖店登录托管（用户自理，这正是「凭据不出本机」的前提）。
- 不做抓取/上架流水线本体（另案）；本方案只预埋抓取计量钩子。
- 不改 Android APK（本轮无手机端新命令）。

## 数据模型设计

### accounts（新增 state 顶层键）

```json
"accounts": {
  "<account_id>": {
    "account_id": "acct-xxxx",
    "name": "用户可读名",
    "status": "active | disabled",
    "created_at": "...", "updated_at": "...",
    "quotas": {
      "max_workspaces": 1,
      "max_workers": 1,
      "scrape": { "limit": 500, "period": "month", "used": 0, "period_started_at": "..." }
    },
    "console_pairing_enabled": true,
    "notes": ""
  }
}
```

- `quotas` 走 `default_account_quotas() → normalize_account_quotas() → assert` 三段式（照抄 artifact_policy 模式）。`0` 或缺省 = 不限（与现有 quota 语义一致），**默认 owner 账户全部为 0（不限），保证现有行为零变化**。
- 扩展性要求：`normalize_account_quotas` 对未知 key 保留透传（未来收费点如 `max_android_devices` 直接加 key 即可生效于对应 assert 点）。
- workspace 增加 `account_id` 字段（`_new_workspace` 补一个参数，默认 owner 账户）。

### 迁移（STATE_VERSION + 1）

- `_migrate_state` 增加一步：创建 `accounts["owner"]`（quotas 全 0），把所有既存 workspace 打上 `account_id: "owner"`。懒迁移、记录进 `schema.migrations`，旧状态文件加载即完成，**云端 `up -d --build` 后用户无感**。

### 三个闸门的强制点

| 配额 | 强制位置 | 行为 |
|---|---|---|
| `max_workspaces` | `ensure_workspace` :664 及所有 `_new_workspace` 创建路径（统一收口到一个 `_assert_can_create_workspace(account_id)`） | 该账户 active workspace 数 ≥ 上限 → `PermissionError("account workspace quota exceeded: N>=M")` |
| `max_workers` | `bind_device` :1215 中 `device_role == "remote_worker"` 成功绑定前 | 该账户全部 workspace 下 active remote_worker 绑定数 ≥ 上限 → 拒绝绑定 |
| `scrape` | 新增 `consume_metered_quota(account_id, kind="scrape", amount=1)`；在 `start_workflow_run` 对**标记了 `metered: "scrape"` 的工作流模板**调用（`built_in_workflow_templates` :273 给 `ecommerce.auto_listing.v1` 等电商模板加标记）；同时导出该函数供未来抓取流水线直接调用 | 惰性周期重置（检查时若 `period_started_at` 已过期则 `used=0` 并推进周期），超限 → `PermissionError`，`used` 递增写入 state 并 `_append_event("account_quota_consumed", ...)` |

### 管理动作（管理 token 专属）

action dispatch 新增：`create_account` / `update_account_quotas` / `set_account_status`（disable 即冻结该账户全部配对生成与工作流启动，不销毁数据）/ `list_accounts`（含各账户用量汇总）。全部 `_append_event` 审计。

### 账户 token（控制台登录凭据）

- 新 `device_role: "account_console"`，pairing 存量机制直接复用：管理端为账户签发，token 绑 `account_id`（而非单 workspace）。
- 网关 gating 规则：account_console token 只能访问 `/account` 页与 `/account/action`，动作范围限自己账户下的 workspace；**不能**调管理动作、不能跨账户、不能签发 `ios_terminal` owner token。参照现有 `ios_terminal` token 的 workspace 越权拒绝写法。

## 切片顺序

| 切片 | 内容 | 规模 | 验证 |
|---|---|---|---|
| 0 | 账户实体 + 迁移 + workspace.account_id | 中 | 单测：新老状态文件各一份跑迁移；owner 账户吸收全部既存 workspace；旧行为回归全绿 |
| 1 | 三个配额闸门 + `update_account_quotas` 等管理动作 | 中 | 单测覆盖：限内通过 / 超限拒绝（三种各测）/ 周期重置 / 0=不限 / disabled 账户冻结 |
| 2 | account_console token + `GET /account` 控制台页 + `POST /account/action` | 大 | 见下「控制台功能清单」；越权用例必须测（跨账户、调管理动作、签 owner token 全拒） |
| 3 | 凭据进入式守卫（控制面 + worker 两端） | 小 | 见下「凭据不出本机」；守卫命中的单测 |
| 4 | worker 一键安装打磨 + 云端更新默认开启 | 小 | 见下「一键安装」 |
| 5 | 主控端账