from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.desktop_state import load_desktop_state, save_desktop_state
from backend.app.learning_workflow import build_learning_workflow_report
from backend.app.service_ports import build_default_service_env, resolve_service_port
from backend.state_store import resolve_state_path

DEFAULT_SERVICE_STATE_PATH = "state/desktop_console/services.json"
DEFAULT_SERVICE_ACTION_LOG = "state/desktop_console/service_actions.jsonl"
DEFAULT_LOG_DIRECTORIES = ("state/logs", "tmp")
GATEWAY_SELF_ACTION_DELAY_SECONDS = 4.0


@dataclass(frozen=True)
class ManagedService:
    service_id: str
    label: str
    port: int
    command: tuple[str, ...]
    working_directory: str = "."
    health_path: str = ""
    process_match: str = ""
    autostart: bool = True
    enabled: bool = True
    description: str = ""

    def snapshot(self, *, host: str = "127.0.0.1", pid_map: dict[int, int] | None = None) -> dict[str, Any]:
        pid = _pid_for_service(self, pid_map=pid_map)
        open_state = _service_running(self, host=host, pid_map=pid_map)
        return {
            "service_id": self.service_id,
            "label": self.label,
            "host": host,
            "port": self.port,
            "status": "running" if open_state else "stopped",
            "pid": pid,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "health_url": f"http://{host}:{self.port}{self.health_path}" if self.health_path and self.port > 0 else "",
            "process_match": self.process_match,
            "autostart": self.autostart,
            "enabled": self.enabled,
            "description": self.description,
        }


@dataclass(frozen=True)
class LogFileSnapshot:
    log_id: str
    path: str
    size_bytes: int
    updated_at: float
    error_count: int = 0
    warning_count: int = 0
    tail: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "updated_at": self.updated_at,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "tail": list(self.tail),
        }


@dataclass(frozen=True)
class OperationsSnapshot:
    generated_at: float
    services: tuple[dict[str, Any], ...]
    logs: tuple[dict[str, Any], ...]
    sync: dict[str, Any]
    daily: dict[str, Any]
    actions: tuple[dict[str, Any], ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "services": list(self.services),
            "logs": list(self.logs),
            "sync": dict(self.sync),
            "daily": dict(self.daily),
            "actions": list(self.actions),
        }


def default_managed_services() -> tuple[ManagedService, ...]:
    frontend_port = resolve_service_port("frontend", 8787)
    events_port = resolve_service_port("event_bridge", 8765)
    command_port = resolve_service_port("command_gateway", 8788)
    remote_worker_port = resolve_service_port("remote_worker", 8790)
    android_port = resolve_service_port("android_endpoint", 8791)
    ios_port = resolve_service_port("ios_endpoint", 8792)
    command_gateway_script = "from backend.app.codex_work_events import install; install(); from backend.app.command_gateway import main; main()"
    return (
        ManagedService(
            "frontend",
            "前端静态服务",
            frontend_port,
            ("python", "-m", "backend.app.static_frontend_server", "--port", str(frontend_port), "--host", "127.0.0.1", "--directory", "frontend"),
            description="avatar_3d.html、桌宠页面和前端调试入口。",
        ),
        ManagedService(
            "event_bridge",
            "事件 WebSocket",
            events_port,
            ("python", "-m", "backend.app.realtime_bridge"),
            description="把运行事件同步给 3D 形象和桌面端。",
        ),
        ManagedService(
            "command_gateway",
            "命令网关",
            command_port,
            ("python", "-u", "-c", command_gateway_script),
            health_path="/health",
            description="桌面端、移动端和前端调用主 Agent 的 HTTP 网关。",
        ),
        ManagedService(
            "voice_session",
            "桌面语音监听",
            0,
            (
                "python",
                "-u",
                "-m",
                "backend.perception.audio.realtime_session",
                "--strict-hotword",
                "--max-turns",
                "0",
                "--idle-timeouts",
                "0",
            ),
            process_match="backend.perception.audio.realtime_session",
            autostart=False,
            description="麦克风热词监听、ASR、主 Agent 输入和后端 Edge TTS 播放。",
        ),
        ManagedService(
            "remote_worker",
            "远端 Worker",
            remote_worker_port,
            ("python", "-m", "backend.remote.worker"),
            autostart=False,
            enabled=True,
            description="用于远端 Skill 包执行和子模块控制。",
        ),
        ManagedService(
            "android_endpoint",
            "Android Control Bridge",
            android_port,
            ("python", "-m", "backend.mobile.android_endpoint"),
            health_path="/android/health",
            autostart=False,
            enabled=True,
            description="Android Companion/链接分享接收、heartbeat 和命令队列入口。",
        ),
        ManagedService(
            "ios_endpoint",
            "iOS 控制入口",
            ios_port,
            ("python", "-m", "backend.mobile.ios_endpoint"),
            health_path="/ios/health",
            autostart=False,
            enabled=True,
            description="iOS Shortcuts/PWA 控制入口。",
        ),
    )


