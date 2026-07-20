from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.service_ports import build_default_service_env, resolve_service_port
from backend.app.settings import resolve_tts_settings

FRONTEND_DIR = ROOT_DIR / "frontend"
STATE_DIR = ROOT_DIR / "state"
RUN_DIR = STATE_DIR / "run"
LOG_DIR = STATE_DIR / "logs"
LAUNCH_STATE_FILE = Path(os.getenv("SPIRITKIN_DESKTOP_LAUNCH_STATE_FILE", str(RUN_DIR / "desktop_console.json")))
EDGE_PROFILE_DIR = RUN_DIR / "desktop_console_edge_profile"
WPF_PROJECT = ROOT_DIR / "desktop" / "SpiritKinDesktop" / "SpiritKinDesktop.csproj"
WPF_BUILD_EXE = (
    ROOT_DIR
    / "desktop"
    / "SpiritKinDesktop"
    / "bin"
    / "Debug"
    / "net8.0-windows10.0.17763.0"
    / "SpiritKinDesktop.exe"
)
WPF_ASSETS_FILE = WPF_PROJECT.parent / "obj" / "project.assets.json"
DESKTOP_STATE_PATH = STATE_DIR / "desktop_console" / "state.json"
OFFICIAL_DESKTOP = "wpf-native"
COMPAT_CONSOLE = "desktop_console.html"
CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"
COSYVOICE_SERVICE_SCRIPT = ROOT_DIR / "backend" / "expression" / "cosyvoice_service.py"
COSYVOICE_PYTHON_DEFAULT = STATE_DIR / "providers" / "miniconda3" / ("python.exe" if os.name == "nt" else "bin/python")
COSYVOICE_MODEL_DIR_DEFAULT = STATE_DIR / "providers" / "cosyvoice-model"
COSYVOICE_SOURCE_DIR_DEFAULT = STATE_DIR / "providers" / "cosyvoice-src"
COSYVOICE_PROFILE_ROOT_DEFAULT = STATE_DIR / "voice-profiles"


@dataclass(frozen=True)
class CosyVoiceServiceConfig:
    selected: bool
    available: bool
    port: int = 50000
    command: tuple[str, ...] = ()
    reason: str = ""


def resolve_session_token(explicit_token: str = "") -> str:
    token = (explicit_token or os.getenv("SPIRITKIN_MOBILE_TOKEN", "")).strip()
    return token or secrets.token_urlsafe(18)


def resolve_launch_token(
    explicit_token: str = "",
    *,
    restart_wpf: bool = False,
    launch_state: dict[str, object] | None = None,
) -> str:
    token = (explicit_token or os.getenv("SPIRITKIN_MOBILE_TOKEN", "")).strip()
    if token:
        return token
    if restart_wpf:
        prior = launch_state if launch_state is not None else read_launch_state()
        prior_token = str(prior.get("session_token") or "").strip()
        if prior_token:
            return prior_token
    return resolve_session_token("")


def _browser_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _project_path(value: str | Path, *, root_dir: Path = ROOT_DIR) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root_dir / path


