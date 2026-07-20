from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.control_plane_worker import post_json


def wait_for_port(host: str, port: int, *, open_state: bool, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                is_open = True
        except OSError:
            is_open = False
        if is_open == open_state:
            return
        time.sleep(0.1)
    expected = "open" if open_state else "closed"
    raise TimeoutError(f"port {host}:{port} did not become {expected}")


def start_receiver(*, host: str, port: int, state_dir: Path, log_path: Path) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    for name in (
        "SPIRITKIN_MANAGEMENT_TOKEN",
        "SPIRITKIN_PRODUCTION_MODE",
        "SPIRITKIN_REQUIRE_PAIRING_TOKEN",
        "SPIRITKIN_REQUIRE_WORKER_TOKEN",
    ):
        env.pop(name, None)
    with log_path.open("ab") as log_file:
        return subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(ROOT_DIR / "scripts" / "mobile_link_receiver.py"),
                "--host",
                host,
                "--port",
                str(port),
                "--state-dir",
                str(state_dir),
            ],
            cwd=str(ROOT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def stop_owned_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("worker did not emit a JSON result")


def run_worker(
    *,
    base_url: str,
    state_dir: Path,
    outbox_dir: Path,
    timeout_seconds: float,
    worker_executable: str = "",
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    entrypoint = (
        [str(Path(worker_executable).resolve())]
        if worker_executable
        else [sys.executable, "-u", str(ROOT_DIR / "scripts" / "control_plane_worker.py")]
    )
    completed = subprocess.run(
        [
            *entrypoint,
            "--server",
            base_url,
            "--workspace-id",
            "local-ecommerce",
            "--worker-id",
            "recovery-smoke-worker",
            "--capability",
            "local.cli",
            "--allow-cli",
            "--once",
            "--state-dir",
            str(state_dir),
            "--outbox-dir",
            str(outbox_dir),
        ],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed, parse_last_json(completed.stdout)


def run_recovery_smoke(
    *,
    host: str = "127.0.0.1",
    port: int = 18791,
    state_dir: str = "",
    timeout_seconds: float = 20.0,
    worker_executable: str = "",
) -> dict[str, Any]:
    temp_dir = tempfile.TemporaryDirectory(prefix="spiritkin-worker-recovery-") if not state_dir else None
    root = Path(state_dir or temp_dir.name).resolve()  # type: ignore[union-attr]
    control_state = root / "control-plane"
    worker_state = root / "worker"
    outbox_dir = worker_state / "outbox"
    log_path = root / "control-plane.log"
    root.mkdir(parents=True, exist_ok=True)
    base_url = f"http://{host}:{port}"
    receiver: subprocess.Popen[bytes] | None = None

    try:
        wait_for_port(host, port, open_state=False, timeout_seconds=1.0)
        receiver = start_receiver(host=host, port=port, state_dir=control_state, log_path=log_path)
        wait_for_port(host, port, open_state=True, timeout_seconds=timeout_seconds)

        preflight = post_json(
            base_url,
            "/worker/heartbeat",
            {
                "worker_id": "recovery-smoke-worker",
                "workspace_id": "local-ecommerce",
                "capabilities": ["local.cli"],
            },
        )
        command = [
            sys.executable,
            "-c",
            f"import os,signal; os.kill({receiver.pid}, signal.SIGTERM); print('control-plane-stopped')",
        ]
        queued = post_json(
            base_url,
            "/management/action",
            {
                "action": "start_workflow_run",
                "workspace_id": "local-ecommerce",
                "template_id": "local.cli.run.v1",
                "inputs": {"command": command, "cwd": "."},
                "requested_by": "worker-recovery-smoke",
            },
        )
        workflow = ((queued.get("result") or {}).get("workflow") or {})
        task = workflow.get("worker_task") if isinstance(workflow, dict) else {}
        task_id = str((task or {}).get("task_id") or "")
        if not task_id:
            raise RuntimeError("control plane did not queue a worker task")

        disconnected_started = time.monotonic()
        disconnected_process, disconnected_result = run_worker(
            base_url=base_url,
            state_dir=worker_state,
            outbox_dir=outbox_dir,
            timeout_seconds=timeout_seconds,
            worker_executable=worker_executable,
        )
        disconnected_ms = int((time.monotonic() - disconnected_started) * 1000)
        wait_for_port(host, port, open_state=False, timeout_seconds=timeout_seconds)
        receiver.wait(timeout=5)
        offline_files = sorted(outbox_dir.glob("*.json"))
        if disconnected_process.returncode != 0 or len(offline_files) != 1:
            raise RuntimeError(
                f"disconnect phase failed: exit={disconnected_process.returncode}, outbox={len(offline_files)}"
            )

        receiver = start_receiver(host=host, port=port, state_dir=control_state, log_path=log_path)
        wait_for_port(host, port, open_state=True, timeout_seconds=timeout_seconds)
        recovery_started = time.monotonic()
        recovery_process, recovery_result = run_worker(
            base_url=base_url,
            state_dir=worker_state,
            outbox_dir=outbox_dir,
            timeout_seconds=timeout_seconds,
            worker_executable=worker_executable,
        )
        recovery_ms = int((time.monotonic() - recovery_started) * 1000)
        remaining = sorted(outbox_dir.glob("*.json"))
        snapshot = post_json(
            base_url,
            "/management/action",
            {"action": "snapshot", "workspace_id": "local-ecommerce"},
        )
        worker_tasks = ((snapshot.get("result") or {}).get("worker_tasks") or {})
        recent_tasks = worker_tasks.get("recent") if isinstance(worker_tasks, dict) else []
        restored_task = next(
            (item for item in (recent_tasks or []) if isinstance(item, dict) and item.get("task_id") == task_id),
            {},
        )
        flushed_before = recovery_result.get("flushed_before") if isinstance(recovery_result, dict) else []
        offline_flush = (
            ((disconnected_result.get("results") or [{}])[0].get("flushed") or [{}])[0]
            if disconnected_result.get("results")
            else {}
        )
        ok = (
            bool(preflight.get("ok"))
            and not bool(offline_flush.get("ok"))
            and recovery_process.returncode == 0
            and bool(flushed_before and flushed_before[0].get("ok"))
            and not remaining
            and restored_task.get("status") == "completed"
        )
        return {
            "ok": ok,
            "base_url": base_url,
            "worker_entrypoint": str(Path(worker_executable).resolve()) if worker_executable else "python",
            "task_id": task_id,
            "preflight_heartbeat": bool(preflight.get("ok")),
            "disconnect": {
                "worker_exit_code": disconnected_process.returncode,
                "elapsed_ms": disconnected_ms,
                "outbox_files": len(offline_files),
                "flush_failed": not bool(offline_flush.get("ok")),
            },
            "recovery": {
                "worker_exit_code": recovery_process.returncode,
                "elapsed_ms": recovery_ms,
                "flushed_before_heartbeat": bool(flushed_before and flushed_before[0].get("ok")),
                "outbox_files": len(remaining),
                "task_status": restored_task.get("status") or "",
            },
            "state_dir": str(root),
            "control_plane_log": str(log_path),
        }
    finally:
        stop_owned_process(receiver)
        if temp_dir is not None:
            temp_dir.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise real Control Plane Worker disconnect/outbox recovery.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18791)
    parser.add_argument("--state-dir", default="", help="Optional persistent evidence directory; default uses a temporary directory.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--worker-executable", default="", help="Optional PyInstaller one-file Worker executable.")
    args = parser.parse_args()
    try:
        report = run_recovery_smoke(
            host=args.host,
            port=args.port,
            state_dir=args.state_dir,
            timeout_seconds=args.timeout,
            worker_executable=args.worker_executable,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