def build_operations_snapshot(*, root: str | os.PathLike[str] | None = None) -> OperationsSnapshot:
    project_root = Path(root or Path.cwd()).resolve()
    services = tuple(build_service_snapshots())
    logs = tuple(log.snapshot() for log in list_project_logs(project_root=project_root))
    sync = build_sync_snapshot()
    daily = build_daily_snapshot(project_root=project_root, logs=logs)
    return OperationsSnapshot(time.time(), services, logs, sync, daily, tuple(list_service_actions(limit=30)))


def build_services_snapshot() -> dict[str, Any]:
    return {"services": build_service_snapshots(), "actions": list_service_actions(limit=20)}


def build_service_snapshots(*, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    services = list(load_managed_services())
    pid_map = _pid_map_for_ports([service.port for service in services if service.port > 0])
    return [service.snapshot(host=host, pid_map=pid_map) for service in services]


def handle_service_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    service_id = str(payload.get("service_id") or "").strip()
    if action not in {"start", "stop", "restart"}:
        raise ValueError("service action must be start, stop, or restart")
    service = _find_service(service_id)
    if service is None:
        raise ValueError(f"unknown service_id: {service_id}")
    gateway_self_action = service.service_id == "command_gateway" and action in {"stop", "restart"}
    service_snapshot: dict[str, Any] | None = None
    services_snapshot: list[dict[str, Any]] | None = None
    if gateway_self_action:
        # Finish the comparatively expensive process/health probes before the
        # detached helper starts its countdown to terminate this process.
        service_snapshot = service.snapshot(pid_map=_pid_map_for_ports([service.port]))
        services_snapshot = build_service_snapshots()
        result = _schedule_gateway_self_action(service, action)
    elif action == "start":
        result = _start_service(service, allow_disabled=True)
    elif action == "stop":
        result = _stop_service(service)
    else:
        stopped = _stop_service(service)
        time.sleep(0.6)
        started = _start_service(service, allow_disabled=True)
        result = {
            "ok": bool(started.get("ok")),
            "status": started.get("status", "unknown"),
            "message": f"{stopped.get('message', '')} {started.get('message', '')}".strip(),
        }
    action_record = record_service_action(action=action, service=service, result=result)
    return {
        **result,
        "service_id": service.service_id,
        "service": service_snapshot or service.snapshot(pid_map=_pid_map_for_ports([service.port])),
        "services": services_snapshot if services_snapshot is not None else build_service_snapshots(),
        "action_record": action_record,
    }


def list_project_logs(
    *,
    project_root: str | os.PathLike[str] | Path | None = None,
    tail_lines: int = 80,
) -> list[LogFileSnapshot]:
    root = Path(project_root or Path.cwd()).resolve()
    results: list[LogFileSnapshot] = []
    for relative_dir in DEFAULT_LOG_DIRECTORIES:
        directory = root / relative_dir
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.log"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)[:40]:
            try:
                stat = path.stat()
                tail = _tail_text(path, limit=tail_lines)
            except OSError:
                continue
            lower_lines = [line.lower() for line in tail]
            results.append(
                LogFileSnapshot(
                    log_id=_log_id_for_path(root, path),
                    path=str(path),
                    size_bytes=stat.st_size,
                    updated_at=stat.st_mtime,
                    error_count=sum(1 for line in lower_lines if "error" in line or "exception" in line or "traceback" in line or "失败" in line),
                    warning_count=sum(1 for line in lower_lines if "warning" in line or "warn" in line or "警告" in line),
                    tail=tuple(tail),
                )
            )
    return results


def build_logs_snapshot(log_id: str = "") -> dict[str, Any]:
    logs = [log.snapshot() for log in list_project_logs()]
    selected = None
    if log_id:
        selected = next((item for item in logs if item["log_id"] == log_id), None)
    if selected is None and logs:
        selected = logs[0]
    return {"logs": logs, "selected": selected}


def build_sync_snapshot() -> dict[str, Any]:
    state = load_desktop_state()
    events = list(state.get("events") or [])
    clients: dict[str, int] = {}
    for event in events:
        if isinstance(event, dict):
            client = str(event.get("client_id") or event.get("source") or event.get("updated_by") or "unknown")
            clients[client] = clients.get(client, 0) + 1
    return {
        "revision": state.get("revision"),
        "updated_by": state.get("updated_by"),
        "updated_at": state.get("updated_at"),
        "active_session_id": state.get("active_session_id"),
        "session_count": len(state.get("sessions") or []),
        "project_count": len(state.get("projects") or []),
        "task_count": len(state.get("tasks") or []),
        "event_count": len(events),
        "pending": state.get("pending"),
        "clients": [{"client_id": key, "event_count": value} for key, value in sorted(clients.items())],
        "last_event": events[-1] if events else None,
    }


def handle_sync_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "refresh").strip().lower()
    state = load_desktop_state()
    if action == "refresh":
        return {"ok": True, "sync": build_sync_snapshot()}
    if action == "clear_events":
        state["events"] = []
    elif action == "clear_pending":
        state["pending"] = None
    elif action == "compact_sessions":
        for session in state.get("sessions") or []:
            if isinstance(session, dict):
                session["messages"] = list(session.get("messages") or [])[-80:]
    else:
        raise ValueError(f"unsupported sync action: {action}")
    saved = save_desktop_state({**state, "revision": int(state.get("revision") or 0) + 1, "updated_by": "operations_center"})
    return {"ok": True, "sync": build_sync_snapshot(), "state": saved}


