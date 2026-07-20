"""Local full-stack smoke: boot the three services and verify the real chain.

Starts realtime_bridge + command_gateway + static_frontend_server as child
processes on free ports with a random token, then asserts:
1. event bridge accepts a WebSocket subscriber;
2. gateway /health responds;
3. frontend serves index.html;
4. gateway auth: missing/wrong token -> 401, correct token -> accepted;
5. a real command ("确认执行" with no pending confirmation) flows
   gateway -> runtime -> agent cluster and returns the deterministic
   no-pending reply with avatar emotion/action, and the same
   assistant.message arrives on the event bridge WebSocket.

Usage: python scripts/smoke_local_stack.py [--startup-timeout 60] [--keep-logs]
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

ROOT_DIR = Path(__file__).resolve().parents[1]
NO_PENDING_REPLY = "当前没有等待确认的操作"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def command_headers(token: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-SpiritKin-Token"] = token
    return headers


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str = "",
    timeout: float = 30.0,
) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib_request.Request(url, data=data, method=method, headers=command_headers(token))
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(body) if body else {}
        except json.JSONDecodeError:
            return int(exc.code), {"raw": body}


def http_status(url: str, *, timeout: float = 10.0) -> int:
    req = urllib_request.Request(url, method="GET")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status)
    except HTTPError as exc:
        return int(exc.code)


def wait_until(probe, *, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if probe():
                return True
        except (OSError, URLError, ConnectionError):
            pass
        time.sleep(interval)
    return False


def summarize(checks: list[dict]) -> dict:
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "passed": len(checks) - len(failed), "failed": failed, "checks": checks}


async def bridge_handshake(ws_url: str, token: str, timeout: float) -> bool:
    import websockets

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            async with websockets.connect(ws_url, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "runtime.auth", "token": token}))
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), timeout=2)
                return True
        except (TimeoutError, OSError, Exception):  # noqa: BLE001 - retry any handshake failure until deadline
            await asyncio.sleep(0.5)
    return False


async def send_and_watch_events(
    ws_url: str,
    command_url: str,
    token: str,
    *,
    timeout: float,
) -> dict:
    """Subscribe to the bridge, POST the command, and collect resulting events."""
    import websockets

    result: dict = {"http": None, "assistant_message_seen": False, "avatar_state_seen": False, "event_types": []}
    async with websockets.connect(ws_url, open_timeout=10) as ws:
        await ws.send(json.dumps({"type": "runtime.auth", "token": token}))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=2)  # drain initial snapshot if any

        loop = asyncio.get_running_loop()
        result["http"] = await loop.run_in_executor(
            None,
            lambda: http_json(
                command_url,
                method="POST",
                payload={"text": "确认执行", "channel": "desktop", "metadata": {"frontend": "smoke_local_stack"}},
                token=token,
                timeout=60,
            ),
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not result["assistant_message_seen"]:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            try:
                event = json.loads(frame)
            except json.JSONDecodeError:
                continue
            events = event if isinstance(event, list) else [event]
            for item in events:
                etype = str(item.get("type", ""))
                result["event_types"].append(etype)
                payload = item.get("payload") or item
                if etype == "avatar.state":
                    result["avatar_state_seen"] = True
                if etype == "assistant.message" and NO_PENDING_REPLY in str(payload.get("text", "")):
                    result["assistant_message_seen"] = True
    return result


def start_service(args_list: list[str], env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - handle owned by caller until teardown
    return subprocess.Popen(
        args_list,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def stop_service(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
    if proc.stdout is not None:
        with contextlib.suppress(OSError):
            proc.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="SpiritKin local full-stack smoke")
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--event-timeout", type=float, default=20.0)
    parser.add_argument("--keep-logs", action="store_true", help="print child service logs even on success")
    args = parser.parse_args()

    events_port = find_free_port()
    command_port = find_free_port()
    frontend_port = find_free_port()
    token = uuid.uuid4().hex
    ws_url = f"ws://127.0.0.1:{events_port}"
    command_url = f"http://127.0.0.1:{command_port}/command"
    health_url = f"http://127.0.0.1:{command_port}/health"
    frontend_url = f"http://127.0.0.1:{frontend_port}/index.html"

    env = {
        **os.environ,
        "SPIRITKIN_EVENTS_HOST": "127.0.0.1",
        "SPIRITKIN_EVENTS_PORT": str(events_port),
        "SPIRITKIN_EVENTS_WS_URL": ws_url,
        "SPIRITKIN_COMMAND_PORT": str(command_port),
        "SPIRITKIN_MOBILE_TOKEN": token,
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN", None)

    log_dir = Path(tempfile.mkdtemp(prefix="spiritkin_smoke_"))
    services = {
        "realtime_bridge": [sys.executable, "-m", "backend.app.realtime_bridge"],
        "command_gateway": [sys.executable, "-m", "backend.app.command_gateway"],
        "static_frontend": [sys.executable, "-m", "backend.app.static_frontend_server", "--port", str(frontend_port)],
    }
    procs: dict[str, subprocess.Popen] = {}
    checks: list[dict] = []

    def record(name: str, ok: bool, detail: object = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))

    try:
        for name, cmd in services.items():
            procs[name] = start_service(cmd, env, log_dir / f"{name}.log")
        print(f"[smoke] services starting (logs: {log_dir}) events={events_port} command={command_port} frontend={frontend_port}")

        record("bridge_ws_handshake", asyncio.run(bridge_handshake(ws_url, token, args.startup_timeout)), ws_url)

        def gateway_healthy() -> bool:
            status, body = http_json(health_url, token=token, timeout=5)
            return status == 200 and body.get("service") == "spiritkin-command-gateway"

        record("gateway_health", wait_until(gateway_healthy, timeout=args.startup_timeout), health_url)
        record("frontend_index", wait_until(lambda: http_status(frontend_url, timeout=5) == 200, timeout=args.startup_timeout), frontend_url)

        status_no_token, _ = http_json(command_url, method="POST", payload={"text": "ping"}, timeout=30)
        record("auth_reject_missing_token", status_no_token == 401, f"status={status_no_token}")
        status_bad_token, _ = http_json(command_url, method="POST", payload={"text": "ping"}, token="wrong-token", timeout=30)
        record("auth_reject_wrong_token", status_bad_token == 401, f"status={status_bad_token}")

        flow = asyncio.run(send_and_watch_events(ws_url, command_url, token, timeout=args.event_timeout))
        http_result = flow["http"] or (0, {})
        status, body = http_result
        reply = body.get("reply") or {}
        record("command_http_ok", status == 200 and body.get("ok") is True, f"status={status} error={body.get('error', '')}")
        record("command_deterministic_reply", NO_PENDING_REPLY in str(reply.get("text", "")), reply.get("text", ""))
        record("reply_has_avatar_fields", bool(reply.get("emotion")) and bool(reply.get("action")), {k: reply.get(k) for k in ("emotion", "action")})
        record("ws_assistant_message", flow["assistant_message_seen"], f"event_types={flow['event_types'][:12]}")
        if flow["avatar_state_seen"]:
            print("[info] avatar.state event observed on bridge")
    finally:
        for proc in procs.values():
            stop_service(proc)

    summary = summarize(checks)
    if not summary["ok"] or args.keep_logs:
        for name in services:
            log_path = log_dir / f"{name}.log"
            if log_path.exists():
                print(f"\n===== {name} log =====\n{log_path.read_text(encoding='utf-8', errors='replace')[-4000:]}")
    print(json.dumps({k: summary[k] for k in ("ok", "passed", "failed")}, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
