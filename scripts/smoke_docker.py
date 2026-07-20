from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_compose_file() -> dict:
    compose_path = ROOT / "docker-compose.yml"
    return {
        "check": "docker-compose.yml exists",
        "ok": compose_path.exists(),
        "path": str(compose_path),
    }


def check_dockerfile() -> dict:
    dockerfile = ROOT / "Dockerfile"
    return {
        "check": "Dockerfile exists",
        "ok": dockerfile.exists(),
        "path": str(dockerfile),
    }


def check_compose_config() -> dict:
    try:
        result = subprocess.run(
            ["docker", "compose", "config"],
            cwd=str(ROOT), capture_output=True, timeout=15,
        )
        return {"check": "docker compose config", "ok": result.returncode == 0, "stderr": result.stderr.decode()[:500]}
    except FileNotFoundError:
        return {"check": "docker compose config", "ok": False, "stderr": "docker not found"}
    except subprocess.TimeoutExpired:
        return {"check": "docker compose config", "ok": False, "stderr": "timeout"}


def run_smoke() -> int:
    checks = [check_compose_file(), check_dockerfile(), check_compose_config()]
    report = {"smoke": "docker-deploy-b1", "checks": checks, "all_ok": all(c["ok"] for c in checks)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(run_smoke())