def build_daily_snapshot(*, project_root: str | os.PathLike[str] | Path | None = None, logs: tuple[dict[str, Any], ...] | None = None) -> dict[str, Any]:
    state = load_desktop_state()
    now = time.time()
    today_start = _local_day_start(now)
    tasks = [item for item in state.get("tasks") or [] if isinstance(item, dict)]
    today_tasks = [item for item in tasks if float(item.get("updated_at") or item.get("created_at") or 0) >= today_start]
    by_status: dict[str, int] = {}
    for item in tasks:
        status = str(item.get("status") or "pending")
        by_status[status] = by_status.get(status, 0) + 1
    learning = build_learning_workflow_report(include_improvement=False).snapshot()
    records = [item for item in learning.get("records") or [] if isinstance(item, dict)]
    today_learning = [item for item in records if float(item.get("created_at") or 0) >= today_start]
    log_items = list(logs or [log.snapshot() for log in list_project_logs(project_root=project_root)])
    error_logs = [item for item in log_items if int(item.get("error_count") or 0) > 0]
    services = build_service_snapshots()
    return {
        "date": time.strftime("%Y-%m-%d", time.localtime(now)),
        "task_total": len(tasks),
        "today_task_count": len(today_tasks),
        "task_status_counts": by_status,
        "today_learning_count": len(today_learning),
        "learning_dataset_count": int((learning.get("dataset") or {}).get("count") or 0),
        "open_error_log_count": len(error_logs),
        "running_service_count": sum(1 for item in services if item.get("status") == "running"),
        "stopped_service_count": sum(1 for item in services if item.get("status") != "running"),
        "items": _daily_items(today_tasks, today_learning, error_logs, services),
    }


