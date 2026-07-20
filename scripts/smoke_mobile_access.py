from __future__ import annotations

import argparse
import json
from urllib import request as urllib_request
from urllib.parse import urlsplit, urlunsplit


def _read_url(url: str, *, timeout_seconds: float = 5.0, headers: dict[str, str] | None = None) -> tuple[int, str]:
    req = urllib_request.Request(url, method="GET")
    for key, value in dict(headers or {}).items():
        req.add_header(key, value)
    with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
        return int(resp.status), resp.read().decode("utf-8", errors="replace")


def _command_health_url(command_url: str) -> str:
    parsed = urlsplit(command_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/command"):
        path = f"{path[:-8]}/health"
    elif not path or path == "/":
        path = "/health"
    else:
        path = f"{path}/health"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def run_mobile_access_smoke(
    *,
    frontend_url: str,
    command_url: str = "",
    token: str = "",
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    checks: dict[str, object] = {}
    frontend_status, frontend_body = _read_url(frontend_url, timeout_seconds=timeout_seconds)
    checks["frontend"] = {
        "ok": frontend_status == 200 and ("SpiritKin" in frontend_body or "<!DOCTYPE" in frontend_body[:256]),
        "status": frontend_status,
        "url": frontend_url,
    }

    if command_url:
        health_url = _command_health_url(command_url)
        headers = {"X-SpiritKin-Token": token} if token else {}
        command_status, command_body = _read_url(health_url, timeout_seconds=timeout_seconds, headers=headers)
        checks["command_gateway"] = {
            "ok": command_status == 200 and "spiritkin-command-gateway" in command_body,
            "status": command_status,
            "url": health_url,
        }

    return {"ok": all(bool(item.get("ok")) for item in checks.values() if isinstance(item, dict)), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test SpiritKin mobile-data/Tailscale access URLs")
    parser.add_argument("--frontend-url", required=True, help="Frontend URL printed by --tailscale, e.g. http://100.64.0.8:8787/index.html")
    parser.add_argument("--command-url", default="", help="Command API URL, e.g. http://100.64.0.8:8788/command")
    parser.add_argument("--token", default="", help="Optional X-SpiritKin-Token for command gateway")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    args = parser.parse_args()

    try:
        report = run_mobile_access_smoke(
            frontend_url=args.frontend_url,
            command_url=args.command_url,
            token=args.token,
            timeout_seconds=args.timeout,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())