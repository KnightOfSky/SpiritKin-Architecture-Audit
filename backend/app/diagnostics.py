from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.desktop_state import load_desktop_state
from backend.app.service_ports import build_service_port_snapshot, resolve_service_port


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    ok: bool
    detail: str
    category: str = "runtime"
    severity: str = "info"
    payload: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "category": self.category,
            "severity": self.severity,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class RepairStep:
    step_id: str
    title: str
    status: str = "pending"
    command: str = ""
    detail: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "status": self.status,
            "command": self.command,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DiagnosticIssue:
    issue_id: str
    title: str
    severity: str
    detail: str
    repair_steps: tuple[RepairStep, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "title": self.title,
            "severity": self.severity,
            "detail": self.detail,
            "repair_steps": [step.snapshot() for step in self.repair_steps],
        }


@dataclass(frozen=True)
class DesktopDiagnosticsReport:
    generated_at: float
    checks: tuple[DiagnosticCheck, ...]
    issues: tuple[DiagnosticIssue, ...]
    service_ports: dict[str, int]
    desktop_state: dict[str, Any]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.severity in {"high", "critical"})

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ok": self.ok,
            "checks": [check.snapshot() for check in self.checks],
            "issues": [issue.snapshot() for issue in self.issues],
            "service_ports": dict(self.service_ports),
            "desktop_state": dict(self.desktop_state),
        }


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    if int(port) <= 0:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _command_version(command: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {type(exc).__name__}"
    text = (result.stdout or result.stderr or "").strip().splitlines()
    return text[0] if text else f"exit={result.returncode}"


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _webview2_installed() -> bool:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "EdgeWebView" / "Application",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft" / "EdgeWebView" / "Application",
    ]
    return any(candidate.exists() and any(candidate.iterdir()) for candidate in candidates)


