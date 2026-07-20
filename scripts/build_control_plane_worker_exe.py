from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "spiritkin-control-plane-worker"


def build_worker_executable(
    *,
    output_dir: Path,
    work_dir: Path,
    python_executable: str = sys.executable,
    name: str = DEFAULT_NAME,
) -> dict[str, object]:
    output_dir = output_dir.resolve()
    work_dir = work_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--noconfirm",
        "--name",
        name,
        "--distpath",
        str(output_dir),
        "--workpath",
        str(work_dir / "build"),
        "--specpath",
        str(work_dir),
        str(ROOT_DIR / "scripts" / "control_plane_worker.py"),
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )
    executable = output_dir / (f"{name}.exe" if sys.platform == "win32" else name)
    if completed.returncode != 0 or not executable.is_file():
        detail = (completed.stderr or completed.stdout)[-4000:]
        raise RuntimeError(f"PyInstaller failed with exit={completed.returncode}: {detail}")
    smoke = subprocess.run(
        [str(executable), "--help"],
        cwd=str(ROOT_DIR),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if smoke.returncode != 0 or "Remote Worker" not in smoke.stdout:
        raise RuntimeError(f"built Worker failed --help smoke: exit={smoke.returncode}")
    return {
        "ok": True,
        "executable": str(executable),
        "size_bytes": executable.stat().st_size,
        "build_command": command,
        "help_smoke": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Control Plane Worker as a PyInstaller one-file executable.")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--work-dir", default="tmp/pyinstaller-control-plane-worker")
    parser.add_argument("--python", default=sys.executable, help="Python environment containing PyInstaller.")
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()
    try:
        report = build_worker_executable(
            output_dir=Path(args.output_dir),
            work_dir=Path(args.work_dir),
            python_executable=args.python,
            name=args.name,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
