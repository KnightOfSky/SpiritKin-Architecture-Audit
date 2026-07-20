from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.orchestrator.runtime_host import (
    HOST_TYPES,
    RuntimeCheckpointStore,
    RuntimeHostHeartbeatService,
    RuntimeHostRegistry,
    RuntimeWorkflowHostService,
)
from backend.orchestrator.workflow_store import JsonWorkflowStore


def _host_id(host_type: str, explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-") or "local"
    return f"{host_type}:{hostname}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fenced SpiritKin Workflow Runtime Host.")
    parser.add_argument("--host-type", choices=sorted(HOST_TYPES), default=os.getenv("SPIRITKIN_RUNTIME_HOST_TYPE", "cloud"))
    parser.add_argument("--host-id", default=os.getenv("SPIRITKIN_RUNTIME_HOST_ID", ""))
    parser.add_argument("--workspace", default=os.getenv("SPIRITKIN_RUNTIME_WORKSPACE_ID", "local-ecommerce"))
    parser.add_argument("--state-root", default=os.getenv("SPIRITKIN_RUNTIME_SHARED_ROOT", str(ROOT / "state")))
    parser.add_argument("--priority", type=int, default=int(os.getenv("SPIRITKIN_RUNTIME_HOST_PRIORITY", "40")))
    parser.add_argument("--heartbeat-interval", type=float, default=float(os.getenv("SPIRITKIN_RUNTIME_HEARTBEAT_INTERVAL", "10")))
    parser.add_argument("--heartbeat-ttl", type=float, default=float(os.getenv("SPIRITKIN_RUNTIME_HEARTBEAT_TTL", "45")))
    parser.add_argument("--execution-interval", type=float, default=float(os.getenv("SPIRITKIN_RUNTIME_EXECUTION_INTERVAL", "2")))
    parser.add_argument("--max-runs", type=int, default=int(os.getenv("SPIRITKIN_RUNTIME_MAX_RUNS", "20")))
    parser.add_argument("--max-steps", type=int, default=int(os.getenv("SPIRITKIN_RUNTIME_MAX_STEPS", "10")))
    parser.add_argument("--once", action="store_true")
    return parser


def build_service(args: argparse.Namespace) -> RuntimeWorkflowHostService:
    state_root = Path(args.state_root).expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    workflow_store = JsonWorkflowStore(state_root / "workflows", project_root=ROOT)
    registry = RuntimeHostRegistry(state_root / "runtime" / "hosts.json")
    checkpoints = RuntimeCheckpointStore(
        registry,
        path=state_root / "runtime" / "checkpoints.json",
        workflow_store=workflow_store,
    )
    heartbeat = RuntimeHostHeartbeatService(
        registry,
        checkpoints,
        host_id=_host_id(args.host_type, args.host_id),
        workspace_id=args.workspace,
        host_type=args.host_type,
        capabilities=["workflow.execute", "checkpoint.create", "checkpoint.resume", "worker.dispatch"],
        priority=args.priority,
        heartbeat_interval_seconds=args.heartbeat_interval,
        heartbeat_ttl_seconds=args.heartbeat_ttl,
    )
    return RuntimeWorkflowHostService(
        heartbeat,
        execution_interval_seconds=args.execution_interval,
        max_runs=args.max_runs,
        max_steps_per_run=args.max_steps,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = build_service(args)
    if args.once:
        result = service.execute_once()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return 0 if result.get("ok") else 1

    stopped = threading.Event()

    def stop(_signum=None, _frame=None) -> None:
        stopped.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, stop)
    service.start()
    public = service.snapshot()
    print(
        json.dumps(
            {
                "status": "started",
                "host_id": public["host_id"],
                "workspace_id": public["workspace_id"],
                "state_root": str(Path(args.state_root).expanduser().resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        while not stopped.wait(1.0):
            pass
    finally:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