def _git_status_counts(root: Path) -> dict[str, int | str]:
    try:
        result = subprocess.run(["git", "status", "--short", "--branch"], cwd=root, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": 0, "error": type(exc).__name__, "branch": "", "changed": 0, "untracked": 0, "ahead": 0, "behind": 0}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    branch_line = lines[0] if lines and lines[0].startswith("## ") else ""
    status_lines = lines[1:] if branch_line else lines
    branch = branch_line[3:].split("...", 1)[0].strip() if branch_line else ""
    return {
        "available": 1 if result.returncode == 0 else 0,
        "branch": branch,
        "changed": sum(1 for line in status_lines if not line.startswith("??")),
        "untracked": sum(1 for line in status_lines if line.startswith("??")),
        "ahead": _parse_branch_counter(branch_line, "ahead"),
        "behind": _parse_branch_counter(branch_line, "behind"),
    }


def _parse_branch_counter(branch_line: str, key: str) -> int:
    marker = f"{key} "
    if marker not in branch_line:
        return 0
    try:
        return int(branch_line.split(marker, 1)[1].split(",", 1)[0].split("]", 1)[0].strip())
    except (IndexError, ValueError):
        return 0


def _http_get_text(url: str, timeout: float = 0.75) -> tuple[bool, str]:
    try:
        from urllib import request

        with request.urlopen(url, timeout=timeout) as response:
            data = response.read(120_000).decode("utf-8", errors="replace")
            return 200 <= int(getattr(response, "status", 200)) < 400, data
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _manifest_model_path(project_root: Path, manifest_relative: str = "frontend/models/spirit3d/manifest.json") -> tuple[bool, str, Path | None, str]:
    manifest_path = project_root / manifest_relative
    if not manifest_path.exists():
        return False, manifest_relative, None, "manifest missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, manifest_relative, None, f"manifest invalid: {type(exc).__name__}"
    raw_model = str(manifest.get("model") or "").strip()
    if not raw_model:
        return False, manifest_relative, None, "manifest model field is empty"
    model_without_query = raw_model.split("?", 1)[0].replace("/", os.sep)
    model_path = (project_root / "frontend" / model_without_query).resolve()
    return model_path.exists(), raw_model, model_path, "exists" if model_path.exists() else "model file missing"


def _frontend_service_ok(host: str, port: int) -> tuple[bool, str]:
    ok, text = _http_get_text(f"http://{host}:{port}/avatar_3d.html")
    if not ok:
        return False, text
    if "avatar_3d" in text or "THREE" in text or "three" in text:
        return True, "avatar_3d.html served"
    return False, "port responded but did not serve avatar_3d.html"


def _command_gateway_ok(host: str, port: int) -> tuple[bool, str]:
    ok, text = _http_get_text(f"http://{host}:{port}/health")
    if not ok:
        return False, text
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return False, "health response is not JSON"
    if payload.get("ok") and payload.get("service") == "spiritkin-command-gateway":
        return True, "health ok"
    return False, "health response is not command gateway"


def _service_repair_command(service_id: str, action: str = "restart") -> str:
    return f"desktop-repair:service:{service_id}:{action}"


def build_desktop_diagnostics_report(
    *,
    root: str | os.PathLike[str] | None = None,
    host: str = "127.0.0.1",
    frontend_port: int | None = None,
    events_port: int | None = None,
    command_port: int | None = None,
) -> DesktopDiagnosticsReport:
    project_root = Path(root or Path.cwd()).resolve()
    checks: list[DiagnosticCheck] = []
    issues: list[DiagnosticIssue] = []

    frontend_port = int(frontend_port if frontend_port is not None else resolve_service_port("frontend", 8787))
    events_port = int(events_port if events_port is not None else resolve_service_port("event_bridge", 8765))
    command_port = int(command_port if command_port is not None else resolve_service_port("command_gateway", 8788))
    port_specs = {
        "frontend": frontend_port,
        "events": events_port,
        "command_gateway": command_port,
    }
    port_registry = build_service_port_snapshot(host=host, include_conflicts=False)
    duplicate_ports = dict(port_registry.get("duplicate_ports") or {})
    checks.append(DiagnosticCheck(
        name="service_port_registry",
        ok=not duplicate_ports,
        detail="no duplicate configured ports" if not duplicate_ports else json.dumps(duplicate_ports, ensure_ascii=False),
        category="service_port",
        severity="high",
        payload=port_registry,
    ))
    for port, service_ids in duplicate_ports.items():
        issues.append(DiagnosticIssue(
            issue_id=f"duplicate-port-{port}",
            title=f"多个服务声明同一端口：{port}",
            severity="high",
            detail=", ".join(str(item) for item in service_ids),
            repair_steps=(RepairStep("review-port-config", "检查端口环境变量或服务配置", command="desktop-repair:service_ports:review"),),
        ))

    for service, port in port_specs.items():
        ok = _port_open(host, port)
        checks.append(DiagnosticCheck(
            name=f"{service}:{port}",
            ok=ok,
            detail="listening" if ok else "not listening",
            category="service_port",
            severity="high",
            payload={"host": host, "port": port},
        ))
        if not ok:
            service_id = "event_bridge" if service == "events" else service
            issues.append(DiagnosticIssue(
                issue_id=f"port-{service}-{port}",
                title=f"{service} 服务未监听 {port}",
                severity="high",
                detail=f"{service} should listen on {host}:{port}",
                repair_steps=(
                    RepairStep(f"start-{service_id}", f"启动 {service}", command=_service_repair_command(service_id, "start")),
                    RepairStep("start-desktop-console", "重新启动桌面端服务", command="python scripts\\start_desktop_console.py"),
                ),
            ))

    if _port_open(host, frontend_port):
        ok, detail = _frontend_service_ok(host, frontend_port)
        checks.append(DiagnosticCheck(
            name=f"frontend_content:{frontend_port}",
            ok=ok,
            detail=detail,
            category="service_health",
            severity="high",
            payload={"host": host, "port": frontend_port},
        ))
        if not ok:
            issues.append(DiagnosticIssue(
                issue_id=f"port-conflict-frontend-{frontend_port}",
                title=f"frontend 端口 {frontend_port} 可能被错误服务占用",
                severity="high",
                detail=detail,
                repair_steps=(
                    RepairStep("restart-frontend", "停止占用进程并重启前端服务", command=_service_repair_command("frontend", "restart")),
                ),
            ))
    if _port_open(host, command_port):
        ok, detail = _command_gateway_ok(host, command_port)
        checks.append(DiagnosticCheck(
            name=f"command_gateway_health:{command_port}",
            ok=ok,
            detail=detail,
            category="service_health",
            severity="high",
            payload={"host": host, "port": command_port},
        ))
        if not ok:
            issues.append(DiagnosticIssue(
                issue_id=f"port-conflict-command_gateway-{command_port}",
                title=f"command gateway 端口 {command_port} 可能被错误服务占用",
                severity="high",
                detail=detail,
                repair_steps=(
                    RepairStep("restart-command-gateway", "停止占用进程并重启命令网关", command=_service_repair_command("command_gateway", "restart")),
                ),
            ))

    from backend.mobile.ios_channels import build_ios_channels_snapshot

    wechat = build_ios_channels_snapshot().get("wechat_ilink") or {}
    wechat_enabled = bool(wechat.get("enabled"))
    wechat_configured = bool(wechat.get("configured"))
    wechat_phase = str(wechat.get("phase") or "disabled")
    wechat_ok = not wechat_enabled or (wechat_configured and wechat_phase in {"running", "connecting"})
    checks.append(DiagnosticCheck(
        name="wechat_ilink",
        ok=wechat_ok,
        detail=str(wechat.get("message") or wechat_phase),
        category="channel",
        severity="medium",
        payload=dict(wechat),
    ))
    if wechat_enabled and not wechat_configured:
        issues.append(DiagnosticIssue(
            issue_id="wechat-ilink-configuration",
            title="微信 iLink 已启用但凭据不完整",
            severity="medium",
            detail="在桌面 Runtime 环境中配置 Bot Token、Bot ID 和 User ID；iOS 只读取脱敏状态。",
            repair_steps=(RepairStep(
                "configure-wechat-ilink",
                "配置微信 iLink 环境变量或凭据文件后重启命令网关",
                command="desktop-repair:service:command_gateway:restart",
            ),),
        ))
    elif wechat_enabled and not wechat_ok:
        issues.append(DiagnosticIssue(
            issue_id="wechat-ilink-offline",
            title="微信 iLink 通道未运行",
            severity="medium",
            detail=str(wechat.get("message") or "命令网关尚未建立 iLink 长轮询。"),
            repair_steps=(RepairStep(
                "restart-wechat-ilink",
                "重启命令网关并重新建立微信 iLink 通道",
                command="desktop-repair:service:command_gateway:restart",
            ),),
        ))

    dependency_specs = {
        "python": _command_version(["python", "--version"]),
        "dotnet": _command_version(["dotnet", "--version"]),
        "websockets": "installed" if _module_available("websockets") else "missing",
        "edge_tts": "installed" if _module_available("edge_tts") else "missing",
        "faster_whisper": "installed" if _module_available("faster_whisper") else "missing",
        "webview2": "installed" if _webview2_installed() else "missing",
    }
    for name, detail in dependency_specs.items():
        ok = not str(detail).startswith("missing") and not str(detail).startswith("unavailable")
        severity = "high" if name in {"python", "dotnet", "websockets", "webview2"} else "medium"
        checks.append(DiagnosticCheck(name=name, ok=ok, detail=str(detail), category="dependency", severity=severity))
        if not ok:
            repair = "pip install -r requirements.txt" if name not in {"dotnet", "webview2"} else ""
            issues.append(DiagnosticIssue(
                issue_id=f"dependency-{name}",
                title=f"依赖缺失：{name}",
                severity=severity,
                detail=str(detail),
                repair_steps=(RepairStep(f"install-{name}", f"安装或修复 {name}", command=repair),),
            ))

    required_paths = [
        "frontend/avatar_3d.html",
        "frontend/models/spirit3d/manifest.json",
        "frontend/models/spirit3d/reference/bangboo_pmx_glb_screen.glb",
        "desktop/SpiritKinDesktop/SpiritKinDesktop.csproj",
        "backend/app/command_gateway.py",
    ]
    for relative in required_paths:
        exists = (project_root / relative).exists()
        checks.append(DiagnosticCheck(relative, exists, "exists" if exists else "missing", category="project_file", severity="high"))
        if not exists:
            issues.append(DiagnosticIssue(
                issue_id=f"missing-{relative.replace('/', '-')}",
                title=f"项目文件缺失：{relative}",
                severity="high",
                detail="核心桌面端或后端文件缺失。",
            ))

    model_ok, raw_model, model_path, model_detail = _manifest_model_path(project_root)
    checks.append(DiagnosticCheck(
        "avatar_3d_manifest_model",
        model_ok,
        model_detail,
        category="avatar_model",
        severity="high",
        payload={"manifest_model": raw_model, "resolved_path": str(model_path or "")},
    ))
    if not model_ok:
        fallback = project_root / "frontend" / "models" / "spirit3d" / "reference" / "bangboo_pmx_glb_screen.glb"
        repair_steps = []
        if fallback.exists():
            repair_steps.append(RepairStep("repair-avatar-manifest", "把 manifest 指向当前 Bangboo GLB", command="desktop-repair:avatar_manifest"))
        repair_steps.append(RepairStep("verify-avatar-model", "检查 3D 模型资源路径", command="python -m json.tool frontend\\models\\spirit3d\\manifest.json"))
        issues.append(DiagnosticIssue(
            issue_id="avatar-model-missing",
            title="3D 模型资源未正确指向可用 GLB",
            severity="high",
            detail=f"{raw_model}: {model_detail}",
            repair_steps=tuple(repair_steps),
        ))

    desktop_state = load_desktop_state()
    state_summary = {
        "revision": desktop_state.get("revision"),
        "updated_by": desktop_state.get("updated_by"),
        "updated_at": desktop_state.get("updated_at"),
        "session_count": len(desktop_state.get("sessions") or []),
        "task_count": len(desktop_state.get("tasks") or []),
        "project_count": len(desktop_state.get("projects") or []),
    }
    checks.append(DiagnosticCheck("desktop_state", True, json.dumps(state_summary, ensure_ascii=False), category="sync", severity="info", payload=state_summary))

    git_counts = _git_status_counts(project_root)
    checks.append(DiagnosticCheck("git_worktree", True, json.dumps(git_counts, ensure_ascii=False), category="project", severity="info", payload=git_counts))
    dirty_count = int(git_counts.get("changed", 0) or 0) + int(git_counts.get("untracked", 0) or 0)
    if dirty_count > 0:
        severity = "medium" if dirty_count > 80 else "info"
        issues.append(DiagnosticIssue(
            issue_id="project-dirty-worktree",
            title="Git 工作区存在未提交改动",
            severity=severity,
            detail=f"branch={git_counts.get('branch') or '--'} changed={git_counts.get('changed')} untracked={git_counts.get('untracked')} ahead={git_counts.get('ahead')} behind={git_counts.get('behind')}",
            repair_steps=(RepairStep("review-git-status", "检查当前变更范围", command="git status --short"),),
        ))

    return DesktopDiagnosticsReport(
        generated_at=time.time(),
        checks=tuple(checks),
        issues=tuple(issues),
        service_ports=port_specs,
        desktop_state=state_summary,
    )


def handle_desktop_diagnostics_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "repair").strip().lower()
    if action not in {"repair", "repair_all", "self_repair"}:
        raise ValueError(f"unsupported diagnostics action: {action}")
    issue_id = str(payload.get("issue_id") or "").strip()
    report = build_desktop_diagnostics_report()
    issues = [issue for issue in report.issues if not issue_id or issue.issue_id == issue_id]
    if action in {"repair_all", "self_repair"} and not issue_id:
        issues = [issue for issue in report.issues if issue.severity in {"critical", "high"}]
    results = [_repair_issue(issue) for issue in issues]
    return {
        "ok": all(bool(item.get("ok", False)) for item in results) if results else True,
        "action": action,
        "issue_id": issue_id,
        "results": results,
        "diagnostics": build_desktop_diagnostics_report().snapshot(),
    }