def load_managed_services(path: str | os.PathLike[str] | None = None) -> tuple[ManagedService, ...]:
    target = _service_state_path(path)
    defaults = {service.service_id: service for service in default_managed_services()}
    if not target.exists():
        return tuple(defaults.values())
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return tuple(defaults.values())
    services: dict[str, ManagedService] = dict(defaults)
    for item in payload.get("services") or []:
        if not isinstance(item, dict):
            continue
        service_id = str(item.get("service_id") or item.get("id") or "").strip()
        if not service_id:
            continue
        base = services.get(service_id)
        command = tuple(str(part) for part in (item.get("command") or (base.command if base else ())))
        services[service_id] = ManagedService(
            service_id=service_id,
            label=str(item.get("label") or (base.label if base else service_id)),
            port=int(item.get("port") or (base.port if base else 0)),
            command=command,
            working_directory=str(item.get("working_directory") or (base.working_directory if base else ".")),
            health_path=str(item.get("health_path") or (base.health_path if base else "")),
            process_match=str(item.get("process_match") or (base.process_match if base else "")),
            autostart=bool(item.get("autostart", base.autostart if base else True)),
            enabled=bool(item.get("enabled", base.enabled if base else True)),
            description=str(item.get("description") or (base.description if base else "")),
        )
    return tuple(services.values())


def _find_service(service_id: str) -> ManagedService | None:
    for service in load_managed_services():
        if service.service_id == service_id:
            return service
    return None


def list_service_actions(*, limit: int = 50) -> list[dict[str, Any]]:
    target = _service_action_log_path()
    if not target.exists():
        return []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines[-max(1, int(limit)) :]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def record_service_action(*, action: str, service: ManagedService, result: dict[str, Any]) -> dict[str, Any]:
    record = {
        "created_at": time.time(),
        "action": action,
        "service_id": service.service_id,
        "label": service.label,
        "port": service.port,
        "ok": bool(result.get("ok")),
        "status": str(result.get("status") or ""),
        "message": str(result.get("message") or "")[:500],
        "pid": result.get("pid"),
    }
    target = _service_action_log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        return record
    return record


def _start_service(service: ManagedService, *, allow_disabled: bool = False) -> dict[str, Any]:
    if _service_running(service):
        message = "服务已在监听。" if service.port > 0 else "服务已在运行。"
        return {"ok": True, "status": "already_running", "message": message}
    if not service.enabled and not allow_disabled:
        return {"ok": False, "status": "disabled", "message": "服务已禁用，未启动。"}
    command = list(service.command)
    if not command:
        return {"ok": False, "status": "missing_command", "message": "未配置启动命令。"}
    root = Path.cwd().resolve()
    cwd = Path(service.working_directory)
    if not cwd.is_absolute():
        cwd = root / cwd
    log_dir = root / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_file = open(log_dir / f"ops_{service.service_id}.out.log", "ab")
    err_file = open(log_dir / f"ops_{service.service_id}.err.log", "ab")
    env = os.environ.copy()
    env.update({key: value for key, value in build_default_service_env(host="127.0.0.1").items() if not env.get(key)})
    env.setdefault("SPIRITKIN_ASR_MIN_RMS", "700")
    env.setdefault("SPIRITKIN_ASR_NO_SPEECH_THRESHOLD", "0.96")
    env.setdefault("SPIRITKIN_ASR_LOW_LOGPROB_THRESHOLD", "-2.0")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=out_file,
            stderr=err_file,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        out_file.close()
        err_file.close()
        return {"ok": False, "status": "start_failed", "message": f"{type(exc).__name__}: {exc}"}
    for _ in range(25):
        if _service_running(service):
            return {"ok": True, "status": "started", "pid": process.pid, "message": "服务已启动。"}
        time.sleep(0.2)
        if service.port <= 0 and process.poll() is not None:
            return {"ok": False, "status": "start_failed", "pid": process.pid, "message": f"启动命令已退出，exit={process.returncode}。"}
    message = "启动命令已发出，但暂未检测到进程。" if service.port <= 0 else "启动命令已发出，但端口暂未监听。"
    return {"ok": False, "status": "start_timeout", "pid": process.pid, "message": message}


