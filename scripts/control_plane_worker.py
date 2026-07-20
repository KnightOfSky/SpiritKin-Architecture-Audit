"""Lightweight Remote Worker loop for the SpiritKin control plane.

This worker intentionally starts with a narrow, governed execution surface:
heartbeat, claim assigned tasks, run a local operation handler, and post
results back. Real LangGraph/CrewAI/CLI adapters can be added behind the same
operation registry without changing the control-plane task contract.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

DEFAULT_CAPABILITIES = ["ecommerce.auto_listing", "local.cli", "langgraph.run", "crewai.run"]
DEFAULT_WORKER_STATE_DIR = Path("state") / "workers"
WORKER_VERSION = "2026.06.16.1"
RUNTIME_STATE_FILE = "runtime-state.json"
MANIFEST_VERSION = 1
RUNTIME_OPERATIONS = {"local.cli.run", "langgraph.run", "crewai.run"}
WORKER_RESULT_ALLOWED_KEYS = {
    "artifact_ids",
    "artifact_refs",
    "artifacts",
    "debug",
    "dry_run",
    "error",
    "error_code",
    "message",
    "ok",
    "planned_android_commands",
    "productData",
    "product_data",
    "promote_mode",
    "queued_android_commands",
    "redacted_sensitive_keys",
    "returncode",
    "runtime_preparation",
    "side_effects",
    "status",
    "status_code",
    "stderr",
    "stdout",
    "usage",
    "workflow_run",
    "workflow_run_id",
}
_WORKER_SENSITIVE_EXACT_KEYS = {
    "auth",
    "authorization",
    "auth_header",
    "browser_profile",
    "browser_profile_path",
    "browser_user_data_dir",
    "chrome_profile",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "local_profile",
    "passwd",
    "password",
    "profile_path",
    "secret",
    "session",
    "session_cookie",
    "session_key",
    "session_secret",
    "session_token",
    "token",
    "user_data_dir",
}
_WORKER_SENSITIVE_FRAGMENTS = ("cookie", "credential", "password", "passwd", "secret")
_WORKER_PROFILE_FRAGMENTS = ("browser_profile", "chrome_profile", "profile_path", "user_data_dir")


@dataclass(frozen=True)
class WorkerConfig:
    server_url: str
    worker_id: str
    workspace_id: str
    capabilities: list[str]
    account_id: str = ""
    token: str = ""
    pairing_token: str = ""
    proxy_url: str = ""
    interval_seconds: float = 5.0
    once: bool = False
    allow_production: bool = False
    allow_cli: bool = False
    prepare_runtime: bool = False
    state_dir: str = ""
    outbox_dir: str = ""
    max_consecutive_errors: int = 0
    error_backoff_seconds: float = 10.0
    update_manifest_url: str = ""
    auto_update: bool = False
    update_install_dir: str = ""


def normalize_server_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def safe_name(value: object, fallback: str = "item") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned[:100] or fallback


def worker_state_dir(config: WorkerConfig) -> Path:
    if config.state_dir:
        return Path(config.state_dir).resolve()
    safe_worker = safe_name(config.worker_id, "worker")
    return (Path.cwd() / DEFAULT_WORKER_STATE_DIR / safe_worker).resolve()


def worker_runtime_state_file(config: WorkerConfig) -> Path:
    return worker_state_dir(config) / RUNTIME_STATE_FILE


def load_runtime_state(config: WorkerConfig) -> dict[str, Any]:
    path = worker_runtime_state_file(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_runtime_state(config: WorkerConfig, state: dict[str, Any]) -> Path:
    path = worker_runtime_state_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_config_file(path: str) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("worker config file must contain a JSON object")
    return data


def config_to_json(config: WorkerConfig, *, include_token: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "server_url": config.server_url,
        "worker_id": config.worker_id,
        "workspace_id": config.workspace_id,
        "account_id": config.account_id,
        "capabilities": config.capabilities,
        "interval_seconds": config.interval_seconds,
        "allow_production": config.allow_production,
        "allow_cli": config.allow_cli,
        "prepare_runtime": config.prepare_runtime,
        "state_dir": config.state_dir,
        "outbox_dir": config.outbox_dir,
        "max_consecutive_errors": config.max_consecutive_errors,
        "error_backoff_seconds": config.error_backoff_seconds,
        "update_manifest_url": config.update_manifest_url,
        "auto_update": config.auto_update,
        "update_install_dir": config.update_install_dir,
    }
    if config.proxy_url:
        data["local_proxy"] = {
            "http_proxy": config.proxy_url,
            "https_proxy": config.proxy_url,
            "note": "Local-only worker proxy. Browser profiles, cookies, and store credentials must stay on this machine.",
        }
    if include_token and config.token:
        data["token"] = config.token
    return data


def proxy_url_from_config(value: object) -> str:
    if isinstance(value, dict):
        for key in ("https_proxy", "http_proxy", "proxy_url", "url"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return str(value or "").strip()


def worker_task_environment(config: WorkerConfig | None, workspace_root: Path) -> dict[str, str]:
    env = {**os.environ, "SPIRITKIN_WORKSPACE_ROOT": str(workspace_root)}
    if config and config.proxy_url:
        env.update(
            {
                "HTTP_PROXY": config.proxy_url,
                "HTTPS_PROXY": config.proxy_url,
                "http_proxy": config.proxy_url,
                "https_proxy": config.proxy_url,
            }
        )
    return env


def write_config_file(config: WorkerConfig, path: str, *, include_token: bool = False) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config_to_json(config, include_token=include_token), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def worker_install_dir(config: WorkerConfig) -> Path:
    if config.update_install_dir:
        return Path(config.update_install_dir).resolve()
    script_path = Path(__file__).resolve()
    return script_path.parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_manifest(manifest: dict[str, Any], secret: str) -> dict[str, Any]:
    signed = dict(manifest)
    signed.pop("signature", None)
    signature = hmac.new(secret.encode("utf-8"), canonical_json(signed), hashlib.sha256).hexdigest()
    signed["signature"] = {"algorithm": "hmac-sha256", "value": signature}
    return signed


def worker_release_manifest(
    *,
    version: str = WORKER_VERSION,
    base_dir: Path | None = None,
    files: list[str] | None = None,
    signing_secret: str = "",
) -> dict[str, Any]:
    root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
    release_files = files or [
        "scripts/control_plane_worker.py",
        "docs/light_cloud_control_plane.md",
        "docs/mobile_link_bridge.md",
    ]
    items = []
    total_size = 0
    for item in release_files:
        rel = Path(item)
        path = (root / rel).resolve()
        if root not in path.parents and path != root:
            raise ValueError(f"release file outside root: {item}")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        size = path.stat().st_size
        total_size += size
        items.append(
            {
                "path": rel.as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": size,
            }
        )
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "package": "spiritkin-control-plane-worker",
        "version": version,
        "created_at": utc_now(),
        "entrypoint": "python scripts\\control_plane_worker.py --config <worker.json>",
        "files": items,
        "integrity": {
            "algorithm": "sha256",
            "file_count": len(items),
            "total_size_bytes": total_size,
        },
        "capabilities": list(DEFAULT_CAPABILITIES),
        "runtime_state_file": RUNTIME_STATE_FILE,
    }
    if signing_secret:
        manifest = sign_manifest(manifest, signing_secret)
    return manifest


def write_worker_release_manifest(path: str, *, signing_secret: str = "", version: str = WORKER_VERSION) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = worker_release_manifest(version=version, signing_secret=signing_secret)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def worker_example_config() -> dict[str, Any]:
    return {
        "server_url": "http://127.0.0.1:8791",
        "account_id": "",
        "worker_id": "worker-1",
        "workspace_id": "local-ecommerce",
        "capabilities": list(DEFAULT_CAPABILITIES),
        "interval_seconds": 5.0,
        "allow_production": False,
        "allow_cli": True,
        "prepare_runtime": True,
        "state_dir": "state/workers/worker-1",
        "max_consecutive_errors": 12,
        "error_backoff_seconds": 10.0,
        "update_manifest_url": "http://127.0.0.1:8791/worker/package/manifest",
        "auto_update": False,
        "update_install_dir": ".",
        "local_proxy": {
            "http_proxy": "",
            "https_proxy": "",
            "note": "Optional local-only proxy. Do not upload browser profiles, cookies, or ecommerce credentials.",
        },
    }


def worker_run_cmd() -> str:
    return "\r\n".join(
        [
            "@echo off",
            "setlocal",
            "cd /d \"%~dp0\"",
            "if exist spiritkin-control-plane-worker.exe (",
            "  spiritkin-control-plane-worker.exe --config worker.example.json %*",
            ") else (",
            "  python -u scripts\\control_plane_worker.py --config worker.example.json %*",
            ")",
            "",
        ]
    )


def worker_install_scheduled_task_ps1() -> str:
    return "\n".join(
        [
            "param(",
            "  [string]$TaskName = 'SpiritKin Remote Worker',",
            "  [string]$WorkerDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),",
            "  [string]$Config = 'worker.example.json'",
            ")",
            "$workerExe = Join-Path $WorkerDir 'spiritkin-control-plane-worker.exe'",
            "$script = Join-Path $WorkerDir 'scripts\\control_plane_worker.py'",
            "$configPath = Join-Path $WorkerDir $Config",
            "if (Test-Path -LiteralPath $workerExe) {",
            "  $action = New-ScheduledTaskAction -Execute $workerExe -Argument \"--config `\"$configPath`\"\" -WorkingDirectory $WorkerDir",
            "} else {",
            "  $python = (Get-Command python -ErrorAction Stop).Source",
            "  $action = New-ScheduledTaskAction -Execute $python -Argument \"-u `\"$script`\" --config `\"$configPath`\"\" -WorkingDirectory $WorkerDir",
            "}",
            "$trigger = New-ScheduledTaskTrigger -AtStartup",
            "$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)",
            "Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null",
            "Write-Host \"Registered scheduled task: $TaskName\"",
            "",
        ]
    )


def worker_setup_ps1() -> str:
    return "\n".join(
        [
            "param(",
            "  [Parameter(Mandatory=$true)][string]$ServerUrl,",
            "  [Parameter(Mandatory=$true)][string]$WorkspaceId,",
            "  [Parameter(Mandatory=$true)][string]$WorkerId,",
            "  [string]$AccountId = '',",
            "  [string]$PairingToken = '',",
            "  [string]$ProxyUrl = '',",
            "  [string]$UpdateManifestUrl = '',",
            "  [string]$WorkerDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),",
            "  [string]$Config = 'worker.json',",
            "  [switch]$InstallScheduledTask",
            ")",
            "$ErrorActionPreference = 'Stop'",
            "$WorkerDir = (Resolve-Path -LiteralPath $WorkerDir).Path",
            "if (-not $UpdateManifestUrl) { $UpdateManifestUrl = $ServerUrl.TrimEnd('/') + '/worker/package/manifest' }",
            "$configPath = Join-Path $WorkerDir $Config",
            "$stateDir = Join-Path $WorkerDir ('state\\workers\\' + $WorkerId)",
            "$script = Join-Path $WorkerDir 'scripts\\control_plane_worker.py'",
            "$workerExe = Join-Path $WorkerDir 'spiritkin-control-plane-worker.exe'",
            "$runner = if (Test-Path -LiteralPath $workerExe) { $workerExe } else { 'python' }",
            "$prefix = if (Test-Path -LiteralPath $workerExe) { @() } else { @($script) }",
            "$args = @(",
            "  $prefix + @('--server', $ServerUrl, '--workspace-id', $WorkspaceId, '--worker-id', $WorkerId,",
            "  '--state-dir', $stateDir, '--write-config', $configPath, '--allow-cli', '--prepare-runtime',",
            "  '--update-manifest-url', $UpdateManifestUrl, '--update-install-dir', $WorkerDir, '--auto-update')",
            ")",
            "if ($AccountId) { $args += @('--account-id', $AccountId) }",
            "if ($ProxyUrl) { $args += @('--proxy-url', $ProxyUrl) }",
            "& $runner @args | Write-Host",
            "if ($PairingToken) {",
            "  $pairArgs = @($prefix + @('--config', $configPath, '--pairing-token', $PairingToken, '--once'))",
            "  & $runner @pairArgs | Write-Host",
            "}",
            "if ($InstallScheduledTask) {",
            "  & (Join-Path $WorkerDir 'install-worker-scheduled-task.ps1') -WorkerDir $WorkerDir -Config $Config",
            "}",
            "Write-Host \"Worker config: $configPath\"",
            "Write-Host \"Worker state: $stateDir\"",
            "",
        ]
    )


def worker_update_ps1() -> str:
    return "\n".join(
        [
            "param(",
            "  [Parameter(Mandatory=$true)][string]$ManifestUrl,",
            "  [string]$InstallDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),",
            "  [string]$DownloadPath = ''",
            ")",
            "$ErrorActionPreference = 'Stop'",
            "$manifest = Invoke-RestMethod -Uri $ManifestUrl",
            "$package = if ($manifest.worker_package) { $manifest.worker_package } else { $manifest }",
            "$downloadUrl = [string]$package.download_url",
            "if (-not $downloadUrl) { throw 'manifest missing download_url' }",
            "if (-not $DownloadPath) {",
            "  $fileName = if ($package.download_file) { [string]$package.download_file } else { 'spiritkin-control-plane-worker.zip' }",
            "  $DownloadPath = Join-Path $env:TEMP $fileName",
            "}",
            "Invoke-WebRequest -Uri $downloadUrl -OutFile $DownloadPath",
            "$expected = [string]$package.sha256",
            "if ($expected) {",
            "  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $DownloadPath).Hash.ToLowerInvariant()",
            "  if ($actual -ne $expected.ToLowerInvariant()) { throw \"Worker package SHA-256 mismatch: $actual\" }",
            "}",
            "New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null",
            "Expand-Archive -LiteralPath $DownloadPath -DestinationPath $InstallDir -Force",
            "Write-Host \"Installed Worker package to $InstallDir\"",
            "",
        ]
    )


def worker_install_gui_ps1() -> str:
    return "\n".join(
        [
            "param(",
            "  [string]$ServerUrl = 'http://127.0.0.1:8791',",
            "  [string]$AccountId = '',",
            "  [string]$WorkspaceId = 'local-ecommerce',",
            "  [string]$WorkerId = $env:COMPUTERNAME,",
            "  [string]$PairingToken = '',",
            "  [string]$ProxyUrl = '',",
            "  [string]$WorkerDir = (Split-Path -Parent $MyInvocation.MyCommand.Path)",
            ")",
            "$ErrorActionPreference = 'Stop'",
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$form = New-Object System.Windows.Forms.Form",
            "$form.Text = 'SpiritKin Remote Worker Installer'",
            "$form.StartPosition = 'CenterScreen'",
            "$form.Size = New-Object System.Drawing.Size(760, 640)",
            "$form.MinimumSize = New-Object System.Drawing.Size(640, 500)",
            "$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)",
            "function Add-Label([string]$Text, [int]$X, [int]$Y) {",
            "  $label = New-Object System.Windows.Forms.Label",
            "  $label.Text = $Text",
            "  $label.Location = New-Object System.Drawing.Point($X, $Y)",
            "  $label.Size = New-Object System.Drawing.Size(160, 24)",
            "  $form.Controls.Add($label)",
            "}",
            "function Add-TextBox([string]$Text, [int]$X, [int]$Y, [int]$Width) {",
            "  $box = New-Object System.Windows.Forms.TextBox",
            "  $box.Text = $Text",
            "  $box.Location = New-Object System.Drawing.Point($X, $Y)",
            "  $box.Size = New-Object System.Drawing.Size($Width, 24)",
            "  $form.Controls.Add($box)",
            "  return $box",
            "}",
            "Add-Label 'Control plane URL' 18 22",
            "$serverBox = Add-TextBox $ServerUrl 190 20 520",
            "Add-Label 'Account ID' 18 58",
            "$accountBox = Add-TextBox $AccountId 190 56 520",
            "Add-Label 'Workspace ID' 18 94",
            "$workspaceBox = Add-TextBox $WorkspaceId 190 92 520",
            "Add-Label 'Worker ID' 18 130",
            "$workerBox = Add-TextBox $WorkerId 190 128 520",
            "Add-Label 'Pairing token' 18 166",
            "$tokenBox = Add-TextBox $PairingToken 190 164 520",
            "Add-Label 'Local proxy URL' 18 202",
            "$proxyBox = Add-TextBox $ProxyUrl 190 200 520",
            "Add-Label 'Install directory' 18 238",
            "$dirBox = Add-TextBox $WorkerDir 190 236 430",
            "$browse = New-Object System.Windows.Forms.Button",
            "$browse.Text = 'Browse'",
            "$browse.Location = New-Object System.Drawing.Point(630, 234)",
            "$browse.Size = New-Object System.Drawing.Size(70, 28)",
            "$browse.Add_Click({",
            "  $dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
            "  $dialog.SelectedPath = $dirBox.Text",
            "  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dirBox.Text = $dialog.SelectedPath }",
            "})",
            "$form.Controls.Add($browse)",
            "$taskBox = New-Object System.Windows.Forms.CheckBox",
            "$taskBox.Text = 'Register Scheduled Task after setup'",
            "$taskBox.Location = New-Object System.Drawing.Point(190, 274)",
            "$taskBox.Size = New-Object System.Drawing.Size(300, 24)",
            "$form.Controls.Add($taskBox)",
            "$hint = New-Object System.Windows.Forms.Label",
            "$hint.Text = 'Local-only credentials: log in to PDD/Douyin on this worker machine. Browser profiles, cookies, passwords and store sessions must never be uploaded to the control plane.'",
            "$hint.Location = New-Object System.Drawing.Point(18, 306)",
            "$hint.Size = New-Object System.Drawing.Size(700, 42)",
            "$hint.ForeColor = [System.Drawing.Color]::FromArgb(154, 52, 18)",
            "$form.Controls.Add($hint)",
            "$run = New-Object System.Windows.Forms.Button",
            "$run.Text = 'Install / Pair Worker'",
            "$run.Location = New-Object System.Drawing.Point(190, 356)",
            "$run.Size = New-Object System.Drawing.Size(160, 32)",
            "$form.Controls.Add($run)",
            "$close = New-Object System.Windows.Forms.Button",
            "$close.Text = 'Close'",
            "$close.Location = New-Object System.Drawing.Point(360, 356)",
            "$close.Size = New-Object System.Drawing.Size(90, 32)",
            "$close.Add_Click({ $form.Close() })",
            "$form.Controls.Add($close)",
            "$log = New-Object System.Windows.Forms.TextBox",
            "$log.Multiline = $true",
            "$log.ScrollBars = 'Vertical'",
            "$log.ReadOnly = $true",
            "$log.Location = New-Object System.Drawing.Point(18, 404)",
            "$log.Size = New-Object System.Drawing.Size(700, 170)",
            "$log.Anchor = 'Left,Right,Top,Bottom'",
            "$form.Controls.Add($log)",
            "$run.Add_Click({",
            "  try {",
            "    $run.Enabled = $false",
            "    $targetDir = $dirBox.Text.Trim()",
            "    if (-not $targetDir) { throw 'Install directory is required' }",
            "    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null",
            "    $setup = Join-Path $targetDir 'setup-worker.ps1'",
            "    if (-not (Test-Path -LiteralPath $setup)) {",
            "      $setup = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'setup-worker.ps1'",
            "    }",
            "    if (-not (Test-Path -LiteralPath $setup)) { throw 'setup-worker.ps1 not found' }",
            "    $args = @(",
            "      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $setup,",
            "      '-ServerUrl', $serverBox.Text.Trim(),",
            "      '-WorkspaceId', $workspaceBox.Text.Trim(),",
            "      '-WorkerId', $workerBox.Text.Trim(),",
            "      '-WorkerDir', $targetDir",
            "    )",
            "    if ($accountBox.Text.Trim()) { $args += @('-AccountId', $accountBox.Text.Trim()) }",
            "    if ($proxyBox.Text.Trim()) { $args += @('-ProxyUrl', $proxyBox.Text.Trim()) }",
            "    if ($tokenBox.Text.Trim()) { $args += @('-PairingToken', $tokenBox.Text.Trim()) }",
            "    if ($taskBox.Checked) { $args += '-InstallScheduledTask' }",
            "    $log.AppendText(\"Running setup-worker.ps1...`r`n\")",
            "    $output = & powershell @args 2>&1 | Out-String",
            "    $log.AppendText($output + \"`r`nDone.`r`n\")",
            "  } catch {",
            "    $log.AppendText('ERROR: ' + $_.Exception.Message + \"`r`n\")",
            "  } finally {",
            "    $run.Enabled = $true",
            "  }",
            "})",
            "[void]$form.ShowDialog()",
            "",
        ]
    )


def build_worker_package(
    output_zip: str,
    *,
    signing_secret: str = "",
    version: str = WORKER_VERSION,
    base_dir: Path | None = None,
    worker_executable: str | os.PathLike[str] | None = None,
) -> Path:
    root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
    target = Path(output_zip)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, bytes] = {}
    for rel in ["scripts/control_plane_worker.py", "docs/light_cloud_control_plane.md", "docs/mobile_link_bridge.md"]:
        path = (root / rel).resolve()
        if root not in path.parents and path != root:
            raise ValueError(f"package file outside root: {rel}")
        payload[Path(rel).as_posix()] = path.read_bytes()
    payload["worker.example.json"] = (json.dumps(worker_example_config(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload["run-worker.cmd"] = worker_run_cmd().encode("utf-8")
    payload["setup-worker.ps1"] = worker_setup_ps1().encode("utf-8")
    payload["update-worker.ps1"] = worker_update_ps1().encode("utf-8")
    payload["install-worker-gui.ps1"] = worker_install_gui_ps1().encode("utf-8")
    payload["install-worker-scheduled-task.ps1"] = worker_install_scheduled_task_ps1().encode("utf-8")
    executable_path = Path(worker_executable).resolve() if worker_executable else None
    if executable_path is not None:
        if not executable_path.is_file():
            raise FileNotFoundError(str(executable_path))
        payload["spiritkin-control-plane-worker.exe"] = executable_path.read_bytes()

    manifest_files = []
    total_size = 0
    for name, content in payload.items():
        total_size += len(content)
        manifest_files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "package": "spiritkin-control-plane-worker",
        "version": version,
        "created_at": utc_now(),
        "entrypoint": (
            "spiritkin-control-plane-worker.exe --config worker.example.json"
            if executable_path is not None
            else "python scripts\\control_plane_worker.py --config worker.example.json"
        ),
        "files": manifest_files,
        "integrity": {
            "algorithm": "sha256",
            "file_count": len(manifest_files),
            "total_size_bytes": total_size,
        },
        "capabilities": list(DEFAULT_CAPABILITIES),
        "runtime_state_file": RUNTIME_STATE_FILE,
        "package_format": "zip",
    }
    if signing_secret:
        manifest = sign_manifest(manifest, signing_secret)
    payload["worker-release-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payload):
            archive.writestr(name, payload[name])
    return target


def post_json(server_url: str, path: str, payload: dict[str, Any], *, token: str = "", timeout: float = 15.0) -> dict[str, Any]:
    url = normalize_server_url(server_url) + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed {path}: {exc.reason}") from exc
    parsed = json.loads(data or "{}")
    if not parsed.get("ok", True):
        raise RuntimeError(str(parsed.get("error") or parsed))
    return parsed


def get_json(url: str, *, timeout: float = 15.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8") or "{}")
    return parsed if isinstance(parsed, dict) else {}


def download_file(url: str, target: Path, *, timeout: float = 60.0) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        with target.open("wb") as fh:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                fh.write(chunk)
    return target


def parse_worker_package_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    manifest = payload.get("worker_package") if isinstance(payload.get("worker_package"), dict) else payload
    if not isinstance(manifest, dict):
        raise ValueError("worker package manifest must be an object")
    if str(manifest.get("package") or "") != "spiritkin-control-plane-worker":
        raise ValueError("unexpected worker package")
    return manifest


def verify_file_sha256(path: Path, expected_sha256: str) -> str:
    actual = sha256_file(path)
    expected = str(expected_sha256 or "").strip().lower()
    if expected and actual != expected:
        raise ValueError(f"sha256 mismatch for {path.name}: {actual}")
    return actual


def extract_worker_package(zip_path: Path, install_dir: Path) -> list[str]:
    install_root = install_dir.resolve()
    written: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"unsafe package path: {info.filename}")
            target = (install_root / rel).resolve()
            if target != install_root and install_root not in target.parents:
                raise ValueError(f"package path outside install dir: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
            written.append(rel.as_posix())
    return written


def check_and_apply_update(config: WorkerConfig, *, install_dir: Path | None = None) -> dict[str, Any]:
    if not config.update_manifest_url:
        return {"checked": False, "reason": "missing update_manifest_url"}
    manifest = parse_worker_package_manifest(get_json(config.update_manifest_url))
    latest_version = str(manifest.get("version") or "")
    current_version = WORKER_VERSION
    if latest_version and latest_version == current_version:
        return {"checked": True, "updated": False, "current_version": current_version, "latest_version": latest_version}
    download_url = str(manifest.get("download_url") or "").strip()
    if not download_url:
        raise ValueError("worker package manifest missing download_url")
    install_root = (install_dir or worker_install_dir(config)).resolve()
    with TemporaryDirectory() as tmp:
        package_path = Path(tmp) / str(manifest.get("download_file") or "spiritkin-control-plane-worker.zip")
        download_file(download_url, package_path)
        actual_sha256 = verify_file_sha256(package_path, str(manifest.get("sha256") or ""))
        written = extract_worker_package(package_path, install_root)
    runtime_state = load_runtime_state(config)
    runtime_state.update(
        {
            "last_update_at": utc_now(),
            "last_update_from_version": current_version,
            "last_update_to_version": latest_version,
            "last_update_manifest_url": config.update_manifest_url,
            "updated_at": utc_now(),
        }
    )
    save_runtime_state(config, runtime_state)
    return {
        "checked": True,
        "updated": True,
        "current_version": current_version,
        "latest_version": latest_version,
        "sha256": actual_sha256,
        "install_dir": str(install_root),
        "files": written,
    }


def heartbeat_payload(config: WorkerConfig) -> dict[str, Any]:
    payload = {
        "worker_id": config.worker_id,
        "workspace_id": config.workspace_id,
        "capabilities": config.capabilities,
        "version": "control-plane-worker/0.1",
        "state": {
            "host": socket.gethostname(),
            "account_id": config.account_id,
            "allow_production": config.allow_production,
            "proxy_configured": bool(config.proxy_url),
        },
    }
    if config.token:
        payload["token"] = config.token
    return payload


def pair_worker(config: WorkerConfig) -> str:
    if not config.pairing_token:
        return config.token
    response = post_json(
        config.server_url,
        "/worker/pair",
        {
            "pairing_token": config.pairing_token,
            "worker_id": config.worker_id,
            "workspace_id": config.workspace_id,
            "device_state": {
                "host": socket.gethostname(),
                "capabilities": config.capabilities,
            },
        },
        token=config.token,
    )
    binding = response.get("binding") if isinstance(response.get("binding"), dict) else {}
    if binding.get("workspace_id"):
        runtime_state = load_runtime_state(config)
        runtime_state.update(
            {
                "server_url": config.server_url,
                "account_id": config.account_id,
                "worker_id": binding.get("worker_id") or config.worker_id,
                "workspace_id": binding.get("workspace_id") or config.workspace_id,
                "token": config.pairing_token,
                "device_role": binding.get("device_role") or "remote_worker",
                "paired_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        state_file = save_runtime_state(config, runtime_state)
        print(
            json.dumps(
                {
                    "paired": {
                        "worker_id": runtime_state["worker_id"],
                        "workspace_id": runtime_state["workspace_id"],
                        "device_role": runtime_state["device_role"],
                        "state_file": str(state_file),
                    }
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return config.pairing_token


def worker_outbox_dir(config: WorkerConfig) -> Path:
    if config.outbox_dir:
        return Path(config.outbox_dir).resolve()
    return worker_state_dir(config) / "outbox"


def spool_result(outbox_dir: Path, payload: dict[str, Any]) -> Path:
    outbox_dir.mkdir(parents=True, exist_ok=True)
    payload = sanitize_worker_report(payload)
    task_id = str(payload.get("task_id") or "task").strip() or "task"
    safe_task = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in task_id).strip("._") or "task"
    target = outbox_dir / f"{int(time.time() * 1000)}-{safe_task}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def pending_outbox_files(outbox_dir: Path) -> list[Path]:
    if not outbox_dir.exists():
        return []
    return sorted(path for path in outbox_dir.glob("*.json") if path.is_file())


def config_from_sources(args: argparse.Namespace) -> WorkerConfig:
    file_config = load_config_file(getattr(args, "config", ""))

    def value(name: str, default: Any = "") -> Any:
        cli_value = getattr(args, name, None)
        if cli_value not in (None, "", [], False):
            return cli_value
        return file_config.get(name, default)

    capabilities = getattr(args, "capabilities", None) or file_config.get("capabilities") or list(DEFAULT_CAPABILITIES)
    if isinstance(capabilities, str):
        capabilities = [item.strip() for item in capabilities.split(",") if item.strip()]
    if not isinstance(capabilities, list):
        capabilities = list(DEFAULT_CAPABILITIES)
    config = WorkerConfig(
        server_url=str(value("server", file_config.get("server_url", "")) or file_config.get("server_url", "")),
        worker_id=str(value("worker_id", socket.gethostname()) or socket.gethostname()),
        workspace_id=str(value("workspace_id", "local-ecommerce") or "local-ecommerce"),
        capabilities=[str(item) for item in capabilities],
        token=str(value("token", file_config.get("token", "")) or ""),
        account_id=str(value("account_id", file_config.get("account_id", "")) or ""),
        pairing_token=str(value("pairing_token", "") or ""),
        proxy_url=proxy_url_from_config(value("proxy_url", file_config.get("local_proxy", file_config.get("proxy_url", "")))),
        interval_seconds=float(value("interval", file_config.get("interval_seconds", 5.0)) or 5.0),
        once=bool(getattr(args, "once", False) or file_config.get("once", False)),
        allow_production=bool(getattr(args, "allow_production", False) or file_config.get("allow_production", False)),
        allow_cli=bool(getattr(args, "allow_cli", False) or file_config.get("allow_cli", False)),
        prepare_runtime=bool(getattr(args, "prepare_runtime", False) or file_config.get("prepare_runtime", False)),
        state_dir=str(value("state_dir", file_config.get("state_dir", "")) or ""),
        outbox_dir=str(value("outbox_dir", file_config.get("outbox_dir", "")) or ""),
        max_consecutive_errors=int(value("max_consecutive_errors", file_config.get("max_consecutive_errors", 0)) or 0),
        error_backoff_seconds=float(value("error_backoff", file_config.get("error_backoff_seconds", 10.0)) or 10.0),
        update_manifest_url=str(value("update_manifest_url", file_config.get("update_manifest_url", "")) or ""),
        auto_update=bool(getattr(args, "auto_update", False) or file_config.get("auto_update", False)),
        update_install_dir=str(value("update_install_dir", file_config.get("update_install_dir", "")) or ""),
    )
    runtime_state = load_runtime_state(config)
    runtime_token = str(runtime_state.get("token") or "").strip()
    if runtime_token and not config.token and not config.pairing_token:
        config = replace(config, token=runtime_token)
    runtime_worker_id = str(runtime_state.get("worker_id") or "").strip()
    runtime_workspace_id = str(runtime_state.get("workspace_id") or "").strip()
    if runtime_worker_id and not getattr(args, "worker_id", ""):
        config = replace(config, worker_id=runtime_worker_id)
    if runtime_workspace_id and not getattr(args, "workspace_id", ""):
        config = replace(config, workspace_id=runtime_workspace_id)
    return config


def flush_outbox(config: WorkerConfig) -> list[dict[str, Any]]:
    outbox_dir = worker_outbox_dir(config)
    flushed: list[dict[str, Any]] = []
    for path in pending_outbox_files(outbox_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("outbox payload must be an object")
            payload = sanitize_worker_report(payload)
            result = post_json(config.server_url, "/worker/result", payload, token=config.token)
            path.unlink()
            flushed.append({"path": str(path), "ok": True, "result": result})
        except Exception as exc:
            flushed.append({"path": str(path), "ok": False, "error": exc.__class__.__name__, "message": str(exc)})
            break
    return flushed


def execute_task(
    task: dict[str, Any],
    *,
    config: WorkerConfig | None = None,
    allow_production: bool = False,
    allow_cli: bool = False,
    prepare_runtime: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    operation = str(task.get("operation") or "")
    handler = TASK_HANDLERS.get(operation)
    if handler is None:
        return task_result(task, "failed", {"ok": False, "error": f"unsupported operation: {operation}"}, started)
    try:
        runtime_preparation = None
        if operation in RUNTIME_OPERATIONS and (allow_cli or prepare_runtime or task.get("runtime_profile")):
            runtime_preparation = prepare_runtime_profile(task.get("runtime_profile"), create_venv=prepare_runtime)
        result = handler(
            task,
            config=config,
            allow_production=allow_production,
            allow_cli=allow_cli,
            runtime_preparation=runtime_preparation,
        )
        status = "completed" if result.get("ok", True) else "failed"
        return task_result(task, status, result, started)
    except Exception as exc:
        return task_result(task, "failed", {"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, started)


def task_result(task: dict[str, Any], status: str, result: dict[str, Any], started: float) -> dict[str, Any]:
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    usage = dict(usage)
    usage.setdefault("runtime_seconds", max(0, int(time.monotonic() - started)))
    usage.setdefault("artifacts", 0)
    usage.setdefault("android_commands", 0)
    usage.setdefault("retries", 0)
    result = sanitize_worker_result_payload(result)
    result["usage"] = usage
    return {
        "task_id": str(task.get("task_id") or ""),
        "worker_id": str(task.get("worker_id") or ""),
        "status": status,
        "success": status == "completed",
        "result": result,
    }


def sanitize_worker_report(payload: dict[str, Any]) -> dict[str, Any]:
    report = dict(payload)
    result = report.get("result")
    if isinstance(result, dict):
        report["result"] = sanitize_worker_result_payload(result)
    return report


def sanitize_worker_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    sensitive: list[str] = find_worker_sensitive_keys(result)
    sanitized = {}
    nested_sensitive: list[str] = []
    for key, value in dict(result).items():
        if key not in WORKER_RESULT_ALLOWED_KEYS:
            continue
        redacted = _redact_worker_value(value, (str(key),), nested_sensitive)
        sanitized[key] = redacted
    sensitive_keys = list(dict.fromkeys([*sensitive, *nested_sensitive]))
    if sensitive_keys:
        sanitized["redacted_sensitive_keys"] = sensitive_keys[:20]
    return sanitized


def find_worker_sensitive_keys(value: Any) -> list[str]:
    found: list[str] = []

    def walk(item: Any, path: tuple[str, ...]) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                next_path = (*path, key_text)
                if _is_worker_sensitive_key(key_text):
                    found.append(".".join(next_path))
                walk(child, next_path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                walk(child, (*path, f"[{index}]"))

    walk(value, ())
    return found


def _redact_worker_value(value: Any, path: tuple[str, ...], sensitive: list[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            next_path = (*path, key_text)
            if _is_worker_sensitive_key(key_text):
                sensitive.append(".".join(next_path))
                continue
            sanitized[key] = _redact_worker_value(child, next_path, sensitive)
        return sanitized
    if isinstance(value, list):
        return [_redact_worker_value(child, (*path, f"[{index}]"), sensitive) for index, child in enumerate(value)]
    return value


def _is_worker_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized or normalized in {"token_id", "pairing_token_id"}:
        return False
    if normalized.endswith("_token") and normalized != "pairing_token":
        return True
    if normalized in _WORKER_SENSITIVE_EXACT_KEYS:
        return True
    if any(fragment in normalized for fragment in _WORKER_SENSITIVE_FRAGMENTS):
        return True
    return any(fragment in normalized for fragment in _WORKER_PROFILE_FRAGMENTS)


def prepare_runtime_profile(profile_value: object, *, create_venv: bool = False) -> dict[str, Any]:
    profile = profile_value if isinstance(profile_value, dict) else {}
    workspace_root = workspace_root_path(profile)
    workspace_root.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "workspace_root": str(workspace_root),
        "venv_path": "",
        "venv_ready": False,
        "dependency_policy": str(profile.get("dependency_policy") or "project_local_only"),
    }
    venv_value = str(profile.get("venv_path") or "").strip()
    if not venv_value:
        return result
    venv_path = resolve_under_workspace(workspace_root, venv_value)
    result["venv_path"] = str(venv_path)
    pyvenv_cfg = venv_path / "pyvenv.cfg"
    if create_venv and not pyvenv_cfg.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], cwd=str(workspace_root), check=True, timeout=120)
    result["venv_ready"] = pyvenv_cfg.exists()
    return result


def workspace_root_path(profile: dict[str, Any]) -> Path:
    value = str(profile.get("workspace_root") or "state/workspaces/local-ecommerce").strip()
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def resolve_under_workspace(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = workspace_root / path
    resolved = path.resolve()
    root = workspace_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path outside workspace root: {resolved}")
    return resolved


def planned_auto_listing_android_commands(inputs: dict[str, Any], *, allow_submit: bool = False) -> list[dict[str, Any]]:
    artifact_ids = inputs.get("artifact_ids") if isinstance(inputs.get("artifact_ids"), list) else []
    commands: list[dict[str, Any]] = []
    if artifact_ids:
        commands.append({"operation": "pdd.share_image", "params": {"artifact_id": artifact_ids[0]}})
    commands.append({"operation": "pdd.create_listing", "params": {"allow_submit": bool(allow_submit)}})
    return commands


def queue_auto_listing_android_commands(task: dict[str, Any], config: WorkerConfig, planned_commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queued: list[dict[str, Any]] = []
    workspace_id = str(task.get("workspace_id") or config.workspace_id)
    requested_by = str(task.get("worker_id") or config.worker_id or "remote_worker")
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    target_device = str(inputs.get("target_device_id") or inputs.get("device_id") or "*")
    for command in planned_commands:
        response = post_json(
            config.server_url,
            "/ios/control/action",
            {
                "action": "queue_android_command",
                "workspace_id": workspace_id,
                "operation": str(command.get("operation") or ""),
                "params": command.get("params") if isinstance(command.get("params"), dict) else {},
                "device_id": target_device,
                "requested_by": requested_by,
            },
            token=config.token,
        )
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        queued_command = result.get("command") if isinstance(result.get("command"), dict) else {}
        queued.append(
            {
                "operation": command.get("operation"),
                "command_id": queued_command.get("command_id") or "",
                "device_id": queued_command.get("device_id") or target_device,
                "status": queued_command.get("status") or "",
            }
        )
    return queued


def start_auto_listing_graph_run(task: dict[str, Any], config: WorkerConfig) -> dict[str, Any]:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    payload_inputs = {
        **dict(inputs),
        "worker_task_id": str(task.get("task_id") or ""),
        "worker_id": str(task.get("worker_id") or config.worker_id),
        "workspace_id": str(task.get("workspace_id") or config.workspace_id),
        "governance": governance,
        "deprecated_worker_operation": "workflow.execute.auto_listing",
    }
    response = post_json(
        config.server_url,
        "/desktop/workflows",
        {
            "action": "start_run",
            "workflow_name": "ecommerce.auto_listing.v1",
            "inputs": payload_inputs,
            "actor": str(task.get("worker_id") or config.worker_id or "remote_worker"),
        },
        token=config.token,
    )
    action_result = response.get("action_result") if isinstance(response.get("action_result"), dict) else {}
    data = action_result.get("data") if isinstance(action_result.get("data"), dict) else {}
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    return {
        "response_ok": bool(response.get("ok", True)),
        "workflow_name": "ecommerce.auto_listing.v1",
        "run_id": str(run.get("run_id") or ""),
        "run": run,
        "raw_response": response,
    }


def execute_auto_listing(
    task: dict[str, Any],
    *,
    config: WorkerConfig | None = None,
    allow_production: bool = False,
    **_: Any,
) -> dict[str, Any]:
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    promote_mode = str(governance.get("promote_mode") or "dry_run")
    artifact_ids = inputs.get("artifact_ids") if isinstance(inputs.get("artifact_ids"), list) else []
    planned_android_commands = planned_auto_listing_android_commands(inputs, allow_submit=promote_mode == "production")

    if promote_mode == "production" and not allow_production:
        return {
            "ok": False,
            "error": "production execution disabled for this worker",
            "promote_mode": promote_mode,
            "dry_run": False,
            "planned_android_commands": planned_android_commands,
            "usage": {"android_commands": len(planned_android_commands), "artifacts": len(artifact_ids)},
        }
    if promote_mode == "production":
        if config is None or not config.server_url:
            return {
                "ok": False,
                "error": "production auto-listing requires worker server config",
                "promote_mode": promote_mode,
                "dry_run": False,
                "planned_android_commands": planned_android_commands,
                "usage": {"android_commands": len(planned_android_commands), "artifacts": len(artifact_ids)},
            }
        workflow_run = start_auto_listing_graph_run(task, config)
        return {
            "ok": True,
            "promote_mode": promote_mode,
            "dry_run": False,
            "debug": bool(governance.get("debug")),
            "artifact_ids": artifact_ids,
            "planned_android_commands": planned_android_commands,
            "workflow_run": workflow_run,
            "workflow_run_id": workflow_run.get("run_id", ""),
            "side_effects": ["workflow_graph_run"],
            "usage": {"android_commands": 0, "artifacts": len(artifact_ids)},
        }

    workflow_run: dict[str, Any] = {}
    side_effects: list[str] = []
    if config is not None and config.server_url:
        workflow_run = start_auto_listing_graph_run(task, config)
        side_effects.append("workflow_graph_run")

    return {
        "ok": True,
        "promote_mode": promote_mode,
        "dry_run": bool(governance.get("dry_run", promote_mode != "production")),
        "debug": bool(governance.get("debug")),
        "artifact_ids": artifact_ids,
        "planned_android_commands": planned_android_commands,
        "workflow_run": workflow_run,
        "workflow_run_id": workflow_run.get("run_id", ""),
        "side_effects": side_effects if promote_mode != "production" else ["planned_only"],
        "usage": {"android_commands": len(planned_android_commands), "artifacts": len(artifact_ids)},
    }


def execute_cli_task(
    task: dict[str, Any],
    *,
    config: WorkerConfig | None = None,
    allow_production: bool = False,
    allow_cli: bool = False,
    runtime_preparation: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    if not allow_cli:
        return {"ok": False, "error": "CLI execution disabled for this worker"}
    governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
    if governance.get("promote_mode") == "production" and not allow_production:
        return {"ok": False, "error": "production CLI execution disabled for this worker"}
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    command = command_from_inputs(inputs)
    return run_local_command(task, command, runtime_preparation=runtime_preparation, config=config)


def execute_langgraph_task(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    task = with_adapter_command(task, default_module="langgraph")
    return execute_cli_task(task, **kwargs)


def execute_crewai_task(task: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    task = with_adapter_command(task, default_module="crewai")
    return execute_cli_task(task, **kwargs)


def with_adapter_command(task: dict[str, Any], *, default_module: str) -> dict[str, Any]:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    if inputs.get("command"):
        return task
    module = str(inputs.get("module") or default_module).strip()
    args = inputs.get("args") if isinstance(inputs.get("args"), list) else []
    next_task = dict(task)
    next_inputs = dict(inputs)
    next_inputs["command"] = [sys.executable, "-m", module, *[str(item) for item in args]]
    next_task["inputs"] = next_inputs
    return next_task


def command_from_inputs(inputs: dict[str, Any]) -> list[str]:
    command = inputs.get("command")
    if isinstance(command, list):
        return [str(item) for item in command if str(item)]
    if isinstance(command, str) and command.strip():
        return shlex.split(command, posix=os.name != "nt")
    raise ValueError("CLI task requires inputs.command")


def run_local_command(
    task: dict[str, Any],
    command: list[str],
    *,
    runtime_preparation: dict[str, Any] | None = None,
    config: WorkerConfig | None = None,
) -> dict[str, Any]:
    if not command:
        raise ValueError("empty command")
    profile = task.get("runtime_profile") if isinstance(task.get("runtime_profile"), dict) else {}
    workspace_root = workspace_root_path(profile)
    workspace_root.mkdir(parents=True, exist_ok=True)
    cwd_value = str((task.get("inputs") if isinstance(task.get("inputs"), dict) else {}).get("cwd") or ".")
    cwd = resolve_under_workspace(workspace_root, cwd_value)
    cwd.mkdir(parents=True, exist_ok=True)
    allowed = command_allowed(command[0], profile.get("allowed_local_commands"))
    if not allowed:
        return {"ok": False, "error": f"command not allowed by runtime profile: {command[0]}"}
    budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
    timeout = int(budget.get("max_runtime_seconds") or 60)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=max(1, timeout),
        env=worker_task_environment(config, workspace_root),
    )
    runtime_seconds = max(0, int(time.monotonic() - started))
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
        "runtime_preparation": runtime_preparation or {},
        "usage": {"runtime_seconds": runtime_seconds, "artifacts": 0, "android_commands": 0, "retries": 0},
    }


def command_allowed(executable: str, allowed_value: object) -> bool:
    allowed = {str(item).strip().lower() for item in (allowed_value if isinstance(allowed_value, list) else ["python"]) if str(item).strip()}
    name = Path(executable).name.lower()
    stem = Path(executable).stem.lower()
    return executable.lower() in allowed or name in allowed or stem in allowed


TASK_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "workflow.execute.auto_listing": execute_auto_listing,
    "local.cli.run": execute_cli_task,
    "langgraph.run": execute_langgraph_task,
    "crewai.run": execute_crewai_task,
}


def run_once(config: WorkerConfig) -> dict[str, Any]:
    flushed_before = flush_outbox(config)
    heartbeat = post_json(config.server_url, "/worker/heartbeat", heartbeat_payload(config), token=config.token)
    tasks = heartbeat.get("tasks") if isinstance(heartbeat.get("tasks"), list) else []
    results = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task = dict(task)
        task["worker_id"] = heartbeat.get("worker_id") or config.worker_id
        result_payload = execute_task(
            task,
            config=config,
            allow_production=config.allow_production,
            allow_cli=config.allow_cli,
            prepare_runtime=config.prepare_runtime,
        )
        spooled = spool_result(worker_outbox_dir(config), result_payload)
        flushed_after = flush_outbox(config)
        results.append({"task_id": result_payload.get("task_id"), "spooled": str(spooled), "flushed": flushed_after})
    return {"heartbeat": heartbeat, "flushed_before": flushed_before, "results": results}


def run_loop(config: WorkerConfig) -> None:
    if config.pairing_token:
        runtime_token = pair_worker(config)
        config = replace(config, token=runtime_token, pairing_token="")
    if config.auto_update and config.update_manifest_url:
        update_result = check_and_apply_update(config)
        if update_result.get("updated"):
            print(json.dumps({"ok": True, "worker_update": update_result, "restart_required": True}, ensure_ascii=False), flush=True)
            return
        print(json.dumps({"ok": True, "worker_update": update_result}, ensure_ascii=False), flush=True)
    consecutive_errors = 0
    while True:
        try:
            result = run_once(config)
            consecutive_errors = 0
            print(json.dumps(result, ensure_ascii=False), flush=True)
            runtime_state = load_runtime_state(config)
            runtime_state.update(
                {
                    "server_url": config.server_url,
                    "worker_id": config.worker_id,
                    "workspace_id": config.workspace_id,
                    "last_ok_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            if config.token:
                runtime_state.setdefault("token", config.token)
            save_runtime_state(config, runtime_state)
            if config.once:
                return
            time.sleep(max(0.5, config.interval_seconds))
        except Exception as exc:
            consecutive_errors += 1
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                        "consecutive_errors": consecutive_errors,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if config.once or (config.max_consecutive_errors and consecutive_errors >= config.max_consecutive_errors):
                raise
            time.sleep(max(0.5, config.error_backoff_seconds))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a lightweight SpiritKin Remote Worker.")
    parser.add_argument("--config", default="", help="Read worker settings from a JSON file.")
    parser.add_argument("--write-config", default="", help="Write the effective non-secret worker config JSON and exit.")
    parser.add_argument("--include-token-in-config", action="store_true", help="Include bearer token when writing --write-config.")
    parser.add_argument("--release-manifest", default="", help="Write signed/versioned worker package metadata JSON and exit.")
    parser.add_argument("--package-zip", default="", help="Build an installable Worker zip package and exit.")
    parser.add_argument("--worker-executable", default="", help="Optional PyInstaller one-file Worker to include in --package-zip.")
    parser.add_argument(
        "--manifest-signing-secret-env",
        default="SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET",
        help="Environment variable containing optional HMAC signing secret for --release-manifest.",
    )
    parser.add_argument("--server", default="", help="Control plane base URL, for example http://127.0.0.1:8791")
    parser.add_argument("--workspace-id", default="")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--account-id", default="", help="Optional account/tenant id shown in worker diagnostics.")
    parser.add_argument("--capability", action="append", dest="capabilities", help="Capability to advertise. Repeatable.")
    parser.add_argument("--token", default="", help="Optional bearer token for deployments that protect worker endpoints.")
    parser.add_argument("--pairing-token", default="", help="Bind this worker with a remote_worker pairing token before heartbeat.")
    parser.add_argument("--proxy-url", default="", help="Optional local-only HTTP(S) proxy for subprocess tasks on this worker.")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-production", action="store_true", help="Permit production-mode handlers to perform real side effects when implemented.")
    parser.add_argument("--allow-cli", action="store_true", help="Enable governed local CLI/LangGraph/CrewAI subprocess adapters.")
    parser.add_argument("--prepare-runtime", action="store_true", help="Create workspace root and Python venv before task execution.")
    parser.add_argument("--state-dir", default="", help="Directory for worker runtime state, including the saved bound token.")
    parser.add_argument("--outbox-dir", default="", help="Directory for durable worker result outbox. Defaults to state/workers/<worker-id>/outbox.")
    parser.add_argument("--max-consecutive-errors", type=int, default=0, help="Exit after this many loop errors. 0 retries forever.")
    parser.add_argument("--error-backoff", type=float, default=10.0, help="Seconds to wait after transient loop errors.")
    parser.add_argument("--update-manifest-url", default="", help="Hosted Worker package manifest URL for update checks.")
    parser.add_argument("--update-install-dir", default="", help="Directory to expand a downloaded Worker package into. Defaults to this script's repo root.")
    parser.add_argument("--auto-update", action="store_true", help="Check --update-manifest-url before the worker loop and exit after applying an update.")
    parser.add_argument("--check-update", action="store_true", help="Check/apply --update-manifest-url once and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_sources(args)
    if not config.server_url:
        if args.package_zip:
            secret = os.environ.get(args.manifest_signing_secret_env, "")
            path = build_worker_package(args.package_zip, signing_secret=secret, worker_executable=args.worker_executable or None)
            print(json.dumps({"ok": True, "package_zip": str(path)}, ensure_ascii=False), flush=True)
            return 0
        if args.release_manifest:
            secret = os.environ.get(args.manifest_signing_secret_env, "")
            path = write_worker_release_manifest(args.release_manifest, signing_secret=secret)
            print(json.dumps({"ok": True, "release_manifest": str(path)}, ensure_ascii=False), flush=True)
            return 0
        if args.check_update:
            result = check_and_apply_update(config)
            print(json.dumps({"ok": True, "worker_update": result}, ensure_ascii=False), flush=True)
            return 0
        raise SystemExit("--server is required unless provided by --config")
    if args.write_config:
        path = write_config_file(config, args.write_config, include_token=bool(args.include_token_in_config))
        print(json.dumps({"ok": True, "config_file": str(path)}, ensure_ascii=False), flush=True)
        return 0
    if args.release_manifest:
        secret = os.environ.get(args.manifest_signing_secret_env, "")
        path = write_worker_release_manifest(args.release_manifest, signing_secret=secret)
        print(json.dumps({"ok": True, "release_manifest": str(path)}, ensure_ascii=False), flush=True)
        return 0
    if args.package_zip:
        secret = os.environ.get(args.manifest_signing_secret_env, "")
        path = build_worker_package(args.package_zip, signing_secret=secret, worker_executable=args.worker_executable or None)
        print(json.dumps({"ok": True, "package_zip": str(path)}, ensure_ascii=False), flush=True)
        return 0
    if args.check_update:
        result = check_and_apply_update(config)
        print(json.dumps({"ok": True, "worker_update": result}, ensure_ascii=False), flush=True)
        return 0
    run_loop(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
