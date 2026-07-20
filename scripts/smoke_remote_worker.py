from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.executors import HttpRemoteNodeClient, RemoteExecutionPayload
from backend.executors.remote_protocol import remote_execution_response_to_dict, remote_node_heartbeat_to_dict


def run_remote_worker_smoke(
    *,
    base_url: str,
    node_id: str = "",
    auth_token: str = "",
    target: str = "local_pc",
    operation: str = "list_installed_apps",
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    client = HttpRemoteNodeClient(base_url, auth_token=auth_token, timeout_seconds=timeout_seconds)
    heartbeat = client.heartbeat(node_id)
    resolved_node_id = node_id or heartbeat.node_id
    response = client.execute(RemoteExecutionPayload(node_id=resolved_node_id, target=target, operation=operation))
    return {
        "ok": response.success,
        "base_url": base_url.rstrip("/"),
        "node_id": resolved_node_id,
        "target": target,
        "operation": operation,
        "heartbeat": remote_node_heartbeat_to_dict(heartbeat),
        "execution": remote_execution_response_to_dict(response),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a SpiritKin remote worker over HTTP")
    parser.add_argument("--url", required=True, help="Remote worker base URL, e.g. http://100.64.0.8:8790")
    parser.add_argument("--node-id", default="", help="Expected remote node id; optional if worker heartbeat returns one")
    parser.add_argument("--token", default="", help="Remote worker token for /execute")
    parser.add_argument("--target", default="local_pc", help="Execution target to test, default: local_pc")
    parser.add_argument("--operation", default="list_installed_apps", help="Read-only operation to test, default: list_installed_apps")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    args = parser.parse_args()

    try:
        report = run_remote_worker_smoke(
            base_url=args.url,
            node_id=args.node_id,
            auth_token=args.token,
            target=args.target,
            operation=args.operation,
            timeout_seconds=args.timeout,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