def _schedule_gateway_self_action(service: ManagedService, action: str) -> dict[str, Any]:
    current_pid = os.getpid()
    root = Path.cwd().resolve()
    payload = {
        "action": action,
        "pid": current_pid,
        "command": list(service.command),
        "cwd": str(root),
        "env": os.environ.copy(),
        "out": str(root / "state" / "logs" / "ops_command_gateway.out.log"),
        "err": str(root / "state" / "logs" / "ops_command_gateway.err.log"),
        "delay_seconds": GATEWAY_SELF_ACTION_DELAY_SECONDS,
    }
    helper = (
        "import json,os,subprocess,sys,time\n"
        "p=json.loads(sys.argv[1])\n"
        f"time.sleep(float(p.get('delay_seconds',{GATEWAY_SELF_ACTION_DELAY_SECONDS})))\n"
        "subprocess.run(['taskkill','/PID',str(p['pid']),'/F'],capture_output=True,text=True)\n"
        "time.sleep(0.7)\n"
        "if p.get('action')=='restart':\n"
        "    os.makedirs(os.path.dirname(p['out']),exist_ok=True)\n"
        "    out=open(p['out'],'ab')\n"
        "    err=open(p['err'],'ab')\n"
        "    subprocess.Popen(p['command'],cwd=p['cwd'],env=p.get('env') or None,stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", helper, json.dumps(payload)],
            cwd=str(root),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as exc:
        return {"ok": False, "status": "schedule_failed", "pid": current_pid, "message": f"{type(exc).__name__}: {exc}"}
    status = "restart_scheduled" if action == "restart" else "stop_scheduled"
    message = "命令网关将在当前响应返回后自动重启。" if action == "restart" else "命令网关将在当前响应返回后停止。"
    return {"ok": True, "status": status, "pid": current_pid, "message": message}


def _stop_service(service: ManagedService) -> dict[str, Any]:
    pid = _pid_for_service(service)
    if pid is None:
        return {"ok": True, "status": "already_stopped", "message": "服务未运行。"}
    try:
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=10, check=False, **_hidden_run_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "status": "stop_failed", "message": f"{type(exc).__name__}: {exc}"}
    ok = result.returncode == 0
    return {
        "ok": ok,
        "status": "stopped" if ok else "stop_failed",
        "pid": pid,
        "message": (result.stdout or result.stderr or "").strip(),
    }


def _device_probes_disabled() -> bool:
    if "pytest" in sys.modules:
        return True
    return os.getenv("SPIRITKIN_DISABLE_DEVICE_PROBES", "").strip().lower() in {"1", "true", "yes", "on"}


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    if int(port) <= 0:
        return False
    if _device_probes_disabled():
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_for_port(port: int) -> int | None:
    if int(port) <= 0:
        return None
    if os.name == "nt":
        return _pid_map_for_ports([port]).get(port)
    return None


def _service_running(service: ManagedService, *, host: str = "127.0.0.1", pid_map: dict[int, int] | None = None) -> bool:
    if service.port > 0:
        return _port_open(host, service.port)
    return _pid_for_service(service, pid_map=pid_map) is not None


def _pid_for_service(service: ManagedService, *, pid_map: dict[int, int] | None = None) -> int | None:
    if service.port > 0:
        return pid_map.get(service.port) if pid_map is not None else _pid_for_port(service.port)
    if service.process_match:
        return _pid_for_process_match(service.process_match)
    return None


