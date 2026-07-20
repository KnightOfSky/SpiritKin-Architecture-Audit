from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_INDEX = ROOT_DIR / "frontend" / "index.html"
FRONTEND_AVATAR = ROOT_DIR / "frontend" / "spirit_avatar.html"
FRONTEND_AVATAR_3D = ROOT_DIR / "frontend" / "avatar_3d.html"
FRONTEND_LIVE2D = ROOT_DIR / "frontend" / "live2d.html"
FRONTEND_DIR = ROOT_DIR / "frontend"
STATE_DIR = ROOT_DIR / "state"
RUN_DIR = STATE_DIR / "run"
DETACHED_STATE_FILE = RUN_DIR / "realtime_panel.json"
SERVICE_COMMAND_PATTERNS = {
    "bridge": "backend.app.realtime_bridge",
    "command_gateway": "backend.app.command_gateway",
    "frontend": "http.server",
    "runtime": "backend.main",
}


def build_startup_commands() -> dict[str, list[str]]:
    command_gateway_script = "from backend.app.codex_work_events import install; install(); from backend.app.command_gateway import main; main()"
    return {
        "bridge": [sys.executable, "-m", "backend.app.realtime_bridge"],
        "command_gateway": [sys.executable, "-u", "-c", command_gateway_script],
        "runtime": [sys.executable, "-m", "backend.main"],
    }


def _format_command(command: list[str]) -> str:
    return " ".join(command)


FAST_VOICE_ENV_DEFAULTS = {
    "SPIRITKIN_HOTWORD_FAST": "1",
    "SPIRITKIN_HOTWORD_BEAM_SIZE": "1",
    "SPIRITKIN_HOTWORD_VAD": "0",
    "SPIRITKIN_HOTWORD_TIMEOUT": "0.8",
    "SPIRITKIN_HOTWORD_PHRASE_TIME_LIMIT": "1.0",
    "SPIRITKIN_HOTWORD_PAUSE_THRESHOLD": "0.18",
    "SPIRITKIN_HOTWORD_PHRASE_THRESHOLD": "0.08",
    "SPIRITKIN_HOTWORD_NON_SPEAKING_DURATION": "0.08",
    "SPIRITKIN_VOICE_INTENT_MODE": "first",
    "SPIRIT_TEXT_MODE": "fast",
    "SPIRIT_TEXT_MAX_NEW_TOKENS": "256",
    "SPIRIT_ASR_BEAM_SIZE": "1",
    "SPIRIT_ASR_VAD_FILTER": "1",
    "SPIRIT_ASR_TEMPERATURE": "0",
    "SPIRITKIN_WAKE_ACK_ENABLED": "0",
    "SPIRITKIN_VOICE_ACK_ENABLED": "0",
}


def _build_child_env(fast_voice: bool = True) -> dict[str, str]:
    child_env = dict(os.environ)
    if fast_voice:
        for key, value in FAST_VOICE_ENV_DEFAULTS.items():
            child_env.setdefault(key, value)
    return child_env


def _apply_remote_worker_env(child_env: dict[str, str], *, url: str = "", node_id: str = "", token: str = "", aliases: str = "") -> None:
    if url.strip():
        child_env["SPIRITKIN_REMOTE_WORKER_URL"] = url.strip().rstrip("/")
    if node_id.strip():
        child_env["SPIRITKIN_REMOTE_WORKER_NODE_ID"] = node_id.strip()
    if token.strip():
        child_env["SPIRITKIN_REMOTE_WORKER_TOKEN"] = token.strip()
    if aliases.strip():
        child_env["SPIRITKIN_REMOTE_WORKER_ALIASES"] = aliases.strip()


def _build_frontend_server_command(host: str, port: int) -> list[str]:
    return [sys.executable, "-m", "http.server", str(port), "--bind", host, "--directory", str(FRONTEND_DIR)]


def build_detached_service_specs(
    commands: dict[str, list[str]],
    *,
    frontend_command: list[str],
    no_command_gateway: bool = False,
    no_frontend_server: bool = False,
    no_runtime: bool = False,
) -> list[tuple[str, list[str]]]:
    specs = [("bridge", commands["bridge"])]
    if not no_command_gateway:
        specs.append(("command_gateway", commands["command_gateway"]))
    if not no_frontend_server:
        specs.append(("frontend", frontend_command))
    if not no_runtime:
        specs.append(("runtime", commands["runtime"]))
    return specs


