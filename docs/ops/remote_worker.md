# Remote Worker 部署与故障排查

SpiritKinAI 提供两种受控 Remote Worker 运行形态：直接 HTTP Worker 适合局域网/单机验证；Control Plane Worker 适合配对、心跳领任务、结果回传、断线重试和安装包分发。两种形态都要求 Token，执行端仍会再次经过安全策略，不能把控制面授权当成执行端授权。

## 直接 HTTP Worker

目标机器安装项目依赖后设置独立 Token：

```powershell
$env:SPIRITKIN_REMOTE_WORKER_TOKEN="<target-machine-secret>"
$env:SPIRITKIN_REMOTE_NODE_ID="office-pc"
python -m backend.remote.worker
```

默认监听 `127.0.0.1:8790`。需要局域网访问时显式配置监听地址，并用主机防火墙只允许控制端来源；不要直接暴露到公网。

控制端验证：

```powershell
python scripts/smoke_remote_worker.py --url http://<worker-host>:8790 --node-id office-pc --token <target-machine-secret> --target local_pc --operation list_installed_apps
```

Smoke 会依次验证 heartbeat 和一次结构化 `ExecutionRequest -> ExecutionResult` 往返。HTTP 401 表示 Token 不一致；`safety_denied`/`execution_hard_stopped` 表示执行端安全门拒绝，不应通过重试绕过。

## Control Plane Worker

`scripts/control_plane_worker.py` 使用 `/worker/heartbeat` 领取任务，使用 `/worker/result` 回传结果；未回传结果会先写入本地 outbox，恢复联网后重放。默认 5 秒心跳，错误循环按 `error_backoff_seconds` 退避。

生成 PyInstaller 单文件 Worker 与签名安装包：

```powershell
$venv = "tmp/pyinstaller-venv"
python -m venv $venv
& "$venv/Scripts/python.exe" -m pip install pyinstaller==6.21.0
python scripts/build_control_plane_worker_exe.py --python "$venv/Scripts/python.exe" --output-dir dist

$env:SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET="<release-signing-secret>"
python scripts/control_plane_worker.py --package-zip dist/spiritkin-worker.zip --worker-executable dist/spiritkin-control-plane-worker.exe
python scripts/control_plane_worker.py --release-manifest dist/worker-manifest.json
```

包内包含 `spiritkin-control-plane-worker.exe`、`setup-worker.ps1`、`install-worker-gui.ps1`、计划任务安装器、更新脚本和示例配置。启动脚本、配置向导和计划任务优先使用 EXE；没有 EXE 的兼容包才回退目标机 Python。目标机优先使用 GUI 安装器完成 Server URL、账户/工作区、Worker ID、配对 Token、本地代理和安装目录配置。浏览器 Profile、Cookie、店铺凭据和代理 URL 只保留在目标机，不得进入控制面 Artifact 或 Worker 回传。

命令行一次性验证：

```powershell
python scripts/control_plane_worker.py --server https://<control-plane-host> --workspace-id <workspace> --worker-id <worker> --pairing-token <pairing-token> --once
```

生产副作用默认关闭。只有经过审核的目标机才允许加 `--allow-production`；本地 CLI/LangGraph/CrewAI 子进程另需 `--allow-cli`。建议先用 preview/dry-run 任务验证 capability、任务领取和 outbox，再打开生产开关。

本机可用真实独立进程 smoke 验证“执行中断联 -> outbox 保留 -> 控制面恢复 -> heartbeat 前回传”：

```powershell
python scripts/smoke_control_plane_worker_recovery.py --port 18791 --state-dir tmp/control-plane-worker-recovery
python scripts/smoke_control_plane_worker_recovery.py --port 18792 --state-dir tmp/control-plane-worker-exe-recovery --worker-executable dist/spiritkin-control-plane-worker.exe
```

该脚本只终止自己启动的临时控制面进程，并保留状态与日志用于验收；它不能替代跨机器拔网线、真实局域网延迟和长时 heartbeat 验收。

## 故障排查

- 无 heartbeat：检查控制面 URL、系统时间、配对角色必须为 `remote_worker`、Token 与防火墙。
- 任务不被领取：检查 Worker 广播的 capabilities 是否覆盖任务 required capabilities，及任务 lease 是否仍归属旧 Worker。
- 结果重复：按 `task_id` 检查 outbox；控制面应幂等接收，不能手工删除未确认记录。
- 本地命令拒绝：确认 `--allow-cli`、命令允许表、工作区路径边界与执行端 Safety Gate。
- 更新失败：核对 release manifest 签名和 ZIP SHA-256；不要跳过校验直接覆盖安装目录。
- 断网恢复：保持 Worker 运行，确认错误退避后 heartbeat 恢复，outbox 清空且同一任务只形成一个终态。

自动回归入口：`backend/tests/unit/test_remote_worker.py`、`tests/test_control_plane_worker.py`、`backend/tests/unit/test_audit_reports.py`、`scripts/smoke_control_plane_worker_recovery.py`。