def _pid_for_process_match(process_match: str) -> int | None:
    if _device_probes_disabled():
        return None
    needle = str(process_match or "").strip().lower()
    if not needle:
        return None
    if os.name == "nt":
        script = (
            "$needle = " + json.dumps(needle) + "\n"
            "$current = $PID\n"
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.ProcessId -ne $current -and $_.CommandLine.ToLowerInvariant().Contains($needle) } | "
            "Select-Object -First 1 -ExpandProperty ProcessId\n"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", _powershell_encoded(script)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                **_hidden_run_kwargs(),
            )
        except Exception:
            return None
        for line in (result.stdout or "").splitlines():
            try:
                return int(line.strip())
            except ValueError:
                continue
        return None
    try:
        result = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return None
    current_pid = os.getpid()
    for line in (result.stdout or "").splitlines():
        if needle not in line.lower():
            continue
        try:
            pid = int(line.strip().split(None, 1)[0])
        except (ValueError, IndexError):
            continue
        if pid != current_pid:
            return pid
    return None


def _powershell_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _hidden_run_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _pid_map_for_ports(ports: list[int]) -> dict[int, int]:
    wanted = {int(port) for port in ports if int(port) > 0}
    if not wanted:
        return {}
    if _device_probes_disabled():
        return {}
    try:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=5, check=False, **_hidden_run_kwargs())
    except (OSError, subprocess.SubprocessError):
        return {}
    mapping: dict[int, int] = {}
    for line in (result.stdout or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            port = int(parts[1].rsplit(":", 1)[1])
            pid = int(parts[-1])
        except (IndexError, ValueError):
            continue
        if port in wanted and pid:
            mapping.setdefault(port, pid)
    return mapping


def _tail_text(path: Path, *, limit: int = 80, max_bytes: int = 96_000) -> list[str]:
    try:
        with path.open("rb") as handle:
            if path.stat().st_size > max_bytes:
                handle.seek(-max_bytes, os.SEEK_END)
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-limit:]


def _log_id_for_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return str(relative).replace("\\", "/")


def _local_day_start(now: float) -> float:
    local = time.localtime(now)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, local.tm_wday, local.tm_yday, local.tm_isdst))


def _daily_items(
    today_tasks: list[dict[str, Any]],
    today_learning: list[dict[str, Any]],
    error_logs: list[dict[str, Any]],
    services: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in today_tasks[-10:]:
        task_id = str(task.get("id") or "").strip()
        title = str(task.get("title") or task_id or "task")
        target = task_id or title
        items.append({"id": f"task:{target}", "type": "task", "title": title, "target": target, "status": str(task.get("status") or "pending")})
    for record in today_learning[-10:]:
        skill_name = str(record.get("skill_name") or "learning")
        created_at = str(record.get("created_at") or "")
        target = str(record.get("id") or skill_name)
        items.append({"id": f"learning:{target}:{created_at}", "type": "learning", "title": skill_name, "target": target, "status": str(record.get("source") or "recorded")})
    for log in error_logs[:8]:
        log_id = str(log.get("log_id") or log.get("path") or "").strip()
        title = log_id or "log"
        items.append({"id": f"log_error:{title}", "type": "log_error", "title": title, "target": title, "status": f"errors={log.get('error_count')}"})
    for service in services:
        if service.get("status") != "running":
            service_id = str(service.get("service_id") or "").strip()
            title = str(service.get("label") or service_id or "service")
            target = service_id or title
            items.append({"id": f"service:{target}", "type": "service", "title": title, "target": target, "status": "stopped"})
    return items


def _service_state_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SERVICE_STATE_PATH", DEFAULT_SERVICE_STATE_PATH, path)


def _service_action_log_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_SERVICE_ACTION_LOG", DEFAULT_SERVICE_ACTION_LOG, path)