def _env_enabled(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_cosyvoice_service_config(
    *,
    environ: dict[str, str] | None = None,
    config_path: str | Path = CONFIG_PATH,
    root_dir: Path = ROOT_DIR,
) -> CosyVoiceServiceConfig:
    env = dict(os.environ if environ is None else environ)
    settings = resolve_tts_settings(environ=env, config_path=config_path)
    if not settings.enabled or settings.provider != "cosyvoice":
        return CosyVoiceServiceConfig(selected=False, available=False, reason="provider_not_selected")
    if not _env_enabled(env.get("SPIRITKIN_AUTOSTART_COSYVOICE"), default=True):
        return CosyVoiceServiceConfig(selected=True, available=False, reason="autostart_disabled")

    try:
        endpoint = urlparse(settings.base_url)
        port = int(endpoint.port or 0)
    except (TypeError, ValueError):
        return CosyVoiceServiceConfig(selected=True, available=False, reason="invalid_base_url")
    if endpoint.scheme != "http" or (endpoint.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"} or port <= 0:
        return CosyVoiceServiceConfig(selected=True, available=False, port=port or 50000, reason="loopback_http_required")

    python_path = _project_path(env.get("SPIRITKIN_COSYVOICE_PYTHON", COSYVOICE_PYTHON_DEFAULT), root_dir=root_dir)
    model_dir = _project_path(env.get("SPIRITKIN_COSYVOICE_MODEL_DIR", COSYVOICE_MODEL_DIR_DEFAULT), root_dir=root_dir)
    source_dir = _project_path(env.get("SPIRITKIN_COSYVOICE_SOURCE_DIR", COSYVOICE_SOURCE_DIR_DEFAULT), root_dir=root_dir)
    profile_root = _project_path(env.get("SPIRITKIN_VOICE_PROFILE_ROOT", COSYVOICE_PROFILE_ROOT_DEFAULT), root_dir=root_dir)
    service_script = _project_path(env.get("SPIRITKIN_COSYVOICE_SERVICE_SCRIPT", COSYVOICE_SERVICE_SCRIPT), root_dir=root_dir)
    profile_path = _project_path(settings.voice_profile_path, root_dir=root_dir) if settings.voice_profile_path else Path()

    try:
        profile_path.resolve().relative_to(profile_root.resolve())
    except (OSError, ValueError):
        return CosyVoiceServiceConfig(selected=True, available=False, port=port, reason="voice_profile_outside_root")

    required_paths = {
        "python": python_path,
        "service": service_script,
        "model": model_dir / "cosyvoice3.yaml",
        "source": source_dir / "cosyvoice" / "cli" / "cosyvoice.py",
        "matcha": source_dir / "third_party" / "Matcha-TTS",
        "profile": profile_path,
    }
    missing = [name for name, path in required_paths.items() if not path.exists()]
    if missing:
        return CosyVoiceServiceConfig(
            selected=True,
            available=False,
            port=port,
            reason=f"missing_runtime:{','.join(missing)}",
        )

    command = [
        str(python_path),
        "-u",
        str(service_script),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--model-dir",
        str(model_dir),
        "--profile-root",
        str(profile_root),
        "--cosyvoice-root",
        str(source_dir),
    ]
    if _env_enabled(env.get("SPIRITKIN_COSYVOICE_FP16"), default=True):
        command.append("--fp16")
    return CosyVoiceServiceConfig(selected=True, available=True, port=port, command=tuple(command))


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


def _http_endpoint_ok(host: str, port: int, path: str, timeout: float = 0.8) -> bool:
    try:
        with urlopen(f"http://{_browser_host(host)}:{port}{path}", timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def _cosyvoice_healthy(port: int, timeout: float = 1.5) -> bool:
    request = Request(f"http://127.0.0.1:{int(port)}/health", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("ok") is True and payload.get("provider") == "cosyvoice"


def _command_gateway_healthy(host: str, port: int, *, token: str = "") -> bool:
    headers = {}
    if token:
        headers = {
            "X-SpiritKin-Token": token,
            "Authorization": f"Bearer {token}",
        }
    request = Request(f"http://{_browser_host(host)}:{port}/desktop/workflows", headers=headers)
    try:
        with urlopen(request, timeout=0.8) as response:
            if not 200 <= int(response.status) < 300:
                return False
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return False
    workflows = payload.get("workflows") if isinstance(payload, dict) else {}
    if not isinstance(workflows, dict):
        return False
    runs = workflows.get("runs")
    first_run = runs[0] if isinstance(runs, list) and runs and isinstance(runs[0], dict) else {}
    return (
        "available_agents" in workflows
        and "agent_skill_map" in workflows
        and (not first_run or "progress" in first_run)
    )


def _desktop_url(host: str, frontend_port: int, *, events_port: int, command_port: int, token: str = "") -> str:
    base = f"http://{_browser_host(host)}:{frontend_port}/desktop_console.html"
    query = {
        "ws": f"ws://{_browser_host(host)}:{events_port}",
        "cmd": f"http://{_browser_host(host)}:{command_port}/command",
    }
    if token:
        query["token"] = token
    query_text = "&".join(f"{key}={quote(value, safe='')}" for key, value in query.items())
    return f"{base}?{query_text}"


def _avatar_url(host: str, frontend_port: int, *, events_port: int, command_port: int, token: str = "") -> str:
    command_url = f"http://{_browser_host(host)}:{command_port}/command"
    suffix = (
        f"?config={quote('models/spirit3d/manifest.json', safe='')}"
        f"&ws={quote(f'ws://{_browser_host(host)}:{events_port}', safe='')}"
        f"&cmd={quote(command_url, safe='')}"
        "&v=desktop-console"
    )
    if token:
        suffix += f"&token={quote(token, safe='')}"
    return f"http://{_browser_host(host)}:{frontend_port}/avatar_3d.html{suffix}"


def build_service_commands(
    *,
    host: str,
    frontend_port: int,
    events_port: int,
    command_port: int,
    cosyvoice: CosyVoiceServiceConfig | None = None,
) -> dict[str, list[str]]:
    command_gateway_script = "from backend.app.codex_work_events import install; install(); from backend.app.command_gateway import main; main()"
    commands = {
        "bridge": [sys.executable, "-m", "backend.app.realtime_bridge"],
        "command_gateway": [sys.executable, "-u", "-c", command_gateway_script],
        "frontend": [
            sys.executable,
            "-m",
            "backend.app.static_frontend_server",
            "--port",
            str(frontend_port),
            "--host",
            host,
            "--directory",
            str(FRONTEND_DIR),
        ],
    }
    if cosyvoice is not None and cosyvoice.available:
        commands["cosyvoice"] = list(cosyvoice.command)
    commands["voice_session"] = [
        sys.executable,
        "-u",
        "-m",
        "backend.perception.audio.realtime_session",
        "--strict-hotword",
        "--max-turns",
        "0",
        "--idle-timeouts",
        "0",
    ]
    return commands


def _service_port(
    service_name: str,
    *,
    events_port: int,
    command_port: int,
    frontend_port: int,
    cosyvoice_port: int = 50000,
) -> int:
    return {
        "bridge": events_port,
        "command_gateway": command_port,
        "frontend": frontend_port,
        "cosyvoice": cosyvoice_port,
        "voice_session": 0,
    }[service_name]


def _service_process_match(service_name: str) -> str:
    return {
        "cosyvoice": "backend/expression/cosyvoice_service.py",
        "voice_session": "backend.perception.audio.realtime_session",
    }.get(service_name, "")


def _service_log_paths(service_name: str) -> tuple[Path, Path]:
    return LOG_DIR / f"desktop_console_{service_name}.out.log", LOG_DIR / f"desktop_console_{service_name}.err.log"


def _hidden_process_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _windowed_process_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _hidden_run_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _start_hidden_process(service_name: str, command: list[str], *, env: dict[str, str]) -> subprocess.Popen:
    out_log, err_log = _service_log_paths(service_name)
    out_log.parent.mkdir(parents=True, exist_ok=True)
    stdout = out_log.open("ab", buffering=0)
    stderr = err_log.open("ab", buffering=0)
    try:
        return subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            env=env,
            stdout=stdout,
            stderr=stderr,
            **_hidden_process_kwargs(),
        )
    finally:
        stdout.close()
        stderr.close()


def ensure_services(
    *,
    host: str,
    frontend_port: int,
    events_port: int,
    command_port: int,
    token: str = "",
    startup_timeout: float = 8.0,
    autostart_voice: bool | None = None,
) -> list[dict[str, object]]:
    env = dict(os.environ)
    env.update(build_default_service_env(host=host, token=token))
    env["SPIRITKIN_EVENTS_PORT"] = str(events_port)
    env["SPIRITKIN_EVENTS_WS_URL"] = f"ws://{_browser_host(host)}:{events_port}"
    env["SPIRITKIN_COMMAND_PORT"] = str(command_port)
    env["SPIRITKIN_FRONTEND_PORT"] = str(frontend_port)
    env.setdefault("SPIRITKIN_ASR_MIN_RMS", "700")
    env.setdefault("SPIRITKIN_ASR_NO_SPEECH_THRESHOLD", "0.96")
    env.setdefault("SPIRITKIN_ASR_LOW_LOGPROB_THRESHOLD", "-2.0")
    if token:
        env["SPIRITKIN_MOBILE_TOKEN"] = token

    if autostart_voice is None:
        autostart_voice = os.getenv("SPIRITKIN_AUTOSTART_VOICE", "").strip() == "1"

    cosyvoice = resolve_cosyvoice_service_config(environ=env)
    records: list[dict[str, object]] = []
    commands = build_service_commands(
        host=host,
        frontend_port=frontend_port,
        events_port=events_port,
        command_port=command_port,
        cosyvoice=cosyvoice,
    )
    if cosyvoice.selected and not cosyvoice.available:
        records.append(
            {
                "name": "cosyvoice",
                "port": cosyvoice.port,
                "status": "skipped" if cosyvoice.reason == "autostart_disabled" else "unavailable",
                "pid": 0,
                "command": [],
                "detail": cosyvoice.reason,
            }
        )
    service_names = [name for name in commands if autostart_voice or name != "voice_session"]
    if not autostart_voice and "voice_session" in commands:
        records.append({"name": "voice_session", "port": 0, "status": "skipped", "pid": 0, "command": commands["voice_session"]})

    for service_name in service_names:
        command = commands[service_name]
        port = _service_port(
            service_name,
            events_port=events_port,
            command_port=command_port,
            frontend_port=frontend_port,
            cosyvoice_port=cosyvoice.port,
        )
        process_match = _service_process_match(service_name)
        if service_name == "cosyvoice" and port > 0:
            if _cosyvoice_healthy(port):
                records.append({"name": service_name, "port": port, "status": "reused", "pid": 0, "command": command})
                continue
            stale_pid = _pid_for_listening_port(port)
            if _port_accepts_connection("127.0.0.1", port):
                records.append({"name": service_name, "port": port, "status": "port_busy", "pid": stale_pid or 0, "command": command})
                continue
        if service_name == "bridge" and port > 0:
            if _websocket_bridge_healthy(host, port, token=token):
                records.append({"name": service_name, "port": port, "status": "reused", "pid": 0, "command": command})
                continue
            stale_pid = _pid_for_listening_port(port)
            if _port_accepts_connection(host, port):
                records.append({"name": service_name, "port": port, "status": "port_busy", "pid": stale_pid or 0, "command": command})
                continue
        if service_name == "command_gateway" and port > 0:
            if _command_gateway_healthy(host, port, token=token):
                records.append({"name": service_name, "port": port, "status": "reused", "pid": 0, "command": command})
                continue
            stale_pid = _pid_for_listening_port(port)
            if stale_pid is not None:
                _terminate_pid(stale_pid)
                time.sleep(0.4)
            if _port_accepts_connection(host, port):
                records.append({"name": service_name, "port": port, "status": "port_busy", "pid": stale_pid or 0, "command": command})
                continue
        if service_name == "frontend" and port > 0:
            stale_pid = _pid_for_listening_port(port)
            if stale_pid is not None:
                _terminate_pid(stale_pid)
                time.sleep(0.4)
            if _port_accepts_connection(host, port):
                records.append({"name": service_name, "port": port, "status": "port_busy", "pid": stale_pid or 0, "command": command})
                continue
        if port > 0 and _port_accepts_connection(host, port):
            records.append({"name": service_name, "port": port, "status": "reused", "pid": 0, "command": command})
            continue
        if process_match and _pid_for_process_match(process_match) is not None:
            records.append({"name": service_name, "port": port, "status": "reused", "pid": 0, "command": command})
            continue
        process = _start_hidden_process(service_name, command, env=env)
        records.append({"name": service_name, "port": port, "status": "started", "pid": process.pid, "command": command})

    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if all(
            _service_started(
                name,
                host=host,
                events_port=events_port,
                command_port=command_port,
                frontend_port=frontend_port,
                cosyvoice_port=cosyvoice.port,
                token=token,
            )
            for name in service_names
        ):
            break
        time.sleep(0.25)
    return records


def _terminate_pid(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
                **_hidden_run_kwargs(),
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(int(pid), 15)
        return True
    except OSError:
        return False


def _service_started(
    service_name: str,
    *,
    host: str,
    events_port: int,
    command_port: int,
    frontend_port: int,
    cosyvoice_port: int = 50000,
    token: str = "",
) -> bool:
    port = _service_port(
        service_name,
        events_port=events_port,
        command_port=command_port,
        frontend_port=frontend_port,
        cosyvoice_port=cosyvoice_port,
    )
    if service_name == "cosyvoice":
        return _cosyvoice_healthy(port)
    if service_name == "bridge":
        return _websocket_bridge_healthy(host, port, token=token or os.getenv("SPIRITKIN_MOBILE_TOKEN", ""))
    if service_name == "command_gateway":
        return _command_gateway_healthy(host, port, token=token or os.getenv("SPIRITKIN_MOBILE_TOKEN", ""))
    if port > 0:
        return _port_accepts_connection(host, port)
    process_match = _service_process_match(service_name)
    return bool(process_match and _pid_for_process_match(process_match) is not None)


def _pid_for_listening_port(port: int) -> int | None:
    if int(port) <= 0 or os.name != "nt":
        return None
    script = (
        "$port = " + json.dumps(int(port)) + "\n"
        "Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty OwningProcess\n"
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
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None:
        for line in (result.stdout or "").splitlines():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid > 0:
                return pid

    # Some Windows PowerShell environments expose the socket through netstat
    # but return no objects from Get-NetTCPConnection. Keep service recovery
    # truthful instead of treating a stale listener as an unknown port owner.
    try:
        fallback = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            **_hidden_run_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    suffix = f":{int(port)}"
    for line in (fallback.stdout or "").splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        local_address, state, owner = fields[1], fields[3].upper(), fields[4]
        if state == "LISTENING" and local_address.endswith(suffix):
            try:
                pid = int(owner)
            except ValueError:
                continue
            return pid if pid > 0 else None
    return None


def _pid_for_process_match(process_match: str) -> int | None:
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
        except (OSError, subprocess.SubprocessError):
            return None
        for line in (result.stdout or "").splitlines():
            try:
                return int(line.strip())
            except ValueError:
                continue
        return None
    try:
        result = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
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


def _edge_candidates() -> list[Path]:
    candidates = []
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.getenv(env_name)
        if not root:
            continue
        candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
    )
    seen: set[str] = set()
    ordered = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            ordered.append(candidate)
    return ordered


def _find_edge() -> Path | None:
    for candidate in _edge_candidates():
        if candidate.exists():
            return candidate
    return None


def build_edge_app_command(url: str, *, profile_dir: Path = EDGE_PROFILE_DIR) -> list[str]:
    edge = _find_edge()
    if edge is None:
        return []
    profile_dir.mkdir(parents=True, exist_ok=True)
    return [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--disable-features=Translate",
    ]


def build_desktop_env(*, host: str = "127.0.0.1", events_port: int = 8765, command_port: int = 8788, frontend_port: int = 8787, token: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env["SPIRITKIN_WORKSPACE_ROOT"] = str(ROOT_DIR)
    env["SPIRITKIN_DESKTOP_STATE_PATH"] = str(DESKTOP_STATE_PATH)
    env.update(build_default_service_env(host=host, token=token))
    env["SPIRITKIN_EVENTS_PORT"] = str(events_port)
    env["SPIRITKIN_EVENTS_WS_URL"] = f"ws://{_browser_host(host)}:{events_port}"
    env["SPIRITKIN_COMMAND_PORT"] = str(command_port)
    env["SPIRITKIN_FRONTEND_PORT"] = str(frontend_port)
    return env


def _wpf_sources_newer_than_build() -> bool:
    try:
        exe_mtime = float(WPF_BUILD_EXE.stat().st_mtime)
        project_dir = WPF_PROJECT.parent
        source_files = [
            path
            for path in project_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".cs", ".xaml", ".csproj"}
            and "bin" not in path.parts
            and "obj" not in path.parts
        ]
        if not source_files:
            return False
        return max(float(path.stat().st_mtime) for path in source_files) > exe_mtime + 0.5
    except Exception:
        return False


def build_wpf_command() -> list[str]:
    if WPF_BUILD_EXE.exists() and not _wpf_sources_newer_than_build():
        return [str(WPF_BUILD_EXE)]
    command = ["dotnet", "run", "--project", str(WPF_PROJECT)]
    if WPF_ASSETS_FILE.exists():
        command.append("--no-restore")
    return command


def _wpf_process_running() -> bool:
    return _pid_for_process_match("SpiritKinDesktop.exe") is not None


def open_console_window(url: str, *, mode: str = "auto", host: str = "127.0.0.1", events_port: int = 8765, command_port: int = 8788, frontend_port: int = 8787, token: str = "", restart_wpf: bool = False) -> str:
    env = build_desktop_env(host=host, events_port=events_port, command_port=command_port, frontend_port=frontend_port, token=token)
    if mode in {"auto", "wpf"} and WPF_PROJECT.exists():
        auto_restarted = False
        if _wpf_process_running():
            should_restart = restart_wpf or _wpf_sources_newer_than_build()
            if not should_restart:
                return "wpf"
            auto_restarted = not restart_wpf
            running_pid = _pid_for_process_match("SpiritKinDesktop.exe")
            if running_pid is not None:
                _terminate_pid(running_pid)
                time.sleep(0.6)
        subprocess.Popen(build_wpf_command(), cwd=ROOT_DIR, env=env, **_windowed_process_kwargs())
        return "wpf-restarted" if restart_wpf else ("wpf-updated" if auto_restarted else "wpf")

    if mode in {"auto", "webview"}:
        try:
            import webview  # type: ignore
        except Exception:
            if mode == "webview":
                raise RuntimeError("pywebview is not installed") from None
        else:
            webview.create_window("SpiritKin Desktop Console", url, width=1440, height=920, min_size=(1100, 720))
            webview.start()
            return "webview"

    if mode in {"auto", "edge"}:
        command = build_edge_app_command(url)
        if command:
            subprocess.Popen(command, cwd=ROOT_DIR, **_windowed_process_kwargs())
            return "edge-app"
        if mode == "edge":
            raise RuntimeError("Microsoft Edge was not found")

    webbrowser.open(url)
    return "browser"


def write_launch_state(
    records: list[dict[str, object]],
    *,
    url: str,
    avatar_url: str,
    mode: str,
    host: str,
    frontend_port: int,
    events_port: int,
    command_port: int,
    token: str = "",
) -> None:
    LAUNCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_STATE_FILE.write_text(
        json.dumps(
            {
                "created_at": time.time(),
                "official_desktop": OFFICIAL_DESKTOP,
                "compat_console": COMPAT_CONSOLE,
                "workspace_root": str(ROOT_DIR),
                "wpf_project": str(WPF_PROJECT),
                "desktop_state_path": str(DESKTOP_STATE_PATH),
                "url": url,
                "avatar_url": avatar_url,
                "open_mode": mode,
                "host": host,
                "frontend_port": frontend_port,
                "events_port": events_port,
                "command_port": command_port,
                "session_token": token,
                "services": records,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def read_launch_state() -> dict[str, object]:
    try:
        data = json.loads(LAUNCH_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def stop_launch_services(state: dict[str, object], *, state_file: Path = LAUNCH_STATE_FILE) -> list[str]:
    if not state:
        return ["[desktop] no launch state found; no recorded services to stop"]
    lines: list[str] = []
    services = state.get("services") if isinstance(state.get("services"), list) else []
    for record in services:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "unknown")
        status = str(record.get("status") or "")
        pid = int(record.get("pid") or 0)
        if status != "started" or pid <= 0:
            lines.append(f"[desktop] {name} pid={pid} skipped status={status or 'unknown'}")
            continue
        stopped = _terminate_pid(pid)
        lines.append(f"[desktop] {name} pid={pid} {'stopped' if stopped else 'not-running'}")
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        lines.append(f"[desktop] launch state not removed: {exc}")
    if not lines:
        lines.append("[desktop] no service records found")
    return lines


def status_lines(state: dict[str, object]) -> list[str]:
    if not state:
        return ["[desktop] no launch state found"]
    host = str(state.get("host") or "127.0.0.1")
    token = str(state.get("session_token") or os.getenv("SPIRITKIN_MOBILE_TOKEN", ""))
    official = str(state.get("official_desktop") or OFFICIAL_DESKTOP)
    compat = str(state.get("compat_console") or COMPAT_CONSOLE)
    lines = [
        f"[desktop] official_desktop={official}",
        f"[desktop] compat_console={compat}",
        f"[desktop] workspace_root={state.get('workspace_root') or ROOT_DIR}",
        f"[desktop] wpf_project={state.get('wpf_project') or WPF_PROJECT}",
        f"[desktop] desktop_state_path={state.get('desktop_state_path') or DESKTOP_STATE_PATH}",
        f"[desktop] url={state.get('url')}",
        f"[desktop] open_mode={state.get('open_mode')}",
    ]
    for key in ("frontend_port", "events_port", "command_port"):
        port = int(state.get(key) or 0)
        if port:
            if key == "events_port":
                lines.append(f"[desktop] {key}={port} bridge={_websocket_bridge_healthy(host, port, token=token)}")
            else:
                lines.append(f"[desktop] {key}={port} listening={_port_accepts_connection(host, port)}")
    voice_pid = _pid_for_process_match(_service_process_match("voice_session"))
    lines.append(f"[desktop] voice_session running={voice_pid is not None} pid={voice_pid or 0}")
    cosyvoice = resolve_cosyvoice_service_config()
    if cosyvoice.selected:
        cosyvoice_pid = _pid_for_listening_port(cosyvoice.port)
        detail = f" reason={cosyvoice.reason}" if cosyvoice.reason else ""
        lines.append(
            f"[desktop] cosyvoice configured={cosyvoice.available} ready={_cosyvoice_healthy(cosyvoice.port)} "
            f"port={cosyvoice.port} pid={cosyvoice_pid or 0}{detail}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 SpiritKin 桌面端控制台")
    parser.add_argument("--host", default="127.0.0.1", help="服务绑定地址；多端同步可用 --lan 绑定 0.0.0.0")
    parser.add_argument("--lan", action="store_true", help="绑定 0.0.0.0，允许局域网设备访问同步入口")
    parser.add_argument("--frontend-port", type=int, default=resolve_service_port("frontend", 8787))
    parser.add_argument("--events-port", type=int, default=resolve_service_port("event_bridge", 8765))
    parser.add_argument("--command-port", type=int, default=resolve_service_port("command_gateway", 8788))
    parser.add_argument("--token", default=os.getenv("SPIRITKIN_MOBILE_TOKEN", ""), help="命令网关 token；未提供时自动生成本次会话 token")
    parser.add_argument("--open-mode", choices=["auto", "wpf", "webview", "edge", "browser"], default="auto")
    parser.add_argument("--no-open", action="store_true", help="只启动服务并打印桌面控制台 URL")
    parser.add_argument("--restart-wpf", action="store_true", help="如果 WPF 桌面端已在运行，先关闭再启动，以加载最新 UI 代码")
    parser.add_argument("--autostart-voice", action="store_true", help="同时启动麦克风语音监听；默认不启动，避免环境声误触发")
    parser.add_argument("--status", action="store_true", help="查看上次桌面端启动状态")
    parser.add_argument("--stop", action="store_true", help="停止上次由此脚本启动并记录的后台服务")
    args = parser.parse_args()

    if args.status:
        for line in status_lines(read_launch_state()):
            print(line)
        return 0
    if args.stop:
        for line in stop_launch_services(read_launch_state()):
            print(line)
        return 0

    bind_host = "0.0.0.0" if args.lan else args.host
    session_token = resolve_launch_token(args.token, restart_wpf=args.restart_wpf)
    url = _desktop_url(bind_host, args.frontend_port, events_port=args.events_port, command_port=args.command_port, token=session_token)
    avatar_url = _avatar_url(bind_host, args.frontend_port, events_port=args.events_port, command_port=args.command_port, token=session_token)
    records = ensure_services(
        host=bind_host,
        frontend_port=args.frontend_port,
        events_port=args.events_port,
        command_port=args.command_port,
        token=session_token,
        autostart_voice=args.autostart_voice,
    )
    mode = "not-opened" if args.no_open else open_console_window(
        url,
        mode=args.open_mode,
        host=bind_host,
        events_port=args.events_port,
        command_port=args.command_port,
        frontend_port=args.frontend_port,
        token=session_token,
        restart_wpf=args.restart_wpf,
    )
    write_launch_state(
        records,
        url=url,
        avatar_url=avatar_url,
        mode=mode,
        host=bind_host,
        frontend_port=args.frontend_port,
        events_port=args.events_port,
        command_port=args.command_port,
        token=session_token,
    )
    print(f"[desktop] url={url}")
    print(f"[desktop] avatar={avatar_url}")
    print(f"[desktop] official_desktop={OFFICIAL_DESKTOP}")
    print(f"[desktop] compat_console={COMPAT_CONSOLE}")
    print(f"[desktop] workspace_root={ROOT_DIR}")
    print(f"[desktop] wpf_project={WPF_PROJECT}")
    print(f"[desktop] desktop_state_path={DESKTOP_STATE_PATH}")
    print(f"[desktop] open_mode={mode}")
    print("[desktop] command token: configured")
    for record in records:
        suffix = f":{record['port']}" if int(record["port"]) > 0 else ""
        detail = f" detail={record['detail']}" if record.get("detail") else ""
        print(f"[desktop] {record['name']}{suffix} {record['status']} pid={record['pid']}{detail}")
    if args.lan:
        print(f"[desktop] LAN: http://<本机局域网IP>:{args.frontend_port}/desktop_console.html")
        print("[desktop] LAN token: configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
