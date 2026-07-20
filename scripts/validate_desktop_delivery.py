from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
WPF_PROJECT = ROOT_DIR / "desktop" / "SpiritKinDesktop" / "SpiritKinDesktop.csproj"
WPF_SOURCE_DIR = ROOT_DIR / "desktop" / "SpiritKinDesktop"
WPF_XAML = WPF_SOURCE_DIR / "MainWindow.xaml"
WPF_MAINWINDOW_CODE = WPF_SOURCE_DIR / "MainWindow.xaml.cs"
WPF_BUILD_EXE = ROOT_DIR / "desktop" / "SpiritKinDesktop" / "bin" / "Debug" / "net8.0-windows" / "SpiritKinDesktop.exe"
WPF_DELIVERY_BUILD_DIR = ROOT_DIR / "tmp" / "desktop-delivery-build"
WPF_DELIVERY_BUILD_EXE = WPF_DELIVERY_BUILD_DIR / "SpiritKinDesktop.exe"
LIVE2D_MANIFEST_VALIDATOR = ROOT_DIR / "scripts" / "validate_live2d_manifest.py"
DESKTOP_LAUNCHER = ROOT_DIR / "scripts" / "start_desktop_console.py"
DEFAULT_HTML_FILES = (
    ROOT_DIR / "frontend" / "desktop_console.html",
    ROOT_DIR / "frontend" / "spirit_avatar.html",
    ROOT_DIR / "frontend" / "live2d.html",
    ROOT_DIR / "frontend" / "index.html",
    ROOT_DIR / "frontend" / "avatar_3d.html",
)

FULL_PYTEST_TARGETS = (
    "backend/tests/unit/test_desktop_delivery_validation.py",
    "backend/tests/unit/test_agent_cluster.py",
    "backend/tests/unit/test_command_gateway.py",
    "backend/tests/unit/test_runtime.py",
    "backend/tests/unit/test_desktop_console_launcher.py",
    "backend/tests/unit/test_tooling_and_remote.py",
    "backend/tests/unit/test_audio_listener.py",
    "backend/tests/unit/test_local_pc_device.py",
    "backend/tests/unit/test_realtime_duplex_session.py",
    "backend/tests/unit/test_remote_worker.py",
    "backend/tests/unit/test_replay_harness.py",
    "backend/tests/unit/test_start_realtime_panel.py",
    "backend/tests/unit/test_tts_provider.py",
    "backend/tests/unit/test_voice_intent_context.py",
    "backend/tests/unit/test_smoke_asr.py",
    "backend/tests/unit/test_streaming_listener.py",
)

QUICK_PYTEST_TARGETS = (
    "backend/tests/unit/test_desktop_delivery_validation.py",
    "backend/tests/unit/test_desktop_console_launcher.py",
    "backend/tests/unit/test_start_realtime_panel.py::StartRealtimePanelTests::test_build_startup_commands_uses_python_modules",
    "backend/tests/unit/test_tooling_and_remote.py::ToolingAndRemoteTests::test_default_tool_registry_exposes_web_search_tool",
    "backend/tests/unit/test_agent_cluster.py::AgentClusterTests::test_cluster_routes_backend_web_search_to_tool",
    "backend/tests/unit/test_agent_cluster.py::AgentClusterTests::test_latest_wins_request_waits_for_resource_instead_of_queue_reply",
    "backend/tests/unit/test_agent_cluster.py::AgentClusterTests::test_cluster_routes_file_search_as_read_only_action",
    "backend/tests/unit/test_runtime.py::RuntimeTests::test_runtime_handle_input_routes_text_and_voice_through_same_agent",
    "backend/tests/unit/test_runtime.py::RuntimeTests::test_runtime_strips_hotword_prefix_from_voice_input",
    "backend/tests/unit/test_tts_provider.py",
)

