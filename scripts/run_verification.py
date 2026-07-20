"""一键验证并自动记录测试台账。

跑四项：pytest（worker+contracts）、ruff、dotnet build、dotnet test，
解析各自结果后向 docs/test-ledger.md 追加一行（含 git 短 hash 与时间戳）。
任何一项失败则退出码非 0。

用法：
    python scripts/run_verification.py --note "批次八 修A-D"
    python scripts/run_verification.py --note "..." --skip-dotnet   # 只跑 Python 侧

pytest/ruff 始终通过启动本脚本的同一个 Python 解释器运行，避免 PATH 上
其他虚拟环境的 console script 造成依赖错配；输出统一 PYTHONIOENCODING=utf-8。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "test-ledger.md"
PYTEST_TARGETS = [
    "backend/tests/unit/test_collaboration_worker_script.py",
    "backend/tests/unit/test_runtime_contracts.py",
    "backend/tests/unit/test_workflow_graph.py",
]
DESKTOP_PROJECT = "desktop/SpiritKinDesktop"
DESKTOP_TESTS = "desktop/SpiritKinDesktop.Tests"
def run_step(label: str, command: list[str]) -> tuple[int, str]:
    """运行一步并回显尾部输出；返回 (returncode, 全量输出)。"""
    print(f"\n=== {label}: {' '.join(command)}", flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-8:])
    print(tail, flush=True)
    print(f"=== {label} exit={proc.returncode}", flush=True)
    return proc.returncode, output


def parse_pytest_passed(output: str) -> str:
    match = re.search(r"(\d+) passed", output)
    return f"{match.group(1)} passed" if match else "?"


def parse_build_counts(output: str) -> str:
    """从 dotnet build 输出提取 警告/错误 数（中英文皆兼容）。"""
    warn = re.search(r"(\d+)\s*(?:个警告|Warning\(s\))", output)
    err = re.search(r"(\d+)\s*(?:个错误|Error\(s\))", output)
    if warn and err:
        return f"{warn.group(1)}/{err.group(1)}"
    return "?" if "error" in output.lower() else "0/0"


def parse_dotnet_test_passed(output: str) -> str:
    # 兼容英文 "Passed! - Failed: 0, Passed: 123" 与中文 "已通过! - 失败: 0，通过: 131"
    match = re.search(r"(?:Passed|通过)[:：]\s*(\d+)", output)
    return f"{match.group(1)} passed" if match else "?"


def git_short_hash() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip() or "?"
    except OSError:
        return "?"


def append_ledger_row(cells: list[str]) -> None:
    row = "| " + " | ".join(cells) + " |\n"
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(row)
    print(f"\n台账已追加：{LEDGER_PATH}")
    print(row.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一键验证并写入测试台账")
    parser.add_argument("--note", default="", help="批次说明（写进台账）")
    parser.add_argument("--skip-dotnet", action="store_true", help="跳过 dotnet build/test")
    args = parser.parse_args(argv)

    failures: list[str] = []
    results: dict[str, str] = {}

    code, output = run_step("pytest", [sys.executable, "-m", "pytest", "-q", *PYTEST_TARGETS])
    results["pytest"] = parse_pytest_passed(output)
    if code != 0:
        failures.append("pytest")

    code, _ = run_step("ruff", [sys.executable, "-m", "ruff", "check", "scripts", "backend"])
    results["ruff"] = "pass" if code == 0 else "FAIL"
    if code != 0:
        failures.append("ruff")

    if args.skip_dotnet:
        results["build"] = "skipped"
        results["dotnet_test"] = "skipped"
    else:
        code, output = run_step("dotnet build", ["dotnet", "build", DESKTOP_PROJECT])
        results["build"] = parse_build_counts(output)
        if code != 0:
            failures.append("dotnet build")
        code, output = run_step("dotnet test", ["dotnet", "test", DESKTOP_TESTS])
        results["dotnet_test"] = parse_dotnet_test_passed(output)
        if code != 0:
            failures.append("dotnet test")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    verdict = "PASS" if not failures else "FAIL(" + ",".join(failures) + ")"
    note = args.note or "-"
    append_ledger_row(
        [
            stamp,
            f"{note} @{git_short_hash()}",
            results["pytest"],
            results["ruff"],
            results["build"],
            results["dotnet_test"],
            "自动验证",
            verdict,
        ]
    )
    print(f"\n结论：{verdict}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