def _service_log_paths(service_name: str, log_dir: Path | None = None) -> tuple[Path, Path]:
    log_root = log_dir or STATE_DIR / "logs"
    return log_root / f"{service_name}.out.log", log_root / f"{service_name}.err.log"


def _port_accepts_connection(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((_browser_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def _recv_exact(sock: socket.socket, size: int, buffer: bytearray | None = None) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    if buffer:
        prefix_size = min(remaining, len(buffer))
        chunks.append(bytes(buffer[:prefix_size]))
        del buffer[:prefix_size]
        remaining -= prefix_size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_websocket_text_frame(sock: socket.socket, initial: bytes = b"") -> str:
    buffer = bytearray(initial)
    header = _recv_exact(sock, 2, buffer)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    payload_len = header[1] & 0x7F
    if payload_len == 126:
        payload_len = int.from_bytes(_recv_exact(sock, 2, buffer), "big")
    elif payload_len == 127:
        payload_len = int.from_bytes(_recv_exact(sock, 8, buffer), "big")
    mask = _recv_exact(sock, 4, buffer) if masked else b""
    payload = _recv_exact(sock, payload_len, buffer)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode != 1:
        return ""
    return payload.decode("utf-8", errors="replace")


def _write_websocket_text_frame(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(0x80 | len(payload))
    elif len(payload) <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(len(payload).to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(len(payload).to_bytes(8, "big"))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _websocket_bridge_healthy(host: str, port: int, timeout: float = 1.2, token: str = "") -> bool:
    browser_host = _browser_host(host)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {browser_host}:{int(port)}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")
    try:
        with socket.create_connection((browser_host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                response.extend(sock.recv(4096))
            headers, _, remainder = bytes(response).partition(b"\r\n\r\n")
            if b" 101 " not in headers.split(b"\r\n", 1)[0]:
                return False
            _write_websocket_text_frame(sock, json.dumps({"type": "runtime.auth", "token": token}, separators=(",", ":")))
            message = _read_websocket_text_frame(sock, remainder)
    except Exception:
        return False
    try:
        event = json.loads(message)
    except json.JSONDecodeError:
        return False
    return isinstance(event, dict) and event.get("type") == "runtime.snapshot"


def _tcp_port_is_listening(port: int, *, host: str = "127.0.0.1") -> bool:
    if port <= 0:
        return False
    if os.name == "nt":
        command = (
            f"$conn = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
            f"Where-Object {{ $_.LocalAddress -in @('{host}', '0.0.0.0', '::', '::1') }}; "
            "if ($conn) { '1' }"
        )
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        return "1" in result.stdout
    try:
        result = subprocess.run(["sh", "-c", f"netstat -an 2>/dev/null | grep ':{int(port)} ' | grep -i listen"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def _service_is_listening(service_name: str, *, bind_host: str, port: int, token: str = "") -> bool:
    if service_name == "bridge":
        return _websocket_bridge_healthy(bind_host, port, token=token)
    return _port_accepts_connection(bind_host, port)


def _detached_process_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _start_detached_process(service_name: str, command: list[str], *, child_env: dict[str, str]) -> subprocess.Popen:
    log_out, log_err = _service_log_paths(service_name)
    log_out.parent.mkdir(parents=True, exist_ok=True)
    stdout = log_out.open("ab", buffering=0)
    stderr = log_err.open("ab", buffering=0)
    try:
        return subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            env=child_env,
            stdout=stdout,
            stderr=stderr,
            **_detached_process_kwargs(),
        )
    finally:
        stdout.close()
        stderr.close()


def _service_probe_port(service_name: str, *, events_port: int, command_port: int, frontend_port: int) -> int | None:
    return {
        "bridge": events_port,
        "command_gateway": command_port,
        "frontend": frontend_port,
    }.get(service_name)


def _default_port_status_lines(*, bind_host: str = "127.0.0.1", events_port: int = 8765, command_port: int = 8788, frontend_port: int = 8787, token: str = "") -> list[str]:
    token = token or os.getenv("SPIRITKIN_MOBILE_TOKEN", "")
    runtime_pids = _known_service_pids("runtime")
    runtime_state = "running" if runtime_pids else "stopped"
    pid_label = ",".join(str(pid) for pid in runtime_pids) if runtime_pids else "-"
    return [
        "[status] 未找到 detached 启动记录；以下是默认端口探测。",
        f"[status] frontend:{frontend_port} port={'listening' if _port_accepts_connection(bind_host, frontend_port) else 'not-listening'}",
        f"[status] command_gateway:{command_port} port={'listening' if _port_accepts_connection(bind_host, command_port) else 'not-listening'}",
        f"[status] bridge:{events_port} port={'listening' if _service_is_listening('bridge', bind_host=bind_host, port=events_port, token=token) else 'not-listening'}",
        f"[status] runtime pid={pid_label} process={runtime_state}",
    ]


def _write_detached_state(
    service_records: list[dict[str, object]],
    *,
    bind_host: str,
    events_port: int,
    command_port: int,
    frontend_port: int,
    frontend_url: str,
    token: str = "",
    state_file: Path = DETACHED_STATE_FILE,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": time.time(),
        "bind_host": bind_host,
        "events_port": events_port,
        "command_port": command_port,
        "frontend_port": frontend_port,
        "frontend_url": frontend_url,
        "mobile_token": token,
        "services": service_records,
    }
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_detached_state(state_file: Path = DETACHED_STATE_FILE) -> dict[str, object]:
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _find_process_ids_by_command_substring(substring: str) -> list[int]:
    needle = substring.strip()
    if not needle:
        return []
    if os.name == "nt":
        escaped = needle.replace("'", "''")
        command = (
            f"$needle='{escaped}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.ProcessId -ne $PID -and "
            "$_.Name -match '^(python|pythonw)\\.exe$' -and "
            "$_.CommandLine -like \"*$needle*\" "
            "} | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        pids: list[int] = []
        for line in result.stdout.splitlines():
            try:
                pids.append(int(line.strip()))
            except ValueError:
                continue
        return sorted(set(pids))
    try:
        result = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and needle in parts[1]:
            try:
                pids.append(int(parts[0]))
            except ValueError:
                continue
    return sorted(set(pids))


def _known_service_pids(service_name: str) -> list[int]:
    pattern = SERVICE_COMMAND_PATTERNS.get(service_name, "")
    return _find_process_ids_by_command_substring(pattern) if pattern else []


def _stop_process(pid: int) -> bool:
    if not _process_is_running(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _detached_status_lines(state: dict[str, object]) -> list[str]:
    if not state:
        return _default_port_status_lines()
    bind_host = str(state.get("bind_host") or "127.0.0.1")
    events_port = int(state.get("events_port") or 8765)
    command_port = int(state.get("command_port") or 8788)
    frontend_port = int(state.get("frontend_port") or 8787)
    token = str(state.get("mobile_token") or os.getenv("SPIRITKIN_MOBILE_TOKEN", ""))
    lines = [f"[status] Frontend: {state.get('frontend_url') or _frontend_url(bind_host, frontend_port)}"]
    services = state.get("services") if isinstance(state.get("services"), list) else []
    for record in services:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "unknown")
        pid = int(record.get("pid") or 0)
        port = _service_probe_port(name, events_port=events_port, command_port=command_port, frontend_port=frontend_port)
        process_state = "running" if _process_is_running(pid) else "stopped"
        external = " external=reused" if record.get("external") else ""
        if port is None:
            lines.append(f"[status] {name} pid={pid} process={process_state}{external}")
            continue
        port_state = "listening" if _service_is_listening(name, bind_host=bind_host, port=port, token=token) else "not-listening"
        lines.append(f"[status] {name}:{port} pid={pid} process={process_state} port={port_state}{external}")
    if not any(isinstance(record, dict) and record.get("name") == "runtime" for record in services):
        runtime_pids = _known_service_pids("runtime")
        runtime_state = "running" if runtime_pids else "stopped"
        pid_label = ",".join(str(pid) for pid in runtime_pids) if runtime_pids else "-"
        lines.append(f"[status] runtime pid={pid_label} process={runtime_state}")
    return lines


def _stop_detached_services(state: dict[str, object], *, state_file: Path = DETACHED_STATE_FILE) -> list[str]:
    if not state:
        return ["[stop] 未找到 detached 启动记录；不会停止未记录的外部进程。"]
    lines = []
    services = state.get("services") if isinstance(state.get("services"), list) else []
    for record in services:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "unknown")
        pid = int(record.get("pid") or 0)
        if record.get("external"):
            lines.append(f"[stop] {name} pid={pid} skipped-reused")
            continue
        stopped = _stop_process(pid)
        lines.append(f"[stop] {name} pid={pid} {'stopped' if stopped else 'not-running'}")
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        lines.append(f"[stop] 状态文件未删除：{exc}")
    return lines


def _browser_host(host: str) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return browser_host


def _public_page_url(public_frontend_url: str, page: str) -> str | None:
    public_frontend_url = public_frontend_url.strip().rstrip("/")
    if not public_frontend_url:
        return None
    if public_frontend_url.endswith(".html"):
        return f"{public_frontend_url.rsplit('/', 1)[0]}/{page}"
    return f"{public_frontend_url}/{page}"


def _frontend_url(host: str, port: int, public_frontend_url: str = "") -> str:
    public_url = _public_page_url(public_frontend_url, "index.html")
    if public_url:
        return public_url
    return f"http://{_browser_host(host)}:{port}/index.html"


def _live2d_url(host: str, port: int, public_frontend_url: str = "") -> str:
    public_url = _public_page_url(public_frontend_url, "live2d.html")
    if public_url:
        return public_url
    return f"http://{_browser_host(host)}:{port}/live2d.html"


def _avatar_url(host: str, port: int, public_frontend_url: str = "") -> str:
    public_url = _public_page_url(public_frontend_url, "spirit_avatar.html")
    if public_url:
        return public_url
    return f"http://{_browser_host(host)}:{port}/spirit_avatar.html"


def _avatar_3d_url(host: str, port: int, public_frontend_url: str = "") -> str:
    public_url = _public_page_url(public_frontend_url, "avatar_3d.html")
    if public_url:
        return public_url
    return f"http://{_browser_host(host)}:{port}/avatar_3d.html"


def _events_ws_url(host: str, port: int, public_events_ws_url: str = "") -> str:
    if public_events_ws_url.strip():
        return public_events_ws_url.strip()
    return f"ws://{_browser_host(host)}:{port}"


def _command_url(host: str, port: int, public_command_url: str = "") -> str:
    if public_command_url.strip():
        return public_command_url.strip()
    return f"http://{_browser_host(host)}:{port}/command"


def _mobile_live2d_url(frontend_live2d_url: str, events_ws_url: str, token: str = "") -> str:
    suffix = f"?ws={quote(events_ws_url, safe='')}&mobile=1&role=spirit&config=models/manifest.json&autoload=1"
    if token:
        suffix += f"&token={quote(token, safe='')}"
    return f"{frontend_live2d_url}{suffix}"


def _mobile_avatar_url(frontend_avatar_url: str, events_ws_url: str, command_url: str = "", token: str = "") -> str:
    suffix = f"?ws={quote(events_ws_url, safe='')}&mobile=1"
    if command_url:
        suffix += f"&cmd={quote(command_url, safe='')}"
    if token:
        suffix += f"&token={quote(token, safe='')}"
    return f"{frontend_avatar_url}{suffix}"


def _mobile_avatar_3d_url(frontend_avatar_3d_url: str, events_ws_url: str, command_url: str = "", config_url: str = "models/spirit3d/manifest.json", token: str = "") -> str:
    suffix = f"?ws={quote(events_ws_url, safe='')}&mobile=1&config={quote(config_url, safe='')}"
    if command_url:
        suffix += f"&cmd={quote(command_url, safe='')}"
    if token:
        suffix += f"&token={quote(token, safe='')}"
    return f"{frontend_avatar_3d_url}{suffix}"


def _frp_public_urls(domain_suffix: str, *, prefix: str = "spiritkin", https: bool = True, token: str = "") -> dict[str, str]:
    suffix = domain_suffix.strip().lstrip(".")
    safe_prefix = prefix.strip().strip(".-") or "spiritkin"
    http_scheme = "https" if https else "http"
    ws_scheme = "wss" if https else "ws"
    frontend_host = f"{safe_prefix}.{suffix}"
    events_host = f"{safe_prefix}-events.{suffix}"
    command_host = f"{safe_prefix}-command.{suffix}"
    events_ws = f"{ws_scheme}://{events_host}"
    return {
        "frontend": f"{http_scheme}://{frontend_host}/index.html",
        "avatar": _mobile_avatar_url(f"{http_scheme}://{frontend_host}/spirit_avatar.html", events_ws, f"{http_scheme}://{command_host}/command", token),
        "avatar_3d": _mobile_avatar_3d_url(f"{http_scheme}://{frontend_host}/avatar_3d.html", events_ws, f"{http_scheme}://{command_host}/command", token=token),
        "live2d": _mobile_live2d_url(f"{http_scheme}://{frontend_host}/live2d.html", events_ws, token),
        "websocket": events_ws,
        "command": f"{http_scheme}://{command_host}/command",
    }


def _detect_tailscale_ip() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        ip = line.strip()
        if ip:
            return ip
    return ""


def _tailscale_urls(tailscale_ip: str, *, frontend_port: int, events_port: int, command_port: int, token: str = "") -> dict[str, str]:
    base_frontend = f"http://{tailscale_ip}:{frontend_port}"
    events_ws = f"ws://{tailscale_ip}:{events_port}"
    return {
        "frontend": f"{base_frontend}/index.html",
        "avatar": _mobile_avatar_url(f"{base_frontend}/spirit_avatar.html", events_ws, f"http://{tailscale_ip}:{command_port}/command", token),
        "avatar_3d": _mobile_avatar_3d_url(f"{base_frontend}/avatar_3d.html", events_ws, f"http://{tailscale_ip}:{command_port}/command", token=token),
        "live2d": _mobile_live2d_url(f"{base_frontend}/live2d.html", events_ws, token),
        "websocket": events_ws,
        "command": f"http://{tailscale_ip}:{command_port}/command",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 SpiritKin runtime 事件桥、主循环和前端面板")
    parser.add_argument("--no-runtime", action="store_true", help="只启动事件桥和前端，不启动语音 runtime")
    parser.add_argument("--no-frontend", action="store_true", help="不自动打开 frontend/index.html")
    parser.add_argument("--no-frontend-server", action="store_true", help="不启动前端 HTTP 静态服务")
    parser.add_argument("--no-command-gateway", action="store_true", help="不启动手机/网页远程指令 HTTP 网关")
    parser.add_argument("--no-fast-voice", action="store_true", help="不注入快速热词/语音 LLM 纠错默认环境变量")
    parser.add_argument("--lan", action="store_true", help="绑定 0.0.0.0，允许同一局域网手机访问面板和命令网关")
    parser.add_argument("--tailscale", action="store_true", help="绑定 0.0.0.0，并打印 Tailscale 移动数据访问地址")
    parser.add_argument("--tailscale-ip", default="", help="手动指定本机 Tailscale IPv4；不填时尝试运行 tailscale ip -4")
    parser.add_argument("--host", default="127.0.0.1", help="非 --lan 模式下的绑定地址")
    parser.add_argument("--events-port", type=int, default=8765, help="事件 WebSocket 端口")
    parser.add_argument("--frontend-port", type=int, default=8787, help="前端 HTTP 端口")
    parser.add_argument("--command-port", type=int, default=8788, help="手机/网页命令网关端口")
    parser.add_argument("--mobile-token", default="", help="手机访问命令网关所需 token；--lan 且未设置时会自动生成")
    parser.add_argument("--public-frontend-url", default="", help="移动数据/公网隧道访问的前端根地址，例如 https://xxx.trycloudflare.com")
    parser.add_argument("--public-events-ws-url", default="", help="移动数据/公网隧道访问的事件 WebSocket 地址，例如 wss://xxx.example.com/events")
    parser.add_argument("--public-command-url", default="", help="移动数据/公网隧道访问的命令 API 地址，例如 https://xxx.example.com/command")
    parser.add_argument("--frp-domain-suffix", default="", help="frp/反代域名后缀，例如 example.com；会推导 spiritkin.example.com 等公网入口")
    parser.add_argument("--frp-prefix", default="spiritkin", help="frp 子域名前缀，默认 spiritkin")
    parser.add_argument("--frp-http", action="store_true", help="frp 公网入口使用 http/ws；默认按 https/wss 打印")
    parser.add_argument("--remote-worker-url", default="", help="中枢要接入的远端 worker 地址，例如 http://100.64.0.8:8790")
    parser.add_argument("--remote-node-id", default="", help="远端节点 ID，例如 office-pc；不填则从 URL 推导")
    parser.add_argument("--remote-worker-token", default="", help="调用远端 worker /execute 使用的 token")
    parser.add_argument("--remote-worker-aliases", default="", help="远端节点别名，逗号分隔，例如 公司电脑,office")
    parser.add_argument("--startup-delay", type=float, default=1.0, help="启动事件桥后等待 runtime 连接的秒数")
    parser.add_argument("--detached", action="store_true", help="后台启动服务后退出，适合 Codex 桌面内置浏览器手动打开本地 URL")
    parser.add_argument("--status", action="store_true", help="查看 --detached 后台服务状态")
    parser.add_argument("--stop", action="store_true", help="停止 --detached 启动的后台服务")
    args = parser.parse_args()

    if args.status:
        for line in _detached_status_lines(_read_detached_state()):
            print(line)
        return 0
    if args.stop:
        for line in _stop_detached_services(_read_detached_state()):
            print(line)
        return 0

    commands = build_startup_commands()
    child_env = _build_child_env(fast_voice=not args.no_fast_voice)
    bind_host = "0.0.0.0" if (args.lan or args.tailscale) else args.host
    tailscale_ip = args.tailscale_ip.strip() or (_detect_tailscale_ip() if args.tailscale else "")
    if args.mobile_token:
        child_env["SPIRITKIN_MOBILE_TOKEN"] = args.mobile_token
    elif not child_env.get("SPIRITKIN_MOBILE_TOKEN"):
        child_env["SPIRITKIN_MOBILE_TOKEN"] = secrets.token_urlsafe(18)
    mobile_token = child_env.get("SPIRITKIN_MOBILE_TOKEN", "")
    frp_urls = _frp_public_urls(args.frp_domain_suffix, prefix=args.frp_prefix, https=not args.frp_http, token=mobile_token) if args.frp_domain_suffix.strip() else {}
    public_frontend_url = args.public_frontend_url or str(frp_urls.get("frontend", ""))
    public_events_ws_url = args.public_events_ws_url or str(frp_urls.get("websocket", ""))
    public_command_url = args.public_command_url or str(frp_urls.get("command", ""))
    child_env["SPIRITKIN_EVENTS_BIND_HOST"] = bind_host
    child_env["SPIRITKIN_EVENTS_PORT"] = str(args.events_port)
    child_env["SPIRITKIN_EVENTS_WS_URL"] = f"ws://127.0.0.1:{args.events_port}"
    child_env["SPIRITKIN_COMMAND_HOST"] = bind_host
    child_env["SPIRITKIN_COMMAND_PORT"] = str(args.command_port)
    _apply_remote_worker_env(
        child_env,
        url=args.remote_worker_url,
        node_id=args.remote_node_id,
        token=args.remote_worker_token,
        aliases=args.remote_worker_aliases,
    )
    public_access = bool(public_frontend_url or public_events_ws_url or public_command_url)
    frontend_command = _build_frontend_server_command(bind_host, args.frontend_port)
    if args.detached:
        service_specs = build_detached_service_specs(
            commands,
            frontend_command=frontend_command,
            no_command_gateway=args.no_command_gateway,
            no_frontend_server=args.no_frontend_server,
            no_runtime=args.no_runtime,
        )
        print("[detached] 正在后台启动 SpiritKin 服务。")
        service_records: list[dict[str, object]] = []
        for service_name, command in service_specs:
            port = _service_probe_port(service_name, events_port=args.events_port, command_port=args.command_port, frontend_port=args.frontend_port)
            existing_pids = _known_service_pids(service_name)
            if port is not None and _service_is_listening(service_name, bind_host=bind_host, port=port, token=mobile_token):
                reused_pid = existing_pids[0] if existing_pids else 0
                service_records.append(
                    {
                        "name": service_name,
                        "pid": reused_pid,
                        "command": command,
                        "external": True,
                        "stdout": "",
                        "stderr": "",
                    }
                )
                print(f"[detached] {service_name}: reused pid={reused_pid or '-'} port={port}")
                continue
            if port is None and existing_pids:
                reused_pid = existing_pids[0]
                service_records.append(
                    {
                        "name": service_name,
                        "pid": reused_pid,
                        "command": command,
                        "external": True,
                        "stdout": "",
                        "stderr": "",
                    }
                )
                print(f"[detached] {service_name}: reused pid={reused_pid}")
                continue
            process = _start_detached_process(service_name, command, child_env=child_env)
            log_out, log_err = _service_log_paths(service_name)
            service_records.append(
                {
                    "name": service_name,
                    "pid": process.pid,
                    "command": command,
                    "stdout": str(log_out.relative_to(ROOT_DIR)),
                    "stderr": str(log_err.relative_to(ROOT_DIR)),
                }
            )
            print(f"[detached] {service_name}: pid={process.pid} log={log_out.relative_to(ROOT_DIR)}")
            time.sleep(0.3)
        time.sleep(max(0.5, args.startup_delay))
        detached_frontend_url = _frontend_url(bind_host, args.frontend_port, public_frontend_url)
        _write_detached_state(
            service_records,
            bind_host=bind_host,
            events_port=args.events_port,
            command_port=args.command_port,
            frontend_port=args.frontend_port,
            frontend_url=detached_frontend_url,
            token=mobile_token,
        )
        for service_name, _ in service_specs:
            port = _service_probe_port(service_name, events_port=args.events_port, command_port=args.command_port, frontend_port=args.frontend_port)
            if port is not None:
                state = "listening" if _service_is_listening(service_name, bind_host=bind_host, port=port, token=mobile_token) else "not-ready"
                print(f"[detached] {service_name}:{port} {state}")
        if not args.no_frontend_server:
            print(f"[frontend] 面板: {detached_frontend_url}")
            print(f"[avatar] P2 角色前台: {_avatar_url(bind_host, args.frontend_port, public_frontend_url)}")
            print(f"[avatar3d] 3D 角色页: {_avatar_3d_url(bind_host, args.frontend_port, public_frontend_url)}")
            print(f"[live2d] 形象页: {_live2d_url(bind_host, args.frontend_port, public_frontend_url)}")
        if args.no_frontend and not args.no_frontend_server:
            print("[detached] Codex Browser 可直接打开上面的本地 URL。")
        elif not args.no_frontend:
            webbrowser.open(_frontend_url(bind_host, args.frontend_port, public_frontend_url))
        return 0
    processes: list[subprocess.Popen] = []
    try:
        if not args.no_fast_voice:
            print("[voice] 已启用快速热词配置与语音 LLM 意图纠错默认值。")
        print(f"[bridge] {_format_command(commands['bridge'])}")
        processes.append(subprocess.Popen(commands["bridge"], cwd=ROOT_DIR, env=child_env))
        time.sleep(max(0.0, args.startup_delay))

        if not args.no_command_gateway:
            print(f"[command-gateway] {_format_command(commands['command_gateway'])}")
            processes.append(subprocess.Popen(commands["command_gateway"], cwd=ROOT_DIR, env=child_env))

        if not args.no_frontend_server:
            print(f"[frontend-server] {_format_command(frontend_command)}")
            processes.append(subprocess.Popen(frontend_command, cwd=ROOT_DIR, env=child_env))
            print(f"[frontend] 面板: {_frontend_url(bind_host, args.frontend_port, public_frontend_url)}")
            print(f"[avatar] P2 角色前台: {_avatar_url(bind_host, args.frontend_port, public_frontend_url)}")
            print(f"[avatar3d] 3D 角色页: {_avatar_3d_url(bind_host, args.frontend_port, public_frontend_url)}")
            print(f"[live2d] 形象页: {_live2d_url(bind_host, args.frontend_port, public_frontend_url)}")

        if not args.no_runtime:
            print(f"[runtime] {_format_command(commands['runtime'])}")
            processes.append(subprocess.Popen(commands["runtime"], cwd=ROOT_DIR, env=child_env))

        if not args.no_frontend:
            frontend_url = _frontend_url(bind_host, args.frontend_port, public_frontend_url) if not args.no_frontend_server else FRONTEND_INDEX.as_uri()
            if args.no_frontend_server:
                print(f"[frontend] 面板: {frontend_url}")
                print(f"[avatar] P2 角色前台: {FRONTEND_AVATAR.as_uri()}")
                print(f"[avatar3d] 3D 角色页: {FRONTEND_AVATAR_3D.as_uri()}")
                print(f"[live2d] 形象页: {FRONTEND_LIVE2D.as_uri()}")
            webbrowser.open(frontend_url)

        if args.lan:
            token_suffix = f"&token={quote(mobile_token, safe='')}" if mobile_token else ""
            print(f"[mobile] 同一 Wi-Fi 手机访问: http://<本机局域网IP>:{args.frontend_port}/index.html")
            print(f"[mobile] Avatar Shell: http://<本机局域网IP>:{args.frontend_port}/spirit_avatar.html?ws=ws://<本机局域网IP>:{args.events_port}&cmd=http://<本机局域网IP>:{args.command_port}/command&mobile=1{token_suffix}")
            print(f"[mobile] 3D Avatar: http://<本机局域网IP>:{args.frontend_port}/avatar_3d.html?ws=ws://<本机局域网IP>:{args.events_port}&cmd=http://<本机局域网IP>:{args.command_port}/command&config=models/spirit3d/manifest.json&mobile=1{token_suffix}")
            print(f"[mobile] Live2D: http://<本机局域网IP>:{args.frontend_port}/live2d.html?ws=ws://<本机局域网IP>:{args.events_port}&mobile=1{token_suffix}")
            print(f"[mobile] WebSocket: ws://<本机局域网IP>:{args.events_port}")
            print(f"[mobile] Command API: http://<本机局域网IP>:{args.command_port}/command")
            if child_env.get("SPIRITKIN_MOBILE_TOKEN"):
                print(f"[mobile] Token: {child_env['SPIRITKIN_MOBILE_TOKEN']}")

        if args.tailscale:
            print("[tailscale] 推荐移动数据路线：PC 和手机都登录同一 Tailnet 后，手机可用下面地址访问。")
            if tailscale_ip:
                urls = _tailscale_urls(tailscale_ip, frontend_port=args.frontend_port, events_port=args.events_port, command_port=args.command_port, token=mobile_token)
                print(f"[tailscale] Frontend: {urls['frontend']}")
                print(f"[tailscale] Avatar: {urls['avatar']}")
                print(f"[tailscale] Avatar3D: {urls['avatar_3d']}")
                print(f"[tailscale] Live2D: {urls['live2d']}")
                print(f"[tailscale] WebSocket: {urls['websocket']}")
                print(f"[tailscale] Command API: {urls['command']}")
                if child_env.get("SPIRITKIN_MOBILE_TOKEN"):
                    print(f"[tailscale] Token: {child_env['SPIRITKIN_MOBILE_TOKEN']}")
            else:
                print("[tailscale] 未检测到 Tailscale IPv4；请先安装/登录 Tailscale，或使用 --tailscale-ip 手动指定。")

        if public_access:
            frontend_public = _frontend_url(bind_host, args.frontend_port, public_frontend_url)
            avatar3d_public = _avatar_3d_url(bind_host, args.frontend_port, public_frontend_url)
            live2d_public = _live2d_url(bind_host, args.frontend_port, public_frontend_url)
            avatar_public = _avatar_url(bind_host, args.frontend_port, public_frontend_url)
            events_public = _events_ws_url(bind_host, args.events_port, public_events_ws_url)
            command_public = _command_url(bind_host, args.command_port, public_command_url)
            print("[public] 移动数据/公网访问需要你先启动 VPN/HTTPS 隧道/反向代理；本脚本只打印外部入口，不自动暴露公网。")
            print(f"[public] Frontend: {frontend_public}")
            print(f"[public] Avatar: {_mobile_avatar_url(avatar_public, events_public, command_public, mobile_token)}")
            print(f"[public] Avatar3D: {_mobile_avatar_3d_url(avatar3d_public, events_public, command_public, token=mobile_token)}")
            print(f"[public] Live2D: {_mobile_live2d_url(live2d_public, events_public, mobile_token)}")
            print(f"[public] WebSocket: {events_public}")
            print(f"[public] Command API: {command_public}")
            print("[public] 安全要求：公网必须 HTTPS/WSS + Token/VPN/访问控制，不要裸露 HTTP 控制口。")
            if child_env.get("SPIRITKIN_MOBILE_TOKEN"):
                print(f"[public] Token: {child_env['SPIRITKIN_MOBILE_TOKEN']}")

        if frp_urls:
            print("[frp] 国内移动数据推荐：用 frp/反代把 frontend/events/command 映射到三个子域名。")
            print(f"[frp] Frontend 域名: {frp_urls['frontend']}")
            print(f"[frp] Avatar URL: {frp_urls['avatar']}")
            print(f"[frp] Avatar3D URL: {frp_urls['avatar_3d']}")
            print(f"[frp] Events 域名: {frp_urls['websocket']}")
            print(f"[frp] Command 域名: {frp_urls['command']}")
            print("[frp] 可用 scripts/generate_frp_config.py 生成 frpc.toml 模板。")

        if child_env.get("SPIRITKIN_REMOTE_WORKER_URL"):
            print(f"[remote-worker] URL: {child_env['SPIRITKIN_REMOTE_WORKER_URL']}")
            print(f"[remote-worker] Node ID: {child_env.get('SPIRITKIN_REMOTE_WORKER_NODE_ID') or '<auto-from-url>'}")
            if child_env.get("SPIRITKIN_REMOTE_WORKER_ALIASES"):
                print(f"[remote-worker] Aliases: {child_env['SPIRITKIN_REMOTE_WORKER_ALIASES']}")
            if child_env.get("SPIRITKIN_REMOTE_WORKER_TOKEN"):
                print("[remote-worker] Token: 已设置，Dashboard 会通过 heartbeat 显示节点状态。")

        print("按 Ctrl+C 结束本启动器；已启动的子进程会一起停止。")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止 SpiritKin realtime 进程...")
    finally:
        for process in processes:
            process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