JS_SCRIPT_TYPES = {"", "text/javascript", "application/javascript", "module"}
WPF_EVENT_ATTRIBUTES = {
    "Checked",
    "Click",
    "Closed",
    "Collapsed",
    "ContextMenuOpening",
    "Expanded",
    "GotKeyboardFocus",
    "KeyDown",
    "KeyUp",
    "Loaded",
    "LostKeyboardFocus",
    "MouseDoubleClick",
    "MouseDown",
    "MouseEnter",
    "MouseLeftButtonDown",
    "MouseLeftButtonUp",
    "MouseMove",
    "MouseRightButtonDown",
    "NavigationCompleted",
    "PreviewKeyDown",
    "PreviewMouseWheel",
    "PreviewTextInput",
    "SelectionChanged",
    "SizeChanged",
    "TextChanged",
    "Unchecked",
}
WPF_MAINWINDOW_MAX_LINES = 80
WPF_SPLIT_FILE_MAX_LINES = 500


@dataclass(frozen=True)
class InlineScript:
    source: Path
    index: int
    code: str
    is_module: bool


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    elapsed_seconds: float
    exit_code: int | None = None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def candidate_pythons(explicit: str = "") -> list[str]:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_python = os.environ.get("SPIRITKIN_PYTHON", "")
    if env_python:
        candidates.append(env_python)
    candidates.append(sys.executable)
    candidates.extend(
        [
            str(ROOT_DIR / ".venv" / "Scripts" / "python.exe"),
            r"D:\Anaconda\envs\spirit_kin_env\python.exe",
            r"D:\Anaconda\python.exe",
        ]
    )
    path_python = shutil.which("python")
    if path_python:
        candidates.append(path_python)
    return _dedupe(candidates)


