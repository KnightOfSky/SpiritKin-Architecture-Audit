from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.executors.base import ExecutionRequest
from backend.executors.python_worker_executor import PythonWorkerExecutor
from backend.orchestrator.agent_cluster import AgentCluster


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run_execution_repair_smoke(
    *,
    state_dir: str = "",
    package: str = "colorama==0.4.6",
    module: str = "colorama",
) -> dict[str, object]:
    temporary = tempfile.TemporaryDirectory(prefix="spiritkin-execution-repair-") if not state_dir else None
    base = Path(state_dir or temporary.name).resolve()  # type: ignore[union-attr]
    run_root = base / f"run-{int(time.time())}-{uuid4().hex[:8]}"
    run_root.mkdir(parents=True, exist_ok=False)
    venv_root = run_root / "venv"
    started = time.monotonic()
    venv.EnvBuilder(with_pip=True, clear=False).create(venv_root)
    python_executable = _venv_python(venv_root)
    if not python_executable.exists():
        raise RuntimeError(f"venv Python is missing: {python_executable}")

    before = subprocess.run(
        [str(python_executable), "-c", f"import importlib.util; print(bool(importlib.util.find_spec({module!r})))"],
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    dependency_present_before = before.stdout.strip().lower() == "true"
    if dependency_present_before:
        raise RuntimeError(f"fresh venv unexpectedly contains module: {module}")

    script_path = run_root / "missing_dependency_demo.py"
    script_path.write_text(
        f"import {module}\nprint(getattr({module}, '__version__', 'installed'))\n",
        encoding="utf-8",
    )
    repair_response = json.dumps(
        {
            "action": "retry",
            "params": {"script_path": script_path.name},
            "repair_tool": {
                "name": "python.install_package",
                "arguments": {"package": package, "timeout_seconds": 120},
            },
            "reason": "install the missing Python dependency and retry",
        }
    )
    old_authz_path = os.environ.get("SPIRITKIN_TOOL_AUTHZ_PATH")
    old_log_path = os.environ.get("SPIRITKIN_SELF_HEAL_LOG")
    os.environ["SPIRITKIN_TOOL_AUTHZ_PATH"] = str(run_root / "tool-authz.json")
    os.environ["SPIRITKIN_SELF_HEAL_LOG"] = str(run_root / "self-heal.jsonl")
    try:
        executor = PythonWorkerExecutor(
            workspace_root=run_root,
            python_executable=str(python_executable),
            default_timeout_seconds=120,
            max_timeout_seconds=180,
        )
        cluster = AgentCluster(
            llm_client=lambda _prompt, **_kwargs: repair_response,
            executors=[executor],
        )
        cluster._active_input_metadata = {
            "permission_mode": "full_access",
            "full_access_granted": True,
            "actor": "execution-repair-smoke",
        }
        reply = cluster._handle_execution(
            ExecutionRequest("python", "python.run", {"script_path": script_path.name}),
            user_input="run the isolated missing-dependency smoke",
            skip_confirmation=True,
        )
    finally:
        if old_authz_path is None:
            os.environ.pop("SPIRITKIN_TOOL_AUTHZ_PATH", None)
        else:
            os.environ["SPIRITKIN_TOOL_AUTHZ_PATH"] = old_authz_path
        if old_log_path is None:
            os.environ.pop("SPIRITKIN_SELF_HEAL_LOG", None)
        else:
            os.environ["SPIRITKIN_SELF_HEAL_LOG"] = old_log_path

    execution = reply.metadata.get("execution") if isinstance(reply.metadata, dict) else {}
    repair_execution = reply.metadata.get("repair_execution") if isinstance(reply.metadata, dict) else {}
    retry_trace = reply.metadata.get("retry_trace") if isinstance(reply.metadata, dict) else []
    stdout = str(((execution or {}).get("data") or {}).get("stdout") or "")
    repair_command = ((repair_execution or {}).get("data") or {}).get("command") or []
    ok = (
        isinstance(execution, dict)
        and execution.get("success") is True
        and isinstance(repair_execution, dict)
        and repair_execution.get("success") is True
        and any(isinstance(item, dict) and item.get("status") == "repair_succeeded" for item in (retry_trace or []))
        and bool(stdout.strip())
    )
    report = {
        "ok": ok,
        "dependency_present_before": dependency_present_before,
        "package": package,
        "module": module,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "repair_command": repair_command,
        "final_stdout": stdout.strip(),
        "retry_trace": retry_trace,
        "evidence_dir": str(run_root),
    }
    (run_root / "smoke-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if temporary is not None:
        temporary.cleanup()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real isolated missing-dependency repair and retry smoke.")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--package", default="colorama==0.4.6")
    parser.add_argument("--module", default="colorama")
    args = parser.parse_args()
    try:
        report = run_execution_repair_smoke(
            state_dir=args.state_dir,
            package=args.package,
            module=args.module,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