def _repair_issue(issue: DiagnosticIssue) -> dict[str, Any]:
    for step in issue.repair_steps:
        command = step.command.strip()
        if command.startswith("desktop-repair:service:"):
            _, _, service_id, action = command.split(":", 3)
            return _repair_service(issue, service_id, action)
        if command == "desktop-repair:avatar_manifest":
            return _repair_avatar_manifest(issue)
    return {
        "ok": False,
        "issue_id": issue.issue_id,
        "status": "manual_required",
        "message": "该问题没有安全的一键修复动作。",
    }


def _repair_service(issue: DiagnosticIssue, service_id: str, action: str) -> dict[str, Any]:
    from backend.app.operations_center import handle_service_action

    try:
        result = handle_service_action({"service_id": service_id, "action": action})
    except Exception as exc:
        return {
            "ok": False,
            "issue_id": issue.issue_id,
            "status": "service_repair_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": bool(result.get("ok", False)),
        "issue_id": issue.issue_id,
        "status": result.get("status", "unknown"),
        "message": result.get("message", ""),
        "service_id": service_id,
    }


def _repair_avatar_manifest(issue: DiagnosticIssue) -> dict[str, Any]:
    root = Path.cwd().resolve()
    manifest_path = root / "frontend" / "models" / "spirit3d" / "manifest.json"
    fallback_model = "models/spirit3d/reference/bangboo_pmx_glb_screen.glb?v=bangboo-visor-panel-40"
    fallback_path = root / "frontend" / "models" / "spirit3d" / "reference" / "bangboo_pmx_glb_screen.glb"
    if not fallback_path.exists():
        return {
            "ok": False,
            "issue_id": issue.issue_id,
            "status": "fallback_missing",
            "message": str(fallback_path),
        }
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data["model"] = fallback_model
        data.setdefault("name", "Bangboo PMX Visor Panel")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "issue_id": issue.issue_id,
            "status": "manifest_write_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "issue_id": issue.issue_id,
        "status": "avatar_manifest_repaired",
        "message": fallback_model,
    }