def _python_has_pytest(python_exe: str) -> bool:
    try:
        completed = subprocess.run(
            [python_exe, "-m", "pytest", "--version"],
            cwd=ROOT_DIR,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def resolve_python(explicit: str = "") -> str:
    for candidate in candidate_pythons(explicit):
        if _python_has_pytest(candidate):
            return candidate
    raise RuntimeError("No Python with pytest was found. Set SPIRITKIN_PYTHON or pass --python.")


def resolve_executable(name: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    return name


def _script_type(attrs: str) -> str:
    match = re.search(r"\btype\s*=\s*([\"'])(?P<type>.*?)\1", attrs or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group("type").strip().lower().split(";", 1)[0]


def extract_inline_scripts(html_path: Path) -> list[InlineScript]:
    html = html_path.read_text(encoding="utf-8")
    scripts: list[InlineScript] = []
    matches = re.finditer(r"<script\b(?P<attrs>[^>]*)>(?P<code>[\s\S]*?)</script>", html, flags=re.IGNORECASE)
    for match in matches:
        attrs = match.group("attrs") or ""
        if re.search(r"\bsrc\s*=", attrs, flags=re.IGNORECASE):
            continue
        script_type = _script_type(attrs)
        if script_type not in JS_SCRIPT_TYPES:
            continue
        code = (match.group("code") or "").strip()
        if not code:
            continue
        scripts.append(InlineScript(source=html_path, index=len(scripts) + 1, code=code, is_module=script_type == "module"))
    return scripts


def html_check_commands(html_files: Sequence[Path], node_exe: str, temp_dir: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    for html_file in html_files:
        for script in extract_inline_scripts(html_file):
            suffix = ".mjs" if script.is_module else ".js"
            script_path = temp_dir / f"{html_file.name}.script{script.index}{suffix}"
            script_path.write_text(script.code, encoding="utf-8")
            commands.append([node_exe, "--check", str(script_path)])
    return commands


def _xml_local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def extract_wpf_event_handlers(xaml_path: Path) -> set[str]:
    root = ET.parse(xaml_path).getroot()
    handlers: set[str] = set()
    for element in root.iter():
        for raw_name, value in element.attrib.items():
            name = _xml_local_name(raw_name)
            if name not in WPF_EVENT_ATTRIBUTES:
                continue
            text = (value or "").strip()
            if re.fullmatch(r"[A-Za-z_]\w*", text):
                handlers.add(text)
    return handlers


def extract_csharp_method_names(source_dir: Path) -> set[str]:
    method_pattern = re.compile(
        r"\b(?:private|public|protected|internal)\s+"
        r"(?:async\s+)?(?:static\s+)?"
        r"[\w<>,?\[\]\s]+\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*\("
    )
    names: set[str] = set()
    for source_file in source_dir.rglob("*.cs"):
        text = source_file.read_text(encoding="utf-8")
        names.update(match.group("name") for match in method_pattern.finditer(text))
    return names


def wpf_split_structure_errors(
    *,
    source_dir: Path = WPF_SOURCE_DIR,
    xaml_path: Path = WPF_XAML,
    main_window_code: Path = WPF_MAINWINDOW_CODE,
    main_window_max_lines: int = WPF_MAINWINDOW_MAX_LINES,
    split_file_max_lines: int = WPF_SPLIT_FILE_MAX_LINES,
) -> list[str]:
    errors: list[str] = []
    if main_window_code.exists():
        main_window_lines = len(main_window_code.read_text(encoding="utf-8").splitlines())
        if main_window_lines > main_window_max_lines:
            errors.append(f"{_display_path(main_window_code)} has {main_window_lines} lines; expected <= {main_window_max_lines}.")
    else:
        errors.append(f"{_display_path(main_window_code)} is missing.")

    split_roots = [source_dir / "Features", source_dir / "ViewModels"]
    for split_root in split_roots:
        if not split_root.exists():
            errors.append(f"{_display_path(split_root)} is missing.")
            continue
        for source_file in split_root.rglob("*.cs"):
            line_count = len(source_file.read_text(encoding="utf-8").splitlines())
            if line_count >= split_file_max_lines:
                errors.append(f"{_display_path(source_file)} has {line_count} lines; expected < {split_file_max_lines}.")

    if xaml_path.exists():
        handlers = extract_wpf_event_handlers(xaml_path)
        methods = extract_csharp_method_names(source_dir)
        missing = sorted(handlers - methods)
        if missing:
            errors.append(f"XAML event handlers missing from C# partials: {', '.join(missing)}.")
    else:
        errors.append(f"{_display_path(xaml_path)} is missing.")

    return errors


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def run_command(
    name: str,
    command: Sequence[str],
    *,
    cwd: Path = ROOT_DIR,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> StepResult:
    started = time.perf_counter()
    print(f"\n== {name} ==")
    print(_format_command(command))
    try:
        completed = subprocess.run([str(part) for part in command], cwd=cwd, timeout=timeout, check=False, env=env)
        ok = completed.returncode == 0
        return StepResult(name=name, ok=ok, elapsed_seconds=time.perf_counter() - started, exit_code=completed.returncode)
    except FileNotFoundError as exc:
        print(f"[FAIL] executable not found: {exc.filename}")
        return StepResult(name=name, ok=False, elapsed_seconds=time.perf_counter() - started, exit_code=None)
    except subprocess.TimeoutExpired:
        print(f"[FAIL] timed out after {timeout}s")
        return StepResult(name=name, ok=False, elapsed_seconds=time.perf_counter() - started, exit_code=None)


def run_capture_command(
    name: str,
    command: Sequence[str],
    *,
    cwd: Path = ROOT_DIR,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[StepResult, str]:
    started = time.perf_counter()
    print(f"\n== {name} ==")
    print(_format_command(command))
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            cwd=cwd,
            timeout=timeout,
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
        if completed.stderr:
            print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
        return (
            StepResult(name=name, ok=completed.returncode == 0, elapsed_seconds=time.perf_counter() - started, exit_code=completed.returncode),
            output,
        )
    except FileNotFoundError as exc:
        print(f"[FAIL] executable not found: {exc.filename}")
        return StepResult(name=name, ok=False, elapsed_seconds=time.perf_counter() - started, exit_code=None), ""
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        print(f"[FAIL] timed out after {timeout}s")
        return StepResult(name=name, ok=False, elapsed_seconds=time.perf_counter() - started, exit_code=None), output


def run_html_checks(html_files: Sequence[Path], node_exe: str) -> list[StepResult]:
    results: list[StepResult] = []
    with tempfile.TemporaryDirectory(prefix="spiritkin_jscheck_") as temp:
        temp_dir = Path(temp)
        commands = html_check_commands(html_files, node_exe, temp_dir)
        if not commands:
            print("\n== frontend inline script syntax ==")
            print("[FAIL] no inline scripts found")
            return [StepResult("frontend inline script syntax", ok=False, elapsed_seconds=0, exit_code=None)]
        for command in commands:
            script_name = Path(command[-1]).name
            results.append(run_command(f"node --check {script_name}", command, timeout=60))
    return results


def run_wpf_structure_check() -> StepResult:
    started = time.perf_counter()
    print("\n== WPF split structure ==")
    errors = wpf_split_structure_errors()
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return StepResult(name="WPF split structure", ok=False, elapsed_seconds=time.perf_counter() - started, exit_code=1)
    print("[PASS] MainWindow shell, split file sizes, and XAML event handlers look consistent.")
    return StepResult(name="WPF split structure", ok=True, elapsed_seconds=time.perf_counter() - started, exit_code=0)


def build_pytest_command(python_exe: str, targets: Sequence[str]) -> list[str]:
    return [python_exe, "-m", "pytest", *targets, "-q"]


def _free_tcp_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [int(sock.getsockname()[1]) for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _port_accepts_connection(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ports_closed(ports: Sequence[int], *, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(not _port_accepts_connection("127.0.0.1", int(port)) for port in ports):
            return True
        time.sleep(0.2)
    return all(not _port_accepts_connection("127.0.0.1", int(port)) for port in ports)


def _with_ok(result: StepResult, ok: bool) -> StepResult:
    return StepResult(name=result.name, ok=result.ok and ok, elapsed_seconds=result.elapsed_seconds, exit_code=result.exit_code)


def run_launcher_smoke(python_exe: str, *, timeout: int = 90) -> list[StepResult]:
    state_file = ROOT_DIR / "tmp" / f"desktop_launcher_smoke_{os.getpid()}.json"
    env = dict(os.environ)
    env["SPIRITKIN_DESKTOP_LAUNCH_STATE_FILE"] = str(state_file)
    frontend_port, events_port, command_port = _free_tcp_ports(3)
    ports = {
        "frontend": frontend_port,
        "events": events_port,
        "command": command_port,
    }
    base_command = [
        python_exe,
        str(DESKTOP_LAUNCHER),
        "--frontend-port",
        str(ports["frontend"]),
        "--events-port",
        str(ports["events"]),
        "--command-port",
        str(ports["command"]),
    ]
    results: list[StepResult] = []
    try:
        start_result, _ = run_capture_command("desktop launcher start smoke", [*base_command, "--no-open"], timeout=timeout, env=env)
        results.append(start_result)
        status_result, status_output = run_capture_command("desktop launcher status smoke", [python_exe, str(DESKTOP_LAUNCHER), "--status"], timeout=30, env=env)
        expected_status = (
            f"frontend_port={ports['frontend']} listening=True",
            f"events_port={ports['events']} listening=True",
            f"command_port={ports['command']} listening=True",
        )
        status_ok = all(expected in status_output for expected in expected_status)
        if not status_ok:
            print("[FAIL] launcher status did not report every expected port as listening")
        results.append(_with_ok(status_result, status_ok))
    finally:
        stop_result, stop_output = run_capture_command("desktop launcher stop smoke", [python_exe, str(DESKTOP_LAUNCHER), "--stop"], timeout=30, env=env)
        stop_ok = "stopped" in stop_output and not state_file.exists() and _ports_closed(tuple(ports.values()))
        if not stop_ok:
            print("[FAIL] launcher stop did not clean recorded services, state file, or ports")
        results.append(_with_ok(stop_result, stop_ok))
    return results


def selected_pytest_targets(quick: bool, explicit_targets: Sequence[str]) -> tuple[str, ...]:
    if explicit_targets:
        return tuple(explicit_targets)
    return QUICK_PYTEST_TARGETS if quick else FULL_PYTEST_TARGETS


def run_delivery_checks(args: argparse.Namespace) -> list[StepResult]:
    results: list[StepResult] = []
    quick = bool(getattr(args, "quick", False))
    skip_launcher_smoke = bool(getattr(args, "skip_launcher_smoke", False))
    with_launcher_smoke = bool(getattr(args, "with_launcher_smoke", False))
    should_run_launcher_smoke = with_launcher_smoke or (not quick and not skip_launcher_smoke)
    skip_pytest = bool(getattr(args, "skip_pytest", False))
    skip_manifest = bool(getattr(args, "skip_manifest", False))
    skip_js = bool(getattr(args, "skip_js", False))
    skip_dotnet = bool(getattr(args, "skip_dotnet", False))
    skip_wpf_structure = bool(getattr(args, "skip_wpf_structure", False))
    needs_python = not skip_pytest or not skip_manifest or should_run_launcher_smoke
    python_exe = resolve_python(getattr(args, "python", "")) if needs_python else ""
    node_exe = resolve_executable("node", getattr(args, "node", "")) if not skip_js else ""
    dotnet_exe = resolve_executable("dotnet", getattr(args, "dotnet", "")) if not skip_dotnet else ""

    if not skip_wpf_structure:
        results.append(run_wpf_structure_check())

    if not skip_dotnet:
        build_result = run_command(
            "WPF desktop build",
            [dotnet_exe, "build", str(WPF_PROJECT), "--no-restore", "-o", str(WPF_DELIVERY_BUILD_DIR)],
            timeout=int(getattr(args, "dotnet_timeout", 180)),
        )
        results.append(build_result)
        if build_result.ok and not bool(getattr(args, "skip_wpf_smoke", False)):
            results.append(run_command("WPF desktop startup smoke", [str(WPF_DELIVERY_BUILD_EXE), "--smoke-startup"], timeout=int(getattr(args, "wpf_smoke_timeout", 60))))

    if not skip_pytest:
        pytest_targets = selected_pytest_targets(quick, getattr(args, "pytest_target", []))
        results.append(run_command("desktop Python regression tests", build_pytest_command(python_exe, pytest_targets), timeout=int(getattr(args, "pytest_timeout", 420))))

    if not skip_manifest:
        results.append(run_command("Live2D manifest validation", [python_exe, str(LIVE2D_MANIFEST_VALIDATOR)], timeout=60))

    if not skip_js:
        results.extend(run_html_checks(tuple(Path(path) for path in getattr(args, "html_file", [])), node_exe))

    if should_run_launcher_smoke:
        results.extend(run_launcher_smoke(python_exe, timeout=int(getattr(args, "launcher_smoke_timeout", 90))))

    return results


def print_summary(results: Sequence[StepResult]) -> None:
    print("\n== desktop delivery validation summary ==")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        code = "" if result.exit_code is None else f" exit={result.exit_code}"
        print(f"{status} {result.name} ({result.elapsed_seconds:.1f}s{code})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeatable SpiritKin desktop delivery validation gates.")
    parser.add_argument("--quick", action="store_true", help="Run the focused desktop delivery subset instead of the full regression set.")
    parser.add_argument("--python", default="", help="Python executable to use for pytest and manifest validation.")
    parser.add_argument("--node", default="", help="Node.js executable to use for inline frontend script syntax checks.")
    parser.add_argument("--dotnet", default="", help="dotnet executable to use for WPF build.")
    parser.add_argument("--pytest-target", action="append", default=[], help="Override pytest targets; can be passed multiple times.")
    parser.add_argument("--html-file", action="append", default=None, help="HTML file to inspect for inline scripts.")
    parser.add_argument("--skip-dotnet", action="store_true", help="Skip WPF dotnet build.")
    parser.add_argument("--skip-wpf-structure", action="store_true", help="Skip static WPF split/XAML handler checks.")
    parser.add_argument("--skip-wpf-smoke", action="store_true", help="Skip the WPF no-window --smoke-startup check after build.")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip Python regression tests.")
    parser.add_argument("--skip-manifest", action="store_true", help="Skip Live2D manifest validation.")
    parser.add_argument("--skip-js", action="store_true", help="Skip frontend inline JavaScript syntax checks.")
    parser.add_argument("--skip-launcher-smoke", action="store_true", help="Skip the full-gate non-GUI start/status/stop launcher smoke.")
    parser.add_argument("--with-launcher-smoke", action="store_true", help="Run the non-GUI start/status/stop launcher smoke even with --quick.")
    parser.add_argument("--dotnet-timeout", type=int, default=180, help="Timeout for WPF build in seconds.")
    parser.add_argument("--wpf-smoke-timeout", type=int, default=60, help="Timeout for WPF --smoke-startup in seconds.")
    parser.add_argument("--pytest-timeout", type=int, default=420, help="Timeout for pytest in seconds.")
    parser.add_argument("--launcher-smoke-timeout", type=int, default=90, help="Timeout for desktop launcher start smoke in seconds.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.html_file is None:
        args.html_file = [str(path) for path in DEFAULT_HTML_FILES]
    try:
        results = run_delivery_checks(args)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print_summary(results)
    return 0 if results and all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
