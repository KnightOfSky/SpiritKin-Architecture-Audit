# Runtime Host、Checkpoint 与迁移契约

版本：1.0（2026-07-19）
实现：`backend/orchestrator/runtime_host.py`

Runtime Host Layer 把 Workflow 的所有权从某一台桌面电脑移到共享 Runtime。Desktop、Cloud、Remote、Edge 可以声明 Workflow 执行能力；iOS 默认作为控制与 Observation Adapter。Remote Worker 继续通过自己的任务 lease 领取步骤，但不依赖 Desktop 的进程身份。

## Host 与 Lease

Host 记录包含稳定 `host_id`、workspace、host type、capabilities、`can_execute_workflows`、`can_observe`、priority、heartbeat TTL、status 和 last seen。一个 workspace 同时最多有一个有效执行 lease：

```text
Host heartbeat
  -> active lease valid: renew same lease
  -> lease missing/expired: deterministic election
  -> epoch + 1, new fencing secret
  -> old epoch/secret can no longer mutate Workflow state
```

公共快照只返回 lease id、Host、epoch、到期时间和 effective status，不返回 fencing secret 或内部 endpoint reference。当前执行 Host 通过受信心跳响应获得自己的 secret；iOS/PWA/WPF 控制面不能获取。

## Checkpoint

Checkpoint 复用 `JsonWorkflowStore` 中的 `WorkflowRun`，保存同一 `run_id`，包括节点状态、outputs、artifacts、events、inputs、queue、Pending Skill/Worker、Context reference、Definition digest、源 Host/epoch、sequence 和 SHA-256 checksum。敏感 key 写入前被替换为 `[redacted]`。

Heartbeat Service 只在活动 Run 的 `updated_at` 变化时生成 Checkpoint。新 Checkpoint supersede 同一 Run 的旧 active Checkpoint；不会每次心跳重复写相同快照。

## Migration / Resume

1. Controller 选择已存在的 Checkpoint 和在线的非当前执行 Host，显式确认迁移。
2. Registry 在同一文件锁事务中验证 workspace、当前 lease 与目标 Host，写入 prepared handoff。
3. 目标 Host 心跳发现发给自己的 handoff，claim 后得到新 epoch/fencing secret。
4. 目标 Host校验 checksum、Definition digest 和 Run 新鲜度，恢复同一 `run_id`。
5. Succeeded/Skipped/Waiting 节点保留；Running 节点改为 `waiting_review + runtime_resume_reconcile_inflight`；Pending 节点仍为 Pending。

因此 Migration 明确是 Resume，不是 Restart。没有可以实际运行的 Cloud/Remote Host 时，架构与控制面虽可见，但桌面关机后不会凭空继续；必须部署另一个常驻 Host 并让它持续心跳。

## 接口

- `GET/POST /desktop/runtime-continuity`
- `GET/POST /ios/runtime-host`
- Host 进程：`RuntimeHostHeartbeatService`

桌面和 iOS Controller 可请求 Election/Migration；执行 Host 才能 create checkpoint、claim handoff 和 resume。
