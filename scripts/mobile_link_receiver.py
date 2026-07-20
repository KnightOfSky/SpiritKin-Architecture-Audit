from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import socket
import sys
import zipfile
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
ROOT_TEXT = str(ROOT)
if ROOT_TEXT not in sys.path:
    sys.path.insert(0, ROOT_TEXT)

try:
    from scripts.control_plane_store import (
        DEFAULT_ACCOUNT_ID,
        DEFAULT_WORKSPACE_ID,
        ControlPlaneStore,
        normalize_workspace_id,
        safe_name,
    )
    from scripts.control_plane_worker import WORKER_VERSION, build_worker_package
except ModuleNotFoundError:
    from control_plane_store import (
        DEFAULT_ACCOUNT_ID,
        DEFAULT_WORKSPACE_ID,
        ControlPlaneStore,
        normalize_workspace_id,
        safe_name,
    )
    from control_plane_worker import WORKER_VERSION, build_worker_package


OUT_DIR = ROOT / "state" / "mobile-links"
QUEUE = OUT_DIR / "links.jsonl"
LATEST = OUT_DIR / "latest-link.txt"
DEFAULT_PORT = 8791
MAX_BODY_BYTES = 25 * 1024 * 1024
DEFAULT_IOS_SHORTCUT_LLM_TIMEOUT_SECONDS = 120.0
APK_PATH = ROOT / "mobile-link-bridge" / "out" / "mobile-link-bridge.apk"
APK_MANIFEST = ROOT / "mobile-link-bridge" / "AndroidManifest.xml"
APK_RELEASE_MANIFEST = ROOT / "mobile-link-bridge" / "out" / "release-manifest.json"
APK_RELEASE_HISTORY = ROOT / "mobile-link-bridge" / "out" / "release-history.json"
APK_RELEASE_DIR = ROOT / "mobile-link-bridge" / "out" / "releases"
WORKER_PACKAGE_DIR = ROOT / "state" / "workers" / "releases"
WORKER_PACKAGE_NAME = "spiritkin-control-plane-worker.zip"
WORKER_EXECUTABLE_PATH = ROOT / "dist" / "spiritkin-control-plane-worker.exe"
WORKER_MANIFEST_SIGNING_SECRET_ENV = "SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET"
PDD_WEB_LINK_RE = re.compile(r"https?://[^\s\"'<>]*\b(?:yangkeduo|pinduoduo)\.com/[^\s\"'<>]*")


def extract_pdd_link(text: object) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    match = PDD_WEB_LINK_RE.search(value)
    return match.group(0) if match else ""


def is_supported_pdd_link(link: str) -> bool:
    return bool(extract_pdd_link(link) == link.strip())


def write_legacy_mobile_link(event: dict[str, object]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    LATEST.write_text(str(event["link"]) + "\n", encoding="utf-8")


def android_apk_manifest(base_url: str) -> dict[str, object]:
    release_manifest = _load_release_manifest()
    if release_manifest:
        release_manifest["download_url"] = f"{base_url}/android/apk"
        release_manifest["download_file"] = str(release_manifest.get("download_file") or APK_PATH.name)
        release_manifest.setdefault("app_id", release_manifest.get("package_name") or "com.spiritkin.mobilelinkbridge")
        release_manifest.setdefault("rollback", _rollback_manifest([]))
        release_manifest["rollback"] = _with_rollback_download_urls(release_manifest["rollback"], base_url)
        release_manifest["serving_validation"] = _release_manifest_serving_validation(release_manifest)
        return release_manifest

    manifest_text = APK_MANIFEST.read_text(encoding="utf-8", errors="replace") if APK_MANIFEST.exists() else ""
    version_code = _regex_group(manifest_text, r'android:versionCode="([^"]+)"')
    version_name = _regex_group(manifest_text, r'android:versionName="([^"]+)"')
    package_name = _regex_group(manifest_text, r'package="([^"]+)"') or "com.spiritkin.mobilelinkbridge"
    min_sdk = _regex_group(manifest_text, r'android:minSdkVersion="([^"]+)"')
    target_sdk = _regex_group(manifest_text, r'android:targetSdkVersion="([^"]+)"')
    size = APK_PATH.stat().st_size if APK_PATH.exists() else 0
    digest = ""
    updated_at = ""
    if APK_PATH.exists():
        digest = hashlib.sha256(APK_PATH.read_bytes()).hexdigest()
        updated_at = datetime.fromtimestamp(APK_PATH.stat().st_mtime, UTC).isoformat()
    current_version_code = int(version_code) if version_code.isdigit() else 0
    current_version_name = version_name or str(current_version_code)
    download_url = f"{base_url}/android/apk"
    return {
        "manifest_version": 2,
        "app_id": package_name,
        "package_name": package_name,
        "version_code": current_version_code,
        "version_name": current_version_name,
        "download_url": download_url,
        "sha256": digest,
        "size_bytes": size,
        "updated_at": updated_at,
        "compatibility": {
            "min_sdk": int(min_sdk) if min_sdk.isdigit() else 23,
            "target_sdk": int(target_sdk) if target_sdk.isdigit() else 35,
            "max_sdk": 0,
            "requires_unknown_app_install_permission": True,
        },
        "integrity": {
            "algorithm": "sha256",
            "sha256": digest,
            "size_bytes": size,
            "signature_scheme": "apk_signature_v2_or_newer",
            "same_package_signature_required": True,
        },
        "rollback": {
            "supported": True,
            "strategy": "serve an older signed APK with matching package/signing key if release-manifest.json is rolled back",
            "previous_versions": [],
        },
        "notes": "SpiritKin Control Bridge Android update",
    }


def _load_release_manifest() -> dict[str, object]:
    if not APK_RELEASE_MANIFEST.exists():
        return {}
    try:
        data = json.loads(APK_RELEASE_MANIFEST.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    manifest = dict(data)
    package_name = str(manifest.get("package_name") or manifest.get("app_id") or "com.spiritkin.mobilelinkbridge")
    manifest["manifest_version"] = int(manifest.get("manifest_version") or 2)
    manifest["app_id"] = str(manifest.get("app_id") or package_name)
    manifest["package_name"] = package_name
    manifest["version_code"] = int(manifest.get("version_code") or 0)
    manifest["version_name"] = str(manifest.get("version_name") or manifest["version_code"])
    manifest["sha256"] = str(manifest.get("sha256") or "").lower()
    manifest["size_bytes"] = int(manifest.get("size_bytes") or 0)
    manifest["updated_at"] = str(manifest.get("updated_at") or "")
    manifest["compatibility"] = _dict_or_default(
        manifest.get("compatibility"),
        {"min_sdk": 23, "target_sdk": 35, "max_sdk": 0, "requires_unknown_app_install_permission": True},
    )
    manifest["integrity"] = _dict_or_default(
        manifest.get("integrity"),
        {
            "algorithm": "sha256",
            "sha256": manifest["sha256"],
            "size_bytes": manifest["size_bytes"],
            "signature_scheme": "apk_signature_v2_or_newer",
            "same_package_signature_required": True,
        },
    )
    manifest["rollback"] = _dict_or_default(manifest.get("rollback"), _rollback_manifest([]))
    manifest["notes"] = str(manifest.get("notes") or "SpiritKin Control Bridge Android update")
    return manifest


def _release_manifest_serving_validation(manifest: dict[str, object]) -> dict[str, object]:
    expected_size = int(manifest.get("size_bytes") or 0)
    expected_sha256 = str(manifest.get("sha256") or "").lower()
    download_file = str(manifest.get("download_file") or APK_PATH.name)
    result: dict[str, object] = {
        "status": "ok",
        "download_file": download_file,
        "expected_size_bytes": expected_size,
        "expected_sha256": expected_sha256,
    }
    try:
        safe_download_file = safe_apk_filename(download_file)
    except KeyError:
        result["status"] = "invalid_download_file"
        result["expected_local_file"] = APK_PATH.name
        return result
    if safe_download_file != APK_PATH.name:
        result["status"] = "download_file_mismatch"
        result["expected_local_file"] = APK_PATH.name
        return result
    if not APK_PATH.exists():
        result["status"] = "missing_apk"
        return result
    actual_size = APK_PATH.stat().st_size
    actual_sha256 = hashlib.sha256(APK_PATH.read_bytes()).hexdigest()
    result["actual_size_bytes"] = actual_size
    result["actual_sha256"] = actual_sha256
    if expected_size and expected_size != actual_size:
        result["status"] = "size_mismatch"
    elif expected_sha256 and not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        result["status"] = "invalid_manifest_sha256"
    elif expected_sha256 and expected_sha256 != actual_sha256:
        result["status"] = "sha256_mismatch"
    elif not expected_sha256:
        result["status"] = "missing_manifest_sha256"
    return result


def _dict_or_default(value: object, fallback: dict[str, object]) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else dict(fallback)


def _rollback_manifest(previous_versions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "supported": True,
        "strategy": "serve an older signed APK with matching package/signing key if release-manifest.json is rolled back",
        "previous_versions": previous_versions,
    }


def _with_rollback_download_urls(rollback: object, base_url: str) -> dict[str, object]:
    result = _dict_or_default(rollback, _rollback_manifest([]))
    previous = result.get("previous_versions") if isinstance(result.get("previous_versions"), list) else []
    next_previous: list[dict[str, object]] = []
    for item in previous:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        version_code = str(next_item.get("version_code") or "").strip()
        if version_code and not next_item.get("download_url"):
            next_item["download_url"] = f"{base_url}/android/apk?version_code={version_code}"
        next_previous.append(next_item)
    result["previous_versions"] = next_previous
    return result


def android_apk_file(version_code: str | int | None = None) -> dict[str, object]:
    version = str(version_code or "").strip()
    if not version:
        if not APK_PATH.exists():
            raise FileNotFoundError(str(APK_PATH))
        return {
            "path": APK_PATH,
            "filename": APK_PATH.name,
            "version_code": "",
        }
    if not re.fullmatch(r"\d{1,20}", version):
        raise KeyError(f"invalid apk version_code: {version}")
    for release in _release_history_items():
        if str(release.get("version_code") or "") != version:
            continue
        archive_file = str(release.get("archive_file") or "").strip()
        file_name = safe_apk_filename(archive_file or str(release.get("file_name") or ""))
        candidate = (APK_RELEASE_DIR / file_name) if not archive_file else (APK_RELEASE_DIR.parent / archive_file)
        candidate = candidate.resolve()
        allowed = APK_RELEASE_DIR.resolve()
        if candidate != allowed and allowed not in candidate.parents:
            raise KeyError(f"invalid archived apk path: {archive_file}")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(str(candidate))
        return {
            "path": candidate,
            "filename": candidate.name,
            "version_code": version,
        }
    raise KeyError(f"unknown apk version_code: {version}")


def worker_package_source_files() -> list[Path]:
    files = [
        ROOT / "scripts" / "control_plane_worker.py",
        ROOT / "docs" / "light_cloud_control_plane.md",
        ROOT / "docs" / "mobile_link_bridge.md",
    ]
    if WORKER_EXECUTABLE_PATH.is_file():
        files.append(WORKER_EXECUTABLE_PATH)
    return files


def ensure_worker_package_file(*, force: bool = False) -> Path:
    package_path = WORKER_PACKAGE_DIR / WORKER_PACKAGE_NAME
    needs_build = force or not package_path.exists()
    if not needs_build and package_path.exists():
        package_mtime = package_path.stat().st_mtime
        needs_build = any(path.exists() and path.stat().st_mtime > package_mtime for path in worker_package_source_files())
    if not needs_build:
        try:
            with zipfile.ZipFile(package_path) as archive:
                archive.getinfo("worker-release-manifest.json")
        except (KeyError, zipfile.BadZipFile):
            needs_build = True
    if not needs_build:
        return package_path
    signing_secret = os.environ.get(WORKER_MANIFEST_SIGNING_SECRET_ENV, "").strip()
    return build_worker_package(
        str(package_path),
        signing_secret=signing_secret,
        version=WORKER_VERSION,
        worker_executable=WORKER_EXECUTABLE_PATH if WORKER_EXECUTABLE_PATH.is_file() else None,
    )


def worker_package_manifest(base_url: str) -> dict[str, object]:
    package_path = ensure_worker_package_file()
    package_bytes = package_path.read_bytes()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()
    package_size = len(package_bytes)
    with zipfile.ZipFile(package_path) as archive:
        release_manifest = json.loads(archive.read("worker-release-manifest.json").decode("utf-8"))
    if not isinstance(release_manifest, dict):
        release_manifest = {}
    manifest = dict(release_manifest)
    manifest.setdefault("manifest_version", 1)
    manifest.setdefault("package", "spiritkin-control-plane-worker")
    manifest.setdefault("version", WORKER_VERSION)
    manifest.setdefault("package_format", "zip")
    manifest["download_url"] = f"{base_url}/worker/package"
    manifest["download_file"] = package_path.name
    manifest["sha256"] = package_sha256
    manifest["size_bytes"] = package_size
    manifest["updated_at"] = datetime.fromtimestamp(package_path.stat().st_mtime, UTC).isoformat()
    manifest["package_integrity"] = {
        "algorithm": "sha256",
        "sha256": package_sha256,
        "size_bytes": package_size,
    }
    manifest["serving_validation"] = {
        "status": "ok",
        "download_file": package_path.name,
        "expected_sha256": package_sha256,
        "expected_size_bytes": package_size,
        "actual_sha256": package_sha256,
        "actual_size_bytes": package_size,
    }
    return manifest


def _release_history_items() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if APK_RELEASE_HISTORY.exists():
        try:
            history = json.loads(APK_RELEASE_HISTORY.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            history = {}
        releases = history.get("releases") if isinstance(history, dict) else []
        if isinstance(releases, list):
            items.extend(dict(item) for item in releases if isinstance(item, dict))
    manifest = _load_release_manifest()
    if manifest:
        items.insert(
            0,
            {
                "version_code": manifest.get("version_code"),
                "version_name": manifest.get("version_name"),
                "file_name": manifest.get("download_file") or APK_PATH.name,
                "archive_file": manifest.get("archive_file") or "",
                "sha256": manifest.get("sha256") or "",
                "size_bytes": manifest.get("size_bytes") or 0,
            },
        )
    seen: set[str] = set()
    unique: list[dict[str, object]] = []
    for item in items:
        key = str(item.get("version_code") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def safe_apk_filename(value: str) -> str:
    name = Path(str(value or "")).name
    if not name.endswith(".apk"):
        raise KeyError(f"invalid apk filename: {value}")
    if name != safe_name_for_file(name):
        raise KeyError(f"invalid apk filename: {value}")
    return name


def safe_name_for_file(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value).strip("._")


def secrets_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))


def json_for_script(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def ios_terminal_bootstrap_snapshot(auth_required: bool = False) -> dict[str, object]:
    return {
        "ok": True,
        "auth_required": auth_required,
        "accounts": {"schema_version": "spiritkin.control_plane.accounts.v1", "total": 0, "items": []},
        "artifacts": {"count": 0},
        "android": {"device_count": 0, "devices": []},
        "remote_workers": {"count": 0},
        "workflow_runs": {"count": 0},
        "pairings": {"pending_count": 0, "bound_count": 0, "recent_pending": [], "recent_history": [], "bindings": [], "binding_history": []},
    }


def control_home_html(snapshot: dict[str, object]) -> str:
    artifacts = (snapshot.get("artifacts") or {}) if isinstance(snapshot.get("artifacts"), dict) else {}
    android = (snapshot.get("android") or {}) if isinstance(snapshot.get("android"), dict) else {}
    workers = (snapshot.get("remote_workers") or {}) if isinstance(snapshot.get("remote_workers"), dict) else {}
    runs = (snapshot.get("workflow_runs") or {}) if isinstance(snapshot.get("workflow_runs"), dict) else {}
    pairings = (snapshot.get("pairings") or {}) if isinstance(snapshot.get("pairings"), dict) else {}
    auth_required = bool(snapshot.get("auth_required"))
    auth_note = "需要 Management Token 后，进入控制台可刷新真实状态。" if auth_required else "状态来自当前 control plane snapshot。"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SpiritKin Control</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; background: #f3f6fb; }}
    body {{ margin: 0; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 26px; margin: 0 0 6px; }}
    h2 {{ font-size: 16px; margin: 0 0 10px; }}
    .muted {{ color: #64748b; font-size: 13px; }}
    .top {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; flex-wrap: wrap; margin-bottom: 16px; }}
    .status {{ border: 1px solid #bed0ea; border-radius: 999px; padding: 6px 10px; background: white; color: #334155; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 16px 0; }}
    .card {{ background: white; border: 1px solid #dbe5f3; border-radius: 8px; padding: 14px; }}
    .value {{ font-size: 24px; font-weight: 700; color: #0757c8; margin-top: 6px; }}
    .flow {{ display: grid; gap: 10px; margin: 16px 0; }}
    .step {{ display: grid; grid-template-columns: 36px minmax(0, 1fr) auto; gap: 12px; align-items: center; background: white; border: 1px solid #dbe5f3; border-radius: 8px; padding: 12px; }}
    .num {{ width: 30px; height: 30px; border-radius: 999px; background: #0757c8; color: white; display: grid; place-items: center; font-weight: 700; }}
    .step strong {{ display: block; margin-bottom: 3px; }}
    a.button {{ display: inline-block; border-radius: 7px; padding: 10px 12px; background: #0757c8; color: white; text-decoration: none; font-weight: 700; white-space: nowrap; }}
    a.secondary {{ background: #e8eef8; color: #172033; }}
    .links {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .links a {{ display: block; background: white; border: 1px solid #dbe5f3; border-radius: 8px; padding: 12px; color: #0757c8; text-decoration: none; font-weight: 700; }}
    @media (max-width: 720px) {{
      main {{ padding: 16px; }}
      .grid, .links {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .step {{ grid-template-columns: 36px minmax(0, 1fr); }}
      .step a.button {{ grid-column: 1 / -1; text-align: center; }}
    }}
  </style>
</head>
<body>
<main id="productEntry">
  <div class="top">
    <div>
      <h1>SpiritKin Control</h1>
      <div class="muted">统一入口：配对设备、管理工作区、启动工作流、查看工作素材和诊断。</div>
    </div>
    <div class="status">{html.escape(auth_note)}</div>
  </div>
  <section class="grid" aria-label="status summary">
    <div class="card"><h2>Android 手机端</h2><div class="value">{android.get("device_count", 0)}</div><div class="muted">设备</div></div>
    <div class="card"><h2>远程执行端</h2><div class="value">{workers.get("count", 0)}</div><div class="muted">执行节点</div></div>
    <div class="card"><h2>工作流运行</h2><div class="value">{runs.get("count", 0)}</div><div class="muted">运行</div></div>
    <div class="card"><h2>工作素材</h2><div class="value">{artifacts.get("count", 0)}</div><div class="muted">商品图、截图、移动端文件</div></div>
  </section>
  <section id="mainWorkflow" class="flow" aria-label="main workflow">
    <div class="step">
      <div class="num">1</div>
      <div><strong>配对 Android 手机端</strong><div class="muted">生成工作区配对码，手机扫码或打开链接完成绑定。当前绑定 {pairings.get("bound_count", 0)} 个。</div></div>
      <a class="button" href="/ios/control#workspace-devices">进入配对</a>
    </div>
    <div class="step">
      <div class="num">2</div>
      <div><strong>上传或选择工作图片</strong><div class="muted">管理商品图、截图和手机上传文件。</div></div>
      <a class="button" href="/ios/control#artifacts-upload">管理图片</a>
    </div>
    <div class="step">
      <div class="num">3</div>
      <div><strong>启动工作流</strong><div class="muted">从电商自动上架、本地命令、LangGraph 或 CrewAI 模板启动受控运行。</div></div>
      <a class="button" href="/ios/control#workflow">启动运行</a>
    </div>
    <div class="step">
      <div class="num">4</div>
      <div><strong>诊断 Android 和查看记录</strong><div class="muted">检查 Accessibility、截图授权、命令 preflight、运行状态和 action log。</div></div>
      <a class="button" href="/ios/control#android-command">诊断/命令</a>
    </div>
  </section>
  <section class="links" aria-label="quick links">
    <a href="/ios/control">完整控制台</a>
    <a href="/pairing">独立配对页</a>
    <a href="/android/apk/manifest">APK 更新 manifest</a>
    <a href="/android/apk">下载 Android APK</a>
    <a href="/worker/package/manifest">Worker 包 manifest</a>
    <a href="/worker/package">下载 Worker 包</a>
    <a href="/ios/control/snapshot">状态 Snapshot</a>
    <a href="/ios/control/action-log">Action Log</a>
  </section>
</main>
</body>
</html>
"""


def _regex_group(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def ios_terminal_html(snapshot: dict[str, object]) -> str:
    artifact_count = ((snapshot.get("artifacts") or {}) if isinstance(snapshot.get("artifacts"), dict) else {}).get("count", 0)
    android = (snapshot.get("android") or {}) if isinstance(snapshot.get("android"), dict) else {}
    workers = (snapshot.get("remote_workers") or {}) if isinstance(snapshot.get("remote_workers"), dict) else {}
    runs = (snapshot.get("workflow_runs") or {}) if isinstance(snapshot.get("workflow_runs"), dict) else {}
    initial_snapshot = json_for_script(snapshot)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="manifest" href="/ios/terminal.webmanifest">
  <link rel="apple-touch-icon" href="/ios/apple-touch-icon.png">
  <link rel="icon" href="/ios/icon.svg" type="image/svg+xml">
  <title>SpiritKin 电商运营 Terminal</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
      --fx-canvas: #eef5fa; --fx-surface: #f5fbff; --fx-surface-2: #e9f2f7; --fx-surface-3: #deeaf1;
      --fx-line: #c4d4df; --fx-line-strong: #70869a; --fx-text: #273a4c; --fx-muted: #4d667b;
      --fx-faint: #5f7689; --fx-primary: #126db6; --fx-on-primary: #ffffff; --fx-copper: #b44a00;
      --fx-success: #187a43; --fx-success-bg: #e7f5ec; --fx-warning: #875900; --fx-warning-bg: #fff3d6;
      --fx-danger: #b52a2e; --fx-danger-bg: #fcebec; --fx-info-bg: #e8f2fb;
      --fx-ink: #080d14; --fx-ink-text: #dbeafe;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --fx-canvas: #15110c; --fx-surface: #1f1914; --fx-surface-2: #2a221c; --fx-surface-3: #372e27;
        --fx-line: #4e463f; --fx-line-strong: #73665d; --fx-text: #f0eae4; --fx-muted: #aca39b;
        --fx-faint: #8b837b; --fx-primary: #e28e3a; --fx-on-primary: #1f1914; --fx-copper: #e99541;
        --fx-success: #5bcc80; --fx-success-bg: #173523; --fx-warning: #efbc4b; --fx-warning-bg: #3a2b10;
        --fx-danger: #ff6b68; --fx-danger-bg: #3b1d1c; --fx-info-bg: #172a43;
        --fx-ink: #070d15; --fx-ink-text: #dbeafe;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--fx-canvas); color: var(--fx-text); }}
    main {{ padding: 18px; max-width: 760px; margin: 0 auto; }}
    .controller-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }}
    .controller-header h1 {{ font-size: 24px; margin: 0 0 4px; letter-spacing: 0; }}
    .role-badge {{ flex: none; border: 1px solid var(--fx-line-strong); border-radius: 999px; padding: 5px 9px; color: var(--fx-copper); font-size: 12px; font-weight: 700; }}
    .avatar-stage {{ position: relative; height: clamp(280px, 42vh, 360px); margin-inline: -18px; overflow: hidden; background: var(--fx-surface-2); border-block: 1px solid var(--fx-line); }}
    .avatar-stage iframe {{ display: block; width: 100%; height: 100%; border: 0; background: var(--fx-surface-2); }}
    h2 {{ font-size: 16px; margin: 0 0 10px; letter-spacing: 0; }}
    .muted {{ color: var(--fx-muted); font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0 -18px 16px; border-bottom: 1px solid var(--fx-line); background: var(--fx-surface); }}
    .grid .summary-item {{ min-width: 0; padding: 12px 14px; border-right: 1px solid var(--fx-line); }}
    .grid .summary-item:last-child {{ border-right: 0; }}
    .grid h2 {{ margin-bottom: 5px; color: var(--fx-muted); font-size: 12px; font-weight: 600; }}
    .grid .muted {{ font-size: 11px; line-height: 1.35; }}
    .card {{ background: var(--fx-surface); border: 1px solid var(--fx-line); border-radius: 8px; padding: 14px; }}
    .value {{ font-size: 22px; font-weight: 700; color: var(--fx-primary); }}
    button, input[type=file]::file-selector-button {{ min-height: 44px; border: 0; border-radius: 7px; padding: 10px 13px; font-weight: 700; background: var(--fx-primary); color: var(--fx-on-primary); transition: transform .08s ease, filter .12s ease, opacity .12s ease, box-shadow .12s ease; }}
    button:active {{ transform: scale(.98); filter: brightness(.92); }}
    button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {{ outline: 3px solid var(--fx-primary); outline-offset: 2px; }}
    button.just-clicked {{ box-shadow: 0 0 0 3px color-mix(in srgb, var(--fx-primary) 24%, transparent); filter: brightness(.95); }}
    button[disabled] {{ opacity: .58; cursor: wait; }}
    button.secondary {{ background: var(--fx-surface-3); color: var(--fx-text); }}
    button.danger {{ background: var(--fx-danger); color: var(--fx-on-primary); }}
    input, select {{ width: 100%; min-height: 44px; border: 1px solid var(--fx-line-strong); border-radius: 7px; padding: 10px; margin: 6px 0 10px; background: var(--fx-surface); color: var(--fx-text); }}
    pre {{ white-space: pre-wrap; background: var(--fx-ink); color: var(--fx-ink-text); padding: 12px; border-radius: 8px; max-height: 280px; overflow: auto; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .row > button {{ flex: 1; min-width: 130px; }}
    .list {{ display: grid; gap: 8px; margin-top: 10px; }}
    .item {{ border: 1px solid var(--fx-line); border-radius: 7px; padding: 10px; background: var(--fx-surface-2); }}
    .item strong {{ display: block; overflow-wrap: anywhere; }}
    .item .row {{ margin-top: 8px; }}
    .item button {{ padding: 8px 10px; }}
    .workspace-panel {{ background: var(--fx-surface); border-color: var(--fx-line); display: grid; gap: 10px; }}
    .workspace-head {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }}
    .workspace-head strong {{ font-size: 15px; }}
    .workspace-meta {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }}
    .workspace-section {{ border-top: 1px solid var(--fx-line); padding-top: 10px; }}
    .workspace-section-title {{ font-weight: 700; margin-bottom: 8px; }}
    .workspace-card {{ border: 1px solid var(--fx-line); border-radius: 8px; padding: 10px; background: var(--fx-surface); }}
    .workspace-card > summary {{ cursor: pointer; list-style-position: inside; }}
    .workspace-card[open] > summary {{ margin-bottom: 8px; }}
    .device-card {{ border: 1px solid var(--fx-line); border-radius: 7px; padding: 10px; margin-top: 8px; background: var(--fx-surface-2); }}
    .device-card > span {{ display: block; color: var(--fx-muted); font-size: 13px; margin-top: 2px; }}
    .device-actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }}
    .device-actions > button {{ flex: 1; min-width: 120px; }}
    .workflow-add {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 10px; align-items: center; }}
    .workflow-add select {{ margin: 0; }}
    .workflow-list-head {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-top: 10px; font-weight: 700; }}
    .workflow-control {{ margin-top: 9px; border-top: 1px solid var(--fx-line); padding-top: 9px; }}
    .workflow-control span {{ display: block; color: var(--fx-muted); font-size: 13px; }}
    .workflow-title {{ display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; flex-wrap: wrap; }}
    .workflow-control .row > button {{ min-width: 108px; }}
    .image-file {{ background: var(--fx-surface-2); border-color: var(--fx-line); display: grid; gap: 6px; }}
    .image-file strong {{ font-size: 14px; }}
    .image-file .row > button {{ min-width: 92px; }}
    .inline-preview {{ margin-top: 10px; }}
    .history-panel {{ margin-top: 10px; border: 1px solid var(--fx-line); border-radius: 7px; padding: 10px; background: var(--fx-surface-2); }}
    .history-panel > summary {{ cursor: pointer; font-weight: 700; }}
    .management-panel {{ margin-top: 10px; border: 1px solid var(--fx-line); border-radius: 7px; padding: 10px; background: var(--fx-surface); }}
    .management-panel > summary {{ cursor: pointer; font-weight: 700; }}
    .management-summary {{ color: var(--fx-muted); font-size: 13px; margin-top: 4px; }}
    .subsection {{ margin-top: 14px; border-top: 1px solid var(--fx-line); padding-top: 12px; }}
    .subsection h3 {{ margin: 0 0 8px; font-size: 14px; }}
    .inline-fields {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: center; }}
    .inline-fields .muted {{ margin: 0; white-space: nowrap; }}
    .run-section {{ margin-top: 12px; }}
    .run-section-title {{ font-weight: 700; margin-bottom: 8px; }}
    details.advanced {{ margin-top: 12px; }}
    details.advanced > summary {{ cursor: pointer; min-height: 44px; display: flex; align-items: center; font-weight: 700; color: var(--fx-text); }}
    .copy-line {{ overflow-wrap: anywhere; background: var(--fx-info-bg); border-radius: 7px; padding: 10px; margin: 8px 0; font-size: 13px; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px; background: var(--fx-surface-3); color: var(--fx-text); font-size: 12px; font-weight: 700; }}
    .pairing-box {{ display: none; margin-top: 10px; }}
    .pairing-box img {{ width: min(220px, 100%); height: auto; display: block; margin: 8px auto; }}
    .pairing-token {{ font-weight: 700; color: var(--fx-primary); overflow-wrap: anywhere; }}
    .preview {{ margin-top: 10px; border: 1px solid var(--fx-line); border-radius: 7px; padding: 10px; background: var(--fx-surface-2); }}
    .preview img {{ display: block; width: 100%; max-height: 360px; object-fit: contain; background: var(--fx-info-bg); border-radius: 6px; }}
    .status-line {{ min-height: 18px; margin-top: 8px; color: var(--fx-muted); font-size: 13px; }}
    .status-line.busy {{ color: var(--fx-primary); font-weight: 700; }}
    .status-line.success {{ color: var(--fx-success); font-weight: 700; }}
    .status-line.error {{ color: var(--fx-danger); font-weight: 700; }}
    .auth-status {{ display: none; margin-top: 8px; border-radius: 7px; padding: 10px; font-size: 13px; }}
    .auth-status.warning {{ display: block; background: var(--fx-warning-bg); color: var(--fx-warning); border: 1px solid var(--fx-warning); }}
    .auth-status.ok {{ display: block; background: var(--fx-success-bg); color: var(--fx-success); border: 1px solid var(--fx-success); }}
    @media (max-width: 620px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid .summary-item:nth-child(2) {{ border-right: 0; }}
      .grid .summary-item:nth-child(-n+2) {{ border-bottom: 1px solid var(--fx-line); }}
    }}
    @media (prefers-reduced-motion: reduce) {{ button {{ transition: none; }} }}
  </style>
</head>
<body>
<main>
  <header class="controller-header">
    <div><h1>SpiritKin 电商运营 Terminal</h1><div class="muted">领域：电商 · 商品素材、发布预检、Android 上架、配对与跨端调度。</div></div>
    <span class="role-badge">iOS CONTROL</span>
  </header>
  <section class="avatar-stage" aria-label="SpiritKin 3D Avatar 主控舞台">
    <iframe id="avatarFrame" title="SpiritKin 3D Avatar" allow="autoplay" loading="eager"></iframe>
  </section>
  <section class="grid">
    <div class="summary-item"><h2>Android Bridge</h2><div id="summaryAndroid" class="value">{android.get("device_count", 0)}</div><div class="muted">在线执行端</div></div>
    <div class="summary-item"><h2>远程执行端</h2><div id="summaryWorkers" class="value">{workers.get("count", 0)}</div><div class="muted">本地或云端节点</div></div>
    <div class="summary-item"><h2>工作流运行</h2><div id="summaryRuns" class="value">{runs.get("count", 0)}</div><div class="muted">当前与历史运行</div></div>
    <div class="summary-item"><h2>工作素材</h2><div id="summaryArtifacts" class="value">{artifact_count}</div><div class="muted">图片、截图与文件</div></div>
  </section>
  <section id="controller-management" class="card">
    <h2>主控端管理</h2>
    <div class="muted">这里只管理主控端本身和主控侧工具。Android 手机、配对、工作流、诊断和商品图片组都在下面的工作区卡片里处理。</div>
    <details id="management-auth" class="advanced" open>
      <summary>控制权限</summary>
      <input id="managementToken" type="password" placeholder="主控访问令牌">
      <div class="row">
        <button onclick="uiAction(event,'保存访问令牌', saveManagementToken)">保存访问令牌</button>
        <button class="secondary" onclick="uiAction(event,'清除访问令牌', clearManagementToken)">清除访问令牌</button>
        <button class="secondary" onclick="uiAction(event,'生成本机查看令牌', createIosTerminalToken)">生成本机查看令牌</button>
      </div>
      <div id="authStatus" class="auth-status"></div>
      <div class="muted">设备工作流、配对记录删除和手机操控需要云端管理令牌；本机查看令牌只适合查看当前工作区状态。</div>
    </details>
    <details id="ios-controller-management" class="advanced" open>
      <summary>当前主控端</summary>
      <div class="muted">管理正在使用或历史登录过的 iOS/网页主控端；同一台手机或浏览器的旧记录可以在这里清理。</div>
      <div id="iosControllers" class="list"></div>
    </details>
    <details id="runtime-profile" class="advanced">
      <summary>主控端高级设置：远程执行环境</summary>
      <div class="muted">这是主控端给远程执行端分配任务时使用的运行环境。只验证 Android 手机端时可以先不填。</div>
      <input id="runtimeVenv" placeholder="虚拟环境路径，例如 state/workspaces/local-ecommerce/.venv">
      <select id="dependencyPolicy">
        <option value="project_local_only">只允许项目内依赖</option>
        <option value="locked">锁定依赖</option>
        <option value="container_only">只允许容器执行</option>
      </select>
      <input id="allowedCommands" placeholder="允许本地命令，逗号分隔，例如 python,node">
      <button class="secondary" onclick="uiAction(event,'保存执行环境', updateRuntimeProfile)">保存执行环境</button>
    </details>
    <details id="artifacts-upload" class="advanced">
      <summary>主控端素材上传</summary>
      <div class="muted">这是从当前主控浏览器上传商品图片到云端素材库；Android 手机相册分享上传会显示在对应工作区的商品图片组里。</div>
      <input id="files" type="file" accept="image/*" multiple>
      <div class="row">
        <button onclick="uiAction(event,'上传图片', uploadFiles)">上传图片</button>
        <button class="secondary" onclick="uiAction(event,'清理过期图片', cleanupArtifacts)">清理过期图片</button>
        <button class="secondary" onclick="uiAction(event,'清理状态', cleanupState)">清理状态</button>
        <button class="secondary" onclick="uiAction(event,'检查状态', validateState)">检查状态</button>
      </div>
    </details>
    <details id="android-command" class="advanced">
      <summary>主控端高级操作：手动下发 Android 步骤</summary>
      <div class="muted">这些是排查和验收用的单步命令。正式工作流应优先在工作区设备卡片里按设备、按工作流管理。</div>
    <select id="targetDevice">
      <option value="*">当前工作区全部 Android 手机</option>
    </select>
    <select id="operation">
      <option value="app.launch">启动应用</option>
      <option value="url.open">打开 URL</option>
      <option value="clipboard.write">写入剪贴板</option>
      <option value="artifact.download">下载选中图片</option>
      <option value="image.share_to_app">分享选中图片</option>
      <option value="artifact.cache.cleanup">清理 Android 图片缓存</option>
      <option value="artifact.cache.status">查看 Android 图片缓存</option>
      <option value="android.ui_snapshot">上传页面快照</option>
      <option value="android.screenshot.request_permission">请求屏幕截图授权</option>
      <option value="android.screenshot.capture">上传屏幕截图</option>
      <option value="pdd.launch">启动 PDD</option>
      <option value="pdd.share_image">PDD 分享图片</option>
      <option value="pdd.create_listing">PDD 自动上架</option>
    </select>
    <div id="commandMetadata" class="item muted">选择手机步骤查看权限和风险</div>
    <input id="param" placeholder="应用名 / URL / 剪贴板文本 / 当前图片组">
    <input id="artifactFileIndex" type="number" min="0" value="0" placeholder="图片序号，默认第 1 张">
    <input id="targetPackage" placeholder="目标包名，可空，例如 com.xunmeng.pinduoduo">
    <input id="title" placeholder="PDD 标题">
    <input id="price" placeholder="PDD 价格">
    <input id="description" placeholder="PDD 描述">
    <label class="muted"><input id="allowSubmit" type="checkbox" style="width:auto"> 允许点击发布/提交</label>
    <button onclick="uiAction(event,'下发手机步骤', queueAndroidCommand)">下发手机步骤</button>
    </details>
    <details id="status-output" class="advanced" open>
      <summary>主控端状态</summary>
      <div class="muted">显示本页最近一次刷新、操作结果和服务器返回内容；用于确认按钮是否下发成功、失败原因是什么。</div>
      <div id="actionStatus" class="status-line">就绪</div>
      <pre id="output">Loading...</pre>
    </details>
  </section>
  <section id="workspace-devices" class="card">
    <h2>工作区设备管理</h2>
    <div class="muted">每个工作区独立折叠管理。Android 手机端、配对请求、配对码、远程执行端、工作流、诊断和商品图片都归到对应工作区下面。</div>
    <div id="workspaceDevices" class="list"></div>
    <input id="workspace" type="hidden" value="{DEFAULT_WORKSPACE_ID}">
  </section>
  <section id="account-console" class="card">
    <h2>我的账户</h2>
    <div class="muted">账户自助视图只显示当前账户下的工作区、执行端、素材和配额用量。账户令牌不能执行主控管理动作，也不能访问其他账户。</div>
    <div id="accountConsole" class="list"></div>
  </section>
</main>
<script>
const initialSnapshot = {initial_snapshot};
const configuredAvatarURL = {json.dumps((os.getenv("SPIRITKIN_IOS_AVATAR_URL") or "").strip(), ensure_ascii=False)};
function resolveAvatarURL() {{
  if (configuredAvatarURL) return configuredAvatarURL;
  const target = new URL('/avatar_3d.html', window.location.href);
  if (target.port === '8791' || target.port === '8792') target.port = '8787';
  target.searchParams.set('embed', '1');
  target.searchParams.set('float', '1');
  return target.toString();
}}
document.getElementById('avatarFrame').src = resolveAvatarURL();
let currentSnapshot = initialSnapshot;
let refreshInFlight = false;
let lastManualRefreshAt = 0;
const DETAIL_STATE_STORAGE_KEY = 'spiritkin_control_detail_state_v1';
let detailOpenState = {{}};
try {{
  detailOpenState = JSON.parse(localStorage.getItem(DETAIL_STATE_STORAGE_KEY) || '{{}}') || {{}};
}} catch (err) {{
  detailOpenState = {{}};
}}
function setActionStatus(message, kind) {{
  const box = document.getElementById('actionStatus');
  if (!box) return;
  box.textContent = message || '就绪';
  box.className = 'status-line' + (kind ? ' ' + kind : '');
}}
function setAuthStatus(message, kind) {{
  const box = document.getElementById('authStatus');
  if (!box) return;
  box.textContent = message || '';
  box.className = 'auth-status' + (message ? ' ' + (kind || 'warning') : '');
}}
function showOutput(value) {{
  const box = document.getElementById('output');
  if (!box) return;
  box.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}}
function detailStateKey(details) {{
  if (!details) return '';
  if (details.dataset && details.dataset.detailKey) return 'key:' + details.dataset.detailKey;
  if (details.id) return 'id:' + details.id;
  const summary = details.querySelector(':scope > summary');
  const summaryText = summary ? summary.textContent.replace(/\\s+/g, ' ').trim() : '';
  const parent = details.parentElement && details.parentElement.closest ? details.parentElement.closest('[id]') : null;
  const parentId = parent && parent.id ? parent.id : 'page';
  const siblingIndex = Array.from((parent || document).querySelectorAll('details')).indexOf(details);
  return 'auto:' + parentId + ':' + summaryText + ':' + siblingIndex;
}}
function saveDetailState() {{
  try {{
    localStorage.setItem(DETAIL_STATE_STORAGE_KEY, JSON.stringify(detailOpenState));
  }} catch (err) {{}}
}}
function rememberDetailState(details) {{
  const key = detailStateKey(details);
  if (!key) return;
  detailOpenState[key] = !!details.open;
  saveDetailState();
}}
function applyDetailState(root) {{
  const scope = root || document;
  scope.querySelectorAll('details').forEach(function(details) {{
    const key = detailStateKey(details);
    if (key && Object.prototype.hasOwnProperty.call(detailOpenState, key)) {{
      details.open = !!detailOpenState[key];
    }}
  }});
}}
function friendlyError(message) {{
  const text = String(message || '操作失败');
  if (text.includes('pairing token is not pending')) return '配对码不可用：这个配对码已经用过、过期或被取消。请在主控端重新生成 Android 配对码，再到手机端绑定。';
  if (text.includes('management token required') || text.includes('iOS terminal token') || text.includes('invalid management token') || text.includes('forbidden') || text.includes('unauthorized') || text.includes('401') || text.includes('403')) return '没有控制权限：请粘贴云端 .env.cloud 里的 SPIRITKIN_MANAGEMENT_TOKEN，保存后再操作。';
  if (text.includes('Failed to fetch')) return '请求没有到达服务器：检查网络、域名 HTTPS、云端服务是否在线。';
  return text;
}}
async function runAction(label, fn) {{
  setActionStatus(label ? label + '...' : '处理中...', 'busy');
  try {{
    const result = await fn();
    setActionStatus(label ? label + '完成' : '已完成', 'success');
    return result;
  }} catch (err) {{
    const message = friendlyError(err && err.message ? err.message : err);
    setActionStatus(message, 'error');
    showOutput({{ok: false, error: message}});
    return null;
  }}
}}
async function uiAction(event, label, fn) {{
  const button = event && event.currentTarget ? event.currentTarget : null;
  if (button) {{
    button.classList.add('just-clicked');
    setBusyButton(button, true, label ? label + '...' : '处理中...');
  }}
  try {{
    return await runAction(label, fn);
  }} finally {{
    if (button) {{
      setBusyButton(button, false);
      window.setTimeout(() => button.classList.remove('just-clicked'), 180);
    }}
  }}
}}
function setBusyButton(button, busy, label) {{
  if (!button) return;
  if (busy) {{
    button.dataset.originalText = button.textContent || '';
    button.textContent = label || '处理中...';
    button.disabled = true;
  }} else {{
    button.disabled = false;
    if (button.dataset.originalText) button.textContent = button.dataset.originalText;
    delete button.dataset.originalText;
  }}
}}
function managementToken() {{
  return document.getElementById('managementToken').value || localStorage.getItem('spiritkin_control_token') || localStorage.getItem('spiritkin_management_token') || '';
}}
function requireControlToken(actionName) {{
  const token = managementToken().trim();
  if (token) return true;
  const message = '需要先填写并保存主控访问令牌，才能' + (actionName || '执行这个操作') + '。';
  setAuthStatus(message, 'warning');
  setActionStatus(message, 'error');
  const input = document.getElementById('managementToken');
  if (input) {{
    input.focus();
    input.scrollIntoView({{behavior: 'smooth', block: 'center'}});
  }}
  return false;
}}
function requestHeaders(jsonBody) {{
  const headers = jsonBody ? {{'content-type': 'application/json'}} : {{}};
  const token = managementToken();
  if (token) headers.authorization = 'Bearer ' + token;
  return headers;
}}
function escapeHtml(value) {{
  return String(value ?? '').replace(/[&<>"']/g, function(ch) {{
    return {{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}}[ch];
  }});
}}
function jsArg(value) {{
  return escapeHtml(JSON.stringify(value ?? ''));
}}
function escapeJs(value) {{
  return String(value ?? '').replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'").replace(/\\n/g, '\\\\n').replace(/\\r/g, '');
}}
async function postJson(url, payload) {{
  if (url.startsWith('/ios/control/') && !requireControlToken(payload && payload.action ? payload.action : '执行主控操作')) {{
    throw new Error('management token required');
  }}
  setActionStatus('请求中...', 'busy');
  let res;
  try {{
    res = await fetch(url, {{method: 'POST', headers: requestHeaders(true), body: JSON.stringify(payload)}});
  }} catch (err) {{
    throw new Error('网络请求失败: ' + (err && err.message ? err.message : err));
  }}
  const text = await res.text();
  let data;
  try {{
    data = text ? JSON.parse(text) : {{}};
  }} catch (err) {{
    data = {{ok: false, raw: text}};
  }}
  showOutput(data);
  if (!res.ok) {{
    const message = friendlyError(data.error || data.detail || data.message || text || ('HTTP ' + res.status));
    if (res.status === 401 || res.status === 403) {{
      setAuthStatus(message, 'warning');
      const input = document.getElementById('managementToken');
      if (input) input.focus();
    }}
    throw new Error(message);
  }}
  setActionStatus('已完成', 'success');
  return data;
}}
async function getJson(url) {{
  return getJsonWithOptions(url, {{silent: false}});
}}
async function getJsonWithOptions(url, options) {{
  const silent = !!(options && options.silent);
  if (!silent) setActionStatus('刷新中...', 'busy');
  let res;
  try {{
    res = await fetch(url, {{headers: requestHeaders(false)}});
  }} catch (err) {{
    throw new Error('网络请求失败: ' + (err && err.message ? err.message : err));
  }}
  const text = await res.text();
  let data;
  try {{
    data = text ? JSON.parse(text) : {{}};
  }} catch (err) {{
    data = {{ok: false, raw: text}};
  }}
  if (!silent) showOutput(data);
  if (!res.ok) {{
    const message = friendlyError(data.error || data.detail || data.message || text || ('HTTP ' + res.status));
    if (res.status === 401 || res.status === 403) {{
      setAuthStatus(message, 'warning');
      const input = document.getElementById('managementToken');
      if (input) input.focus();
    }}
    throw new Error(message);
  }}
  if (!silent) setActionStatus('已刷新', 'success');
  return data;
}}
async function refresh(options) {{
  const silent = !!(options && options.silent);
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {{
    const data = await getJsonWithOptions('/ios/control/snapshot?terminal_id=' + encodeURIComponent(terminalId()), {{silent}});
    renderSnapshot(data);
  }} catch (err) {{
    if (!silent) renderSnapshot(initialSnapshot);
    const message = friendlyError(err && err.message ? err.message : err);
    if (!silent) {{
      setActionStatus('刷新失败：' + message, 'error');
      showOutput({{ok: false, error: message}});
    }}
  }} finally {{
    refreshInFlight = false;
  }}
}}
function workspace() {{ return document.getElementById('workspace').value || '{DEFAULT_WORKSPACE_ID}'; }}
function baseUrl() {{ return window.location.origin; }}
function apkUrl() {{ return baseUrl() + '/android/apk'; }}
function pairingDomId(prefix, workspaceId) {{
  return prefix + '_' + String(workspaceId || workspace()).replace(/[^a-zA-Z0-9_-]/g, '_');
}}
function pairingExpiryInputId(workspaceId) {{ return pairingDomId('pairingExpiresAt', workspaceId); }}
function pairingHintId(workspaceId) {{ return pairingDomId('pairingTtlHint', workspaceId); }}
function pairingBoxId(workspaceId) {{ return pairingDomId('pairingBox', workspaceId); }}
function initPairingExpiryInput(workspaceId) {{
  const input = document.getElementById(workspaceId ? pairingExpiryInputId(workspaceId) : 'pairingExpiresAt');
  if (!input || input.value) return;
  const expiry = new Date(Date.now() + 30 * 60 * 1000);
  const local = new Date(expiry.getTime() - expiry.getTimezoneOffset() * 60000);
  input.value = local.toISOString().slice(0, 16);
  updatePairingTtlHint(workspaceId);
  input.addEventListener('input', () => updatePairingTtlHint(workspaceId));
}}
function initWorkspacePairingInputs() {{
  const items = (((currentSnapshot.workspace_devices || {{}}).items) || []);
  for (const item of items) initPairingExpiryInput(item.workspace_id || workspace());
  initPairingExpiryInput();
}}
function pairingTtlMinutes(workspaceId) {{
  const input = document.getElementById(workspaceId ? pairingExpiryInputId(workspaceId) : 'pairingExpiresAt');
  if (!input || !input.value) return '30';
  const selected = new Date(input.value);
  const diffMinutes = Math.ceil((selected.getTime() - Date.now()) / 60000);
  return String(Math.max(5, Math.min(43200, diffMinutes || 30)));
}}
function updatePairingTtlHint(workspaceId) {{
  const hint = document.getElementById(workspaceId ? pairingHintId(workspaceId) : 'pairingTtlHint');
  if (!hint) return;
  const minutes = Number(pairingTtlMinutes(workspaceId));
  if (minutes >= 1440) {{
    hint.textContent = '约 ' + Math.round(minutes / 1440) + ' 天';
  }} else if (minutes >= 60) {{
    hint.textContent = '约 ' + Math.round(minutes / 60) + ' 小时';
  }} else {{
    hint.textContent = '约 ' + minutes + ' 分钟';
  }}
}}
function commandCatalog() {{
  const items = Array.isArray(currentSnapshot.android_command_catalog) ? currentSnapshot.android_command_catalog : [];
  const result = {{}};
  for (const item of items) result[item.operation] = item;
  return result;
}}
function saveManagementToken() {{
  const token = document.getElementById('managementToken').value.trim();
  if (token) {{
    localStorage.setItem('spiritkin_control_token', token);
    localStorage.removeItem('spiritkin_management_token');
    setAuthStatus('已保存主控访问令牌，正在刷新。', 'ok');
  }} else {{
    setAuthStatus('请输入云端 .env.cloud 里的 SPIRITKIN_MANAGEMENT_TOKEN。', 'warning');
  }}
  refresh();
}}
function clearManagementToken() {{
  localStorage.removeItem('spiritkin_control_token');
  localStorage.removeItem('spiritkin_management_token');
  document.getElementById('managementToken').value = '';
  setAuthStatus('已清除本浏览器保存的访问令牌。', 'warning');
  refresh();
}}
function pairingQuery(workspaceId) {{
  const targetWorkspace = workspaceId || workspace();
  return new URLSearchParams({{workspace_id: targetWorkspace, device_role: 'android_bridge', requested_by: 'ios_terminal', ttl_minutes: pairingTtlMinutes(targetWorkspace), format: 'json'}});
}}
function workerPairingQuery(workspaceId) {{
  const targetWorkspace = workspaceId || workspace();
  return new URLSearchParams({{workspace_id: targetWorkspace, device_role: 'remote_worker', requested_by: 'ios_terminal', ttl_minutes: pairingTtlMinutes(targetWorkspace), format: 'json'}});
}}
function browserExtensionPairingQuery(workspaceId) {{
  const targetWorkspace = workspaceId || workspace();
  return new URLSearchParams({{workspace_id: targetWorkspace, device_role: 'browser_extension', requested_by: 'ios_terminal', ttl_minutes: pairingTtlMinutes(targetWorkspace), format: 'json'}});
}}
function terminalId() {{
  let id = localStorage.getItem('spiritkin_terminal_id') || '';
  if (!id) {{
    id = 'ios-web-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('spiritkin_terminal_id', id);
  }}
  return id;
}}
function iosTerminalPairingQuery(workspaceId) {{
  const targetWorkspace = workspaceId || workspace();
  return new URLSearchParams({{workspace_id: targetWorkspace, device_role: 'ios_terminal', requested_by: 'ios_terminal', ttl_minutes: pairingTtlMinutes(targetWorkspace), format: 'json'}});
}}
async function createPairingData(workspaceId) {{
  const query = pairingQuery(workspaceId);
  return await getJson('/ios/control/pairing?' + query.toString());
}}
async function createIosTerminalToken() {{
  const data = await getJson('/ios/control/pairing?' + iosTerminalPairingQuery().toString());
  const pairing = data.pairing || {{}};
  renderPairing(pairing);
  const bound = await postJson('/ios/control/pair', {{
    pairing_token: pairing.pairing_token,
    terminal_id: terminalId(),
    device_state: {{user_agent: navigator.userAgent}}
  }});
  const token = (bound.binding && bound.binding.token) || pairing.pairing_token || '';
  if (token) {{
    localStorage.setItem('spiritkin_control_token', token);
    localStorage.removeItem('spiritkin_management_token');
    document.getElementById('managementToken').value = token;
  }}
  await refresh();
  return bound.binding || {{}};
}}
async function createWorkerPairingForWorkspace(workspaceId) {{
  const data = await getJson('/ios/control/pairing?' + workerPairingQuery(workspaceId).toString());
  const pairing = data.pairing || {{}};
  renderPairing(pairing, workspaceId);
  await refresh();
  return pairing;
}}
async function createWorkerPairing() {{ return createWorkerPairingForWorkspace(workspace()); }}
async function createBrowserExtensionPairingForWorkspace(workspaceId) {{
  const data = await getJson('/ios/control/pairing?' + browserExtensionPairingQuery(workspaceId).toString());
  const pairing = data.pairing || {{}};
  renderPairing(pairing, workspaceId);
  await refresh();
  return pairing;
}}
async function createPairingForWorkspace(workspaceId) {{
  const data = await createPairingData(workspaceId);
  const pairing = data.pairing || {{}};
  renderPairing(pairing, workspaceId);
  await refresh();
  return pairing;
}}
async function createPairing() {{ return createPairingForWorkspace(workspace()); }}
function pairingStandaloneHtml(pairing) {{
  const qr = pairing.qr_png_data_url ? `<img class="qr" src="${{escapeHtml(pairing.qr_png_data_url)}}" alt="Pairing QR">` : '<div class="empty">二维码不可用</div>';
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SpiritKin Android Pairing</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f3f6fb; color: #111827; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    .card {{ background: white; border: 1px solid #dbe5f3; border-radius: 8px; padding: 16px; margin: 12px 0; }}
    .label {{ color: #64748b; font-size: 12px; margin-top: 12px; }}
    .value {{ overflow-wrap: anywhere; background: #eef4ff; border-radius: 6px; padding: 10px; margin-top: 5px; }}
    .token {{ font-size: 19px; font-weight: 700; color: #0757c8; }}
    .qr {{ display: block; width: min(260px, 100%); margin: 10px auto; }}
    a.button {{ display: block; box-sizing: border-box; width: 100%; border-radius: 7px; padding: 12px 14px; margin-top: 10px; background: #0757c8; color: white; text-align: center; text-decoration: none; font-weight: 700; }}
  </style>
</head>
<body>
<main>
  <h1>Android 手机端配对</h1>
  <section class="card">
    ${{qr}}
    <a class="button" href="${{escapeHtml(pairing.deep_link)}}">打开 App 并自动填入配对信息</a>
  </section>
  <section class="card">
    <div class="label">服务器地址</div><div class="value">${{escapeHtml(pairing.server_url)}}</div>
    <div class="label">工作区</div><div class="value">${{escapeHtml(pairing.workspace_id)}}</div>
    <div class="label">配对码</div><div class="value token">${{escapeHtml(pairing.pairing_token)}}</div>
    <div class="label">有效期至</div><div class="value">${{escapeHtml(pairing.expires_at)}}</div>
  </section>
</main>
</body>
</html>`;
}}
async function openPairingPageForWorkspace(workspaceId) {{
  const popup = window.open('', '_blank');
  if (popup) {{
    popup.document.write('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;padding:20px">Loading pairing...</body>');
    popup.document.close();
  }}
  const pairing = await createPairingForWorkspace(workspaceId);
  if (popup) {{
    popup.document.write(pairingStandaloneHtml(pairing));
    popup.document.close();
  }}
}}
async function openPairingPage() {{ return openPairingPageForWorkspace(workspace()); }}
function renderPairing(pairing, workspaceId) {{
  const box = document.getElementById(workspaceId ? pairingBoxId(workspaceId) : 'pairingBox');
  if (!box) return;
  const qr = pairing.qr_png_data_url ? `<img src="${{escapeHtml(pairing.qr_png_data_url)}}" alt="Pairing QR">` : '';
  const command = pairing.pairing_command
    ? `<div class="item"><strong>Worker 启动命令</strong><pre>${{escapeHtml(pairing.pairing_command)}}</pre></div>`
    : '';
  box.innerHTML = `
    ${{qr}}
    <div class="item"><strong>服务器地址</strong><span>${{escapeHtml(pairing.server_url)}}</span></div>
    <div class="item"><strong>工作区</strong><span>${{escapeHtml(pairing.workspace_id)}}</span></div>
    <div class="item"><strong>配对类型</strong><span>${{escapeHtml(roleLabel(pairing.device_role || ''))}}</span></div>
    <div class="item"><strong>配对码</strong><span class="pairing-token">${{escapeHtml(pairing.pairing_token)}}</span><div class="muted">有效期至：${{escapeHtml(pairing.expires_at || '')}}</div></div>
    <div class="item"><strong>一键配对链接</strong><a href="${{escapeHtml(pairing.deep_link)}}">打开 App 并自动填入配对信息</a><div class="muted">手机可识别这个链接，可免手动复制服务器地址和配对码。</div></div>
    ${{command}}
  `;
  box.style.display = 'block';
}}
function roleLabel(role) {{
  if (role === 'android_bridge') return 'Android 手机端';
  if (role === 'ios_terminal') return '主控端';
  if (role === 'remote_worker') return '远程执行端';
  if (role === 'browser_extension') return '浏览器抓取扩展';
  if (role === 'account_console') return '账户控制台';
  return role || '设备';
}}
function statusLabel(status) {{
  if (status === 'pending') return '待使用';
  if (status === 'active') return '已绑定';
  if (status === 'online') return '在线';
  if (status === 'blocked') return '需处理';
  if (status === 'warning') return '提醒';
  if (status === 'ready') return '正常';
  if (status === 'bound') return '已使用';
  if (status === 'cancelled') return '已取消';
  if (status === 'expired') return '已过期';
  if (status === 'revoked') return '已撤销';
  if (status === 'replaced') return '已替换';
  if (status === 'needs_rebind') return '等待恢复绑定';
  if (status === 'enabled') return '已启用';
  if (status === 'paused') return '已暂停';
  return status || '';
}}
function deviceLabel(item) {{
  return item.worker_id || item.device_id || item.terminal_id || item.token_id || '设备';
}}
function renderSnapshot(data) {{
  currentSnapshot = data || {{}};
  const workspaceInput = document.getElementById('workspace');
  if (data.workspace_filter && workspaceInput && workspaceInput.value !== data.workspace_filter) {{
    workspaceInput.value = data.workspace_filter;
  }}
  const android = data.android || {{}};
  const pairings = data.pairings || {{}};
  const artifacts = data.artifacts || {{}};
  const runs = data.workflow_runs || {{}};
  const diagnostics = android.diagnostics || {{}};
  setSummaryValue('summaryAndroid', android.device_count || 0);
  setSummaryValue('summaryWorkers', (data.remote_workers || {{}}).count || 0);
  setSummaryValue('summaryRuns', runs.count || 0);
  setSummaryValue('summaryArtifacts', artifacts.count || 0);
  const devices = Array.isArray(android.devices) ? android.devices : [];
  renderAccountConsole(data.accounts || {{}}, data.workspace_devices || {{}}, artifacts, data.remote_workers || {{}});
  renderWorkspaceDevices(data.workspace_devices || {{}});
  renderIosControllers(data.workspace_devices || {{}}, Array.isArray(data.ios_terminals) ? data.ios_terminals : []);
  renderTargetDevices(devices);
  renderCommandMetadata();
  applyDetailState(document);
}}
function renderInstallBox() {{
  const box = document.getElementById('apkDownloadUrl');
  if (box) box.textContent = apkUrl();
}}
async function copyApkLink() {{
  await navigator.clipboard.writeText(apkUrl());
  setActionStatus('已复制 Android 安装包链接');
}}
function openApkLink() {{
  window.open(apkUrl(), '_blank');
}}
function renderAccountConsole(accounts, overview, artifacts, workers) {{
  const box = document.getElementById('accountConsole');
  if (!box) return;
  const accountItems = Array.isArray(accounts.items) ? accounts.items : [];
  const workspaceItems = Array.isArray(overview.items) ? overview.items : [];
  if (!accountItems.length) {{
    box.innerHTML = '<div class="muted">当前访问令牌没有账户视图，使用 account_console token 后会只显示自己的账户。</div>';
    return;
  }}
  const artifactQuota = artifacts && artifacts.quota && artifacts.quota.workspaces ? artifacts.quota.workspaces : {{}};
  const workerItems = Array.isArray(workers.items) ? workers.items : [];
  const rows = accountItems.map(function(account) {{
    const summary = account.usage_summary || {{}};
    const workspaceIds = Array.isArray(account.workspace_ids) ? account.workspace_ids : [];
    const workspaceRows = workspaceIds.map(function(workspaceId) {{
      const workspace = workspaceItems.find(item => (item.workspace_id || '') === workspaceId) || {{workspace_id: workspaceId, counts: {{}}}};
      const counts = workspace.counts || {{}};
      const quota = artifactQuota[workspaceId] || {{}};
      const workerCount = workerItems.filter(item => (item.workspace_id || workspaceId) === workspaceId).length || counts.remote_workers || 0;
      return `<div class="item"><strong>${{escapeHtml(workspaceId)}} · ${{escapeHtml(workspace.name || '')}}</strong>
        <span>Android ${{escapeHtml(counts.android || 0)}} · 远程执行 ${{escapeHtml(workerCount)}} · 素材 ${{escapeHtml(quota.artifact_count || 0)}} / ${{formatBytes(quota.total_size_bytes || 0)}}</span>
        <div class="muted">${{quotaLine('工作区', summary.workspace_count, summary.max_workspaces)}} · ${{quotaLine('Worker', summary.worker_count, summary.max_workers)}} · ${{quotaLine('抓取', summary.scrapes_this_period, summary.max_scrapes_per_period)}}</div>
        <div class="row">
          <button class="secondary" onclick="uiAction(event,'刷新账户用量', () => loadAccountUsage('${{escapeJs(workspaceId)}}'))">刷新用量</button>
          <button class="secondary" onclick="uiAction(event,'生成自己的 Worker 配对码', () => createWorkerPairingForWorkspace('${{escapeJs(workspaceId)}}'))">绑定自己的 Worker</button>
        </div>
      </div>`;
    }}).join('');
    return `<details class="management-panel" data-detail-key="account:${{escapeHtml(account.account_id || '')}}" open>
      <summary>${{escapeHtml(account.name || account.account_id || '账户')}} · ${{escapeHtml(statusLabel(account.status || 'active'))}}</summary>
      <div class="management-summary">账户 ${{escapeHtml(account.account_id || '')}} · 周期 ${{escapeHtml(formatDate(summary.period_start || ''))}} - ${{escapeHtml(formatDate(summary.period_end || ''))}}</div>
      <div class="workspace-meta">
        <span class="badge">${{quotaLine('工作区', summary.workspace_count, summary.max_workspaces)}}</span>
        <span class="badge">${{quotaLine('Worker', summary.worker_count, summary.max_workers)}}</span>
        <span class="badge">${{quotaLine('抓取', summary.scrapes_this_period, summary.max_scrapes_per_period)}}</span>
      </div>
      <div class="list">${{workspaceRows || '<div class="muted">这个账户还没有工作区。</div>'}}</div>
    </details>`;
  }});
  box.innerHTML = rows.join('');
}}
function quotaLine(label, used, limit) {{
  const usedText = Number(used || 0);
  const limitText = Number(limit || 0);
  return label + ' ' + usedText + '/' + (limitText ? limitText : '不限');
}}
async function loadAccountUsage(workspaceId) {{
  const data = await postJson('/ios/control/action', {{action: 'get_account_usage', workspace_id: workspaceId || workspace()}});
  await refresh({{silent: true}});
  return data.result || data;
}}
function renderWorkspaceDevices(overview) {{
  const rows = [];
  const items = Array.isArray(overview.items) ? overview.items : [];
  for (const item of items) {{
    const counts = item.counts || {{}};
    const android = Array.isArray(item.android_devices) ? item.android_devices : [];
    const workers = Array.isArray(item.remote_workers) ? item.remote_workers : [];
    const workspaceId = item.workspace_id || '工作区';
    const deviceSections = [
      deviceGroupLine('Android 手机端', android, 'android'),
      deviceGroupLine('远程执行端', workers, 'worker'),
      workspacePairingManagementHtml(item),
      workspaceBindingsHtml(item),
      workspaceWorkflowRunsHtml(workspaceId),
      workspaceDiagnosticsHtml(workspaceId),
      workspaceArtifactGroupsHtml(workspaceId)
    ].filter(Boolean).join('');
    rows.push(`<details class="workspace-card workspace-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}" open>
      <summary><div class="workspace-head">
        <div>
          <strong>${{escapeHtml(workspaceId)}} · ${{escapeHtml(item.name || '')}}</strong>
          <div class="muted">最近活动：${{escapeHtml(formatDate(item.last_seen_at))}}</div>
        </div>
        <span class="badge">${{escapeHtml(statusLabel(item.status || 'active'))}}</span>
      </div></summary>
      <div class="workspace-meta">
        <span class="badge">Android ${{escapeHtml(counts.android || 0)}}</span>
        <span class="badge">远程执行 ${{escapeHtml(counts.remote_workers || 0)}}</span>
        <span class="badge">已绑定 ${{escapeHtml(counts.active_bindings || 0)}}</span>
        <span class="badge">待批准 ${{escapeHtml(counts.pairing_requests || 0)}}</span>
        <span class="badge">待用配对码 ${{escapeHtml(counts.pending_pairings || 0)}}</span>
      </div>
      ${{deviceSections || '<div class="muted">这个工作区暂无设备。先生成配对码并让手机端绑定。</div>'}}
    </details>`);
  }}
  const box = document.getElementById('workspaceDevices');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无工作区设备。先生成配对码并让手机端绑定。</div>';
  initWorkspacePairingInputs();
}}
function deviceGroupLine(label, devices, isAndroid) {{
  if (!Array.isArray(devices) || !devices.length) return '';
  const kind = isAndroid === true ? 'android' : isAndroid === false ? 'plain' : isAndroid;
  const visible = devices.slice(0, 6);
  const parts = visible.map(function(item) {{
    const id = item.device_id || '设备';
    const status = statusLabel(item.status || '');
    const foreground = item.foreground_package ? ' · 前台 ' + item.foreground_package : '';
    const lastSeen = item.last_seen_at ? ' · ' + formatDate(item.last_seen_at) : '';
    const workflowManagement = kind === 'android' ? workflowManagementHtml(item) : '';
    const deviceNeedsAttention = kind === 'android' && (item.status === 'blocked' || item.status === 'warning');
    const openAttr = deviceNeedsAttention ? ' open' : '';
    const androidActions = kind === 'android' ? `<div class="device-actions">
      <button class="secondary" onclick="uiAction(event,'检查这台手机', () => repairDeviceWorkflow('${{escapeJs(item.workspace_id || workspace())}}','${{escapeJs(id)}}','ecommerce.auto_listing.v1','status'))">检查这台手机</button>
      <button class="secondary" onclick="uiAction(event,'清这台队列', () => clearAndroidCommandsForDevice('${{escapeJs(item.workspace_id || workspace())}}','${{escapeJs(id)}}'))">清这台队列</button>
    </div>` : '';
    const deviceInner = `<strong>${{escapeHtml(id)}}</strong><span>${{escapeHtml(status)}}${{escapeHtml(foreground)}}${{escapeHtml(lastSeen)}}</span>${{workflowManagement}}${{androidActions}}`;
    if (kind === 'android') {{
      return `<details class="device-card management-panel" data-detail-key="android-device:${{escapeHtml(item.workspace_id || workspace())}}:${{escapeHtml(id)}}"${{openAttr}}><summary>${{escapeHtml(id)}} · ${{escapeHtml(status)}} · 工作流 ${{escapeHtml((item.workflow_controls || []).length || 0)}}</summary><div class="management-summary">${{escapeHtml(foreground || 'Android 手机端')}}${{escapeHtml(lastSeen)}}</div>${{workflowManagement}}${{androidActions}}</details>`;
    }}
    return `<div class="device-card">${{deviceInner}}</div>`;
  }}).join('');
  const hidden = devices.slice(6);
  const hiddenRows = hidden.map(function(item) {{
    const id = item.device_id || '设备';
    const lastSeen = item.last_seen_at ? ' · ' + formatDate(item.last_seen_at) : '';
    return `<div class="device-card"><strong>${{escapeHtml(id)}}</strong><span>${{escapeHtml(statusLabel(item.status || ''))}}${{escapeHtml(lastSeen)}}</span></div>`;
  }}).join('');
  const groupWorkspace = (devices[0] && devices[0].workspace_id) || workspace();
  const history = hiddenRows ? `<details class="history-panel" data-detail-key="device-history:${{escapeHtml(groupWorkspace)}}:${{escapeHtml(label)}}"><summary>更多${{escapeHtml(label)}}（${{hidden.length}}）</summary><div class="list">${{hiddenRows}}</div></details>` : '';
  return `<div class="workspace-section"><div class="workspace-section-title">${{escapeHtml(label)}}（${{devices.length}}）</div>${{parts}}${{history}}</div>`;
}}
function workspacePairingManagementHtml(item) {{
  const workspaceId = item.workspace_id || workspace();
  const requests = Array.isArray(item.pairing_requests) ? item.pairing_requests : [];
  const pending = Array.isArray(item.pending_pairings) ? item.pending_pairings : [];
  const requestRows = pairingRequestRows(requests, workspaceId);
  const pendingRows = pendingPairingRows(pending, workspaceId);
  return `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:pairing" ${{requests.length || pending.length ? 'open' : ''}}><summary>配对与安装（待批准 ${{requests.length}} / 待用码 ${{pending.length}}）</summary>
    <div class="management-summary">手机端请求绑定后先出现在这里。主控批准并设置有效期后，手机端才会收到配对码并绑定到这个工作区。</div>
    <div class="subsection">
      <h3>安装包发送</h3>
      <div class="copy-line">${{escapeHtml(apkUrl())}}</div>
      <div class="row">
        <button class="secondary" onclick="uiAction(event,'复制安装包链接', () => copyApkLink())">复制安装包链接</button>
        <button class="secondary" onclick="uiAction(event,'打开下载页', () => openApkLink())">打开下载页</button>
      </div>
    </div>
    <div class="subsection">
      <h3>配对有效期</h3>
      <div class="inline-fields">
        <input id="${{pairingExpiryInputId(workspaceId)}}" type="datetime-local" aria-label="pairing token expires at">
        <div id="${{pairingHintId(workspaceId)}}" class="muted">默认 30 分钟</div>
      </div>
      <div class="row">
        <button onclick="uiAction(event,'生成 Android 配对码', () => createPairingForWorkspace('${{escapeJs(workspaceId)}}'))">生成 Android 配对码</button>
        <button class="secondary" onclick="uiAction(event,'打开二维码/一键配网页', () => openPairingPageForWorkspace('${{escapeJs(workspaceId)}}'))">打开二维码/一键配网页</button>
        <button class="secondary" onclick="uiAction(event,'生成执行端配对码', () => createWorkerPairingForWorkspace('${{escapeJs(workspaceId)}}'))">生成执行端配对码</button>
        <button class="secondary" onclick="uiAction(event,'生成浏览器扩展配对码', () => createBrowserExtensionPairingForWorkspace('${{escapeJs(workspaceId)}}'))">生成抓取扩展配对码</button>
      </div>
      <div id="${{pairingBoxId(workspaceId)}}" class="pairing-box"></div>
    </div>
    <div class="subsection">
      <h3>待批准请求</h3>
      <div class="list">${{requestRows || '<div class="muted">暂无手机请求。Android 端点击“请求绑定”后会显示在这里。</div>'}}</div>
    </div>
    <div class="subsection">
      <h3>待使用配对码</h3>
      <div class="list">${{pendingRows || '<div class="muted">暂无待使用配对码。</div>'}}</div>
    </div>
    <details class="history-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:pairing-history">
      <summary>历史配对记录</summary>
      <div class="row" style="margin-top:8px"><button class="danger" onclick="uiAction(event,'清理历史配对记录', () => clearPairingHistoryForWorkspace('${{escapeJs(workspaceId)}}'))">清理这个工作区的历史配对记录</button></div>
      <div class="management-summary">历史明细在下方状态输出或 action log 中查看；这里保留清理入口，避免列表越来越长。</div>
    </details>
  </details>`;
}}
function pairingRequestRows(items, workspaceId) {{
  return (Array.isArray(items) ? items : []).map(function(item) {{
    const role = roleLabel(item.device_role || item.role || '');
    const requestId = item.request_id || item.token_id || '';
    const device = item.device_id ? ` · ${{item.device_id}}` : '';
    return `<div class="item"><strong>待批准 ${{escapeHtml(role)}}${{escapeHtml(device)}}</strong><span>${{escapeHtml(workspaceId)}} · 手机已请求绑定</span><div class="muted">请求时间：${{escapeHtml(formatDate(item.created_at))}}</div><div class="row"><button onclick="uiAction(event,'批准绑定请求', () => approvePairingRequestForWorkspace('${{escapeJs(workspaceId)}}', ${{jsArg(requestId)}}))">批准并下发配对码</button><button class="danger" onclick="uiAction(event,'拒绝绑定请求', () => rejectPairingRequestForWorkspace('${{escapeJs(workspaceId)}}', ${{jsArg(requestId)}}))">拒绝请求</button></div></div>`;
  }}).join('');
}}
function pendingPairingRows(items, workspaceId) {{
  return (Array.isArray(items) ? items : []).map(function(item) {{
    const role = roleLabel(item.device_role || item.role || '');
    return `<div class="item"><strong>${{escapeHtml(role)}}配对码 ${{escapeHtml(shortId(item.token_id || ''))}}</strong><span>${{escapeHtml(workspaceId)}} · ${{escapeHtml(statusLabel(item.status || ''))}}</span><div class="muted">有效期至：${{escapeHtml(formatDate(item.expires_at))}}</div><button class="danger" onclick="uiAction(event,'取消配对码', () => cancelPairingTokenForWorkspace('${{escapeJs(workspaceId)}}', ${{jsArg(item.token_id || '')}}))">取消配对码</button></div>`;
  }}).join('');
}}
function workspaceBindingsHtml(item) {{
  const workspaceId = item.workspace_id || workspace();
  const bindings = Array.isArray(item.active_bindings) ? item.active_bindings : [];
  const rows = bindings.map(function(binding) {{
    const label = deviceLabel(binding);
    return `<div class="item"><strong>${{escapeHtml(label)}}</strong><span>${{escapeHtml(workspaceId)}} · ${{escapeHtml(roleLabel(binding.device_role || binding.role || ''))}} · 已绑定</span><div class="muted">最近使用：${{escapeHtml(formatDate(binding.last_seen_at || binding.bound_at))}}</div><button class="danger" onclick="uiAction(event,'撤销绑定', () => revokeDeviceBindingForWorkspace('${{escapeJs(workspaceId)}}', ${{jsArg(binding.token_id || '')}}, ${{jsArg(label)}}))">撤销绑定</button></div>`;
  }}).join('');
  return `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:bindings"><summary>有效绑定（${{bindings.length}}）</summary>${{rows || '<div class="muted">暂无有效绑定。手机重装或清数据后，请在手机端请求恢复绑定，主控批准后会沿用原 token 和有效期。</div>'}}<details class="history-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:binding-history"><summary>历史/待恢复绑定记录</summary><div class="row" style="margin-top:8px"><button class="danger" onclick="uiAction(event,'清理历史绑定记录', () => clearBindingHistoryForWorkspace('${{escapeJs(workspaceId)}}'))">清理这个工作区的历史绑定记录</button></div></details></details>`;
}}
function workspaceWorkflowRunsHtml(workspaceId) {{
  const runs = currentSnapshot.workflow_runs || {{}};
  const active = (Array.isArray(runs.active) ? runs.active : []).filter(item => (item.workspace_id || workspaceId) === workspaceId && isEcommerceWorkflowId(item.template_id || item.workflow_name || ''));
  const history = (Array.isArray(runs.history) ? runs.history : []).filter(item => (item.workspace_id || workspaceId) === workspaceId && isEcommerceWorkflowId(item.template_id || item.workflow_name || ''));
  const activeRows = workflowRowsByDevice(active, false);
  const historyRows = workflowRowsByDevice(history, true);
  return `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:workflow-runs"><summary>工作流运行记录（${{active.length}} 运行 / ${{history.length}} 历史）</summary>
    <div class="row">
      <button class="secondary" onclick="uiAction(event,'刷新状态', refresh)">刷新状态</button>
      <button class="secondary" onclick="uiAction(event,'查看记录', () => loadActionLogForWorkspace('${{escapeJs(workspaceId)}}'))">查看记录</button>
      <button class="danger" onclick="uiAction(event,'清理已结束记录', () => clearWorkflowRunsForWorkspace('${{escapeJs(workspaceId)}}'))">清理已结束记录</button>
    </div>
    <details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:workflow-runs-active" ${{active.length ? 'open' : ''}}><summary>正在运行（${{active.length}}）</summary>${{activeRows || '<div class="muted">当前没有正在运行的工作流。</div>'}}</details>
    <details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:workflow-runs-history"><summary>已结束记录（${{history.length}}）</summary>${{historyRows || '<div class="muted">暂无已结束记录。</div>'}}</details>
    <details class="advanced" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:workflow-start">
      <summary>手动启动工作流</summary>
      <select id="${{workflowFieldId(workspaceId, 'template')}}">
        <option value="ecommerce.auto_listing.v1">电商自动上架</option>
      </select>
      <select id="${{workflowFieldId(workspaceId, 'mode')}}">
        <option value="dry_run">只验证不提交</option>
        <option value="debug">调试模式</option>
        <option value="production">正式执行</option>
      </select>
      <input id="${{workflowFieldId(workspaceId, 'budget')}}" type="number" min="0" value="1800" placeholder="最大运行秒数">
      <input id="${{workflowFieldId(workspaceId, 'command')}}" placeholder="命令或执行模块">
      <input id="${{workflowFieldId(workspaceId, 'args')}}" placeholder="参数，空格分隔">
      <button onclick="uiAction(event,'启动工作流', () => startWorkflowForWorkspace('${{escapeJs(workspaceId)}}'))">启动工作流</button>
    </details>
  </details>`;
}}
function workspaceDiagnosticsHtml(workspaceId) {{
  const items = (((currentSnapshot.android || {{}}).diagnostics || {{}}).items || []).filter(item => (item.workspace_id || workspaceId) === workspaceId);
  const rows = items.map(diagnosticRowHtml).join('');
  return `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:diagnostics"><summary>在线诊断（${{items.length}} 台）</summary>${{rows || '<div class="muted">暂无 Android 诊断。</div>'}}</details>`;
}}
function diagnosticRowHtml(item) {{
  const issues = Array.isArray(item.issues) ? item.issues : [];
  const actions = Array.isArray(item.actions) ? item.actions : [];
  const issueHtml = issues.length
    ? issues.map(issue => `<div class="muted">${{escapeHtml(issue.severity || '')}} · ${{escapeHtml(issue.message || issue.code || '')}}</div>`).join('')
    : '<div class="muted">可用 · 手机端在线</div>';
  const actionHtml = actions.map(function(action) {{
    const command = action.command || '';
    const executable = action.kind === 'queue_command' || action.kind === 'retry_command';
    if (action.supported && executable) {{
      return `<button class="secondary" onclick="uiAction(event,'${{escapeJs(action.label || command)}}', () => runDiagnosticAction(${{jsArg(action)}}))">${{escapeHtml(action.label || command)}}</button>`;
    }}
    return `<button class="secondary" disabled title="${{escapeHtml(action.reason || 'not executable from control page')}}">${{escapeHtml(action.label || command)}}</button>`;
  }}).join('');
  return `<div class="item"><strong>${{escapeHtml(item.device_id || 'Android 手机')}} · ${{escapeHtml(statusLabel(item.status || 'unknown'))}}</strong><span>${{escapeHtml(item.workspace_id || '')}} · ${{escapeHtml(item.foreground_package || '')}}</span>${{issueHtml}}<div class="row">${{actionHtml}}</div></div>`;
}}
function workspaceArtifactGroupsHtml(workspaceId) {{
  const artifacts = (((currentSnapshot.artifacts || {{}}).recent) || []).filter(item => (item.status || 'available') === 'available' && (!item.workspace_id || item.workspace_id === workspaceId));
  const images = artifacts.reduce((sum, item) => sum + (Array.isArray(item.files) ? item.files.length : 0), 0);
  const rows = artifactGroupRows(artifacts, workspaceId);
  return `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:artifact-groups"><summary>商品图片组 · ${{artifacts.length}} 组 / ${{images}} 张</summary><div class="management-summary">每组商品图可展开预览、设为当前图片或删除单张图片。</div>${{rows || '<div class="muted">暂无商品图片。</div>'}}</details>`;
}}
function workflowFieldId(workspaceId, field) {{
  return 'workflow_' + String(workspaceId || workspace()).replace(/[^a-zA-Z0-9_-]/g, '_') + '_' + field;
}}
function artifactPreviewId(workspaceId, groupIndex) {{
  return 'artifactPreview_' + String(workspaceId || workspace()).replace(/[^a-zA-Z0-9_-]/g, '_') + '_' + groupIndex;
}}
function artifactGroupRows(items, workspaceId) {{
  const rows = (Array.isArray(items) ? items : []).map(function(item, groupIndex) {{
    const files = Array.isArray(item.files) ? item.files : [];
    const countLabel = files.length ? `${{files.length}} 张图片` : '暂无图片';
    const sourceLabel = sourceLabelFor(item.source, item.purpose);
    const previewId = artifactPreviewId(workspaceId, groupIndex);
    const fileRows = files.map(function(file, index) {{
      const mime = file.mime_type || 'application/octet-stream';
      const imageNo = index + 1;
      const name = file.name || `第 ${{imageNo}} 张图片`;
      return `<div class="item image-file"><div><strong>第 ${{imageNo}} 张</strong><span>${{escapeHtml(name)}} · ${{formatBytes(file.size_bytes || 0)}}</span></div><div class="row"><button class="secondary" onclick="uiAction(event,'预览图片', () => previewArtifact(${{jsArg(item.artifact_id)}}, ${{jsArg(item.workspace_id || workspaceId)}}, ${{index}}, ${{jsArg(mime)}}, ${{jsArg(previewId)}}))">预览</button><button class="secondary" onclick="uiAction(event,'设为当前图片', () => useArtifactFile(${{jsArg(item.artifact_id)}}, ${{index}}, ${{jsArg(item.workspace_id || workspaceId)}}))">设为当前图片</button><button class="danger" onclick="uiAction(event,'删除图片', () => deleteArtifactFile(${{jsArg(item.artifact_id)}}, ${{jsArg(item.workspace_id || workspaceId)}}, ${{index}}))">删除这张</button></div></div>`;
    }}).join('');
    return `<div class="item product-group"><strong>商品图片组 ${{groupIndex + 1}}</strong><span>${{escapeHtml(countLabel)}} · ${{escapeHtml(sourceLabel)}} · ${{formatDate(item.created_at || '')}}</span><div id="${{previewId}}" class="preview muted inline-preview">选择本组图片查看预览</div><div class="list">${{fileRows || '<div class="muted">这组里没有可用图片</div>'}}</div></div>`;
  }});
  const recentRows = rows.slice(0, 5).join('');
  const oldRows = rows.slice(5).join('');
  const history = oldRows ? `<details class="management-panel" data-detail-key="workspace:${{escapeHtml(workspaceId)}}:artifact-groups-more"><summary>更多图片组（${{rows.length - 5}}）</summary><div class="list">${{oldRows}}</div></details>` : '';
  return rows.length ? recentRows + history : '';
}}
function renderIosControllers(overview, fallbackItems) {{
  const workspaces = Array.isArray(overview.items) ? overview.items : [];
  const grouped = [];
  for (const item of workspaces) {{
    const ios = Array.isArray(item.ios_controllers) ? item.ios_controllers : [];
    if (!ios.length) continue;
    grouped.push({{workspace_id: item.workspace_id || workspace(), ios}});
  }}
  if (!grouped.length && Array.isArray(fallbackItems) && fallbackItems.length) {{
    grouped.push({{workspace_id: workspace(), ios: fallbackItems.map(item => ({{
      device_id: item.terminal_id || item.device_id || 'iOS 主控端',
      workspace_id: item.workspace_id || workspace(),
      status: item.status || 'active',
      last_seen_at: item.last_seen_at || '',
      client: item.client || ''
    }}))}});
  }}
  const rows = grouped.map(function(group) {{
    const visible = group.ios.slice(0, 1).map(iosControllerRow).join('');
    const hidden = group.ios.slice(1).map(iosControllerRow).join('');
    const history = hidden ? `<details class="history-panel" data-detail-key="controller:${{escapeHtml(group.workspace_id)}}:ios-history"><summary>历史/其他主控端（${{group.ios.length - 1}}）</summary><div class="row" style="margin-top:8px"><button class="danger" onclick="uiAction(event,'清理旧主控端记录', () => clearIosTerminalHistory('${{escapeJs(group.workspace_id)}}'))">清理旧主控端记录</button></div><div class="list">${{hidden}}</div></details>` : '';
    return `<details class="management-panel" data-detail-key="controller:${{escapeHtml(group.workspace_id)}}:ios" open><summary>${{escapeHtml(group.workspace_id)}} · iOS 主控端（${{group.ios.length}}）</summary>${{visible}}${{history}}</details>`;
  }}).join('');
  const box = document.getElementById('iosControllers');
  if (box) box.innerHTML = rows || '<div class="muted">暂无 iOS 主控端记录。</div>';
}}
function iosControllerRow(item) {{
  const id = item.device_id || item.terminal_id || 'iOS 主控端';
  const lastSeen = item.last_seen_at ? formatDate(item.last_seen_at) : '--';
  return `<div class="device-card"><strong>${{escapeHtml(id)}}</strong><span>${{escapeHtml(statusLabel(item.status || 'active'))}} · ${{escapeHtml(lastSeen)}}</span></div>`;
}}
function isEcommerceWorkflowId(value) {{
  const id = String(value || '').toLowerCase();
  return ['ecommerce', 'commerce', 'listing', 'product', 'pdd'].some(keyword => id.includes(keyword));
}}
function workflowCatalogOptions(selected) {{
  const raw = currentSnapshot.workflow_templates || [];
  const templates = Array.isArray(raw) ? raw : (raw.items || []);
  const fallback = ['ecommerce.auto_listing.v1'];
  const ids = templates.length
    ? templates.filter(item => String(item.category || '').toLowerCase() === 'ecommerce' || isEcommerceWorkflowId(item.template_id || item.id || '')).map(item => item.template_id || item.id || '').filter(Boolean)
    : fallback;
  return Array.from(new Set(ids)).map(id => `<option value="${{escapeHtml(id)}}" ${{id === selected ? 'selected' : ''}}>${{escapeHtml(id)}}</option>`).join('');
}}
function workflowAddHtml(device) {{
  const selectId = 'wf_' + Math.random().toString(36).slice(2, 10);
  return `<div class="workflow-add"><select id="${{selectId}}">${{workflowCatalogOptions('ecommerce.auto_listing.v1')}}</select><button class="secondary" onclick="uiAction(event,'添加工作流', () => addDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}', document.getElementById('${{selectId}}').value))">添加到这台设备</button></div>`;
}}
function workflowManagementHtml(device) {{
  const controls = Array.isArray(device.workflow_controls) ? device.workflow_controls : [];
  const count = controls.length;
  const list = workflowControlsHtml(device) || '<div class="muted">这台设备还没有配置工作流。先在下面添加，添加后才会显示开关和修复按钮。</div>';
  const workspaceId = device.workspace_id || workspace();
  const deviceId = device.device_id || '';
  return `<details class="management-panel" data-detail-key="device:${{escapeHtml(workspaceId)}}:${{escapeHtml(deviceId)}}:workflows"><summary>工作流（${{count}}）</summary>${{list}}<details class="advanced" data-detail-key="device:${{escapeHtml(workspaceId)}}:${{escapeHtml(deviceId)}}:workflow-add"><summary>给这台设备添加工作流</summary>${{workflowAddHtml(device)}}</details></details>`;
}}
function workflowControlsHtml(device) {{
  const controls = Array.isArray(device.workflow_controls) ? device.workflow_controls : [];
  if (!controls.length) return '';
  return controls.map(function(control) {{
    const workflowId = control.workflow_id || 'ecommerce.auto_listing.v1';
    const enabled = control.enabled !== false;
    const state = enabled ? '已启用' : '已暂停';
    const reason = control.reason ? ` · ${{escapeHtml(control.reason)}}` : '';
    const repair = control.last_repair_type ? `<div class="muted">最近修复：${{escapeHtml(control.last_repair_type)}} · ${{escapeHtml(formatDate(control.last_repair_at))}}</div>` : '';
    return `<details class="workflow-control" data-detail-key="device:${{escapeHtml(device.workspace_id || workspace())}}:${{escapeHtml(device.device_id || '')}}:workflow:${{escapeHtml(workflowId)}}"><summary>${{escapeHtml(workflowDisplayName(workflowId))}} · ${{enabled ? '运行允许' : '已暂停'}}</summary><div class="workflow-title"><div><span>${{escapeHtml(workflowId)}} · ${{state}}${{reason}}</span></div><span class="badge">${{enabled ? '运行允许' : '已暂停'}}</span></div>${{repair}}<div class="row">
      <button class="secondary" onclick="uiAction(event,'${{enabled ? '暂停工作流' : '启用工作流'}}', () => setDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}',${{enabled ? 'false' : 'true'}}))">${{enabled ? '暂停工作流' : '启用工作流'}}</button>
      <button class="secondary" onclick="uiAction(event,'检查状态', () => repairDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}','status'))">检查状态</button>
      <button class="secondary" onclick="uiAction(event,'打开 PDD', () => repairDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}','open_pdd'))">打开 PDD</button>
      <button class="secondary" onclick="uiAction(event,'修复无障碍', () => repairDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}','accessibility_settings'))">修复无障碍</button>
      <button class="secondary" onclick="uiAction(event,'截图授权', () => repairDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}','screenshot_permission'))">截图授权</button>
      <button class="danger" onclick="uiAction(event,'从设备移除工作流', () => deleteDeviceWorkflow('${{escapeJs(device.workspace_id || workspace())}}','${{escapeJs(device.device_id || '')}}','${{escapeJs(workflowId)}}'))">从设备移除</button>
    </div></details>`;
  }}).join('');
}}
function workflowDisplayName(workflowId) {{
  if (workflowId === 'ecommerce.auto_listing.v1') return '电商自动上架';
  if (workflowId === 'local.cli.run.v1') return '本地命令';
  if (workflowId === 'langgraph.run.v1') return 'LangGraph';
  if (workflowId === 'crewai.run.v1') return 'CrewAI';
  return workflowId || '工作流';
}}
function renderPairingRequests(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    const role = roleLabel(item.device_role || item.role || '');
    const requestId = item.request_id || item.token_id || '';
    const device = item.device_id ? ` · ${{item.device_id}}` : '';
    rows.push(`<div class="item"><strong>待批准 ${{escapeHtml(role)}}${{escapeHtml(device)}}</strong><span>${{escapeHtml(item.workspace_id)}} · 手机已请求绑定</span><div class="muted">请求时间：${{escapeHtml(formatDate(item.created_at))}}</div><div class="row"><button onclick="uiAction(event,'批准绑定请求', () => approvePairingRequest(${{jsArg(requestId)}}))">批准并下发配对码</button><button class="danger" onclick="uiAction(event,'拒绝绑定请求', () => rejectPairingRequest(${{jsArg(requestId)}}))">拒绝请求</button></div></div>`);
  }}
  const box = document.getElementById('pairingRequests');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无手机请求。手机端点击“请求绑定”后会显示在这里，批准后手机才会收到配对码。</div>';
}}
function renderPendingPairings(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    const status = item.status || '';
    const role = roleLabel(item.device_role || item.role || '');
    rows.push(`<div class="item"><strong>${{escapeHtml(role)}}配对码 ${{escapeHtml(shortId(item.token_id || ''))}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(statusLabel(status))}}</span><div class="muted">有效期至：${{escapeHtml(formatDate(item.expires_at))}}</div><button class="danger" onclick="uiAction(event,'取消配对码', () => cancelPairingToken(${{jsArg(item.token_id || '')}}))">取消配对码</button></div>`);
  }}
  const box = document.getElementById('pendingPairings');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无待使用配对码。手机端可点“请求配对码并绑定”，或在这里手动生成。</div>';
}}
function renderPairingHistory(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    const role = roleLabel(item.device_role || item.role || '');
    const device = item.bound_device_id ? ` · 绑定设备 ${{item.bound_device_id}}` : '';
    rows.push(`<div class="item"><strong>${{escapeHtml(role)}}记录 ${{escapeHtml(shortId(item.token_id || ''))}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(statusLabel(item.status || ''))}}${{escapeHtml(device)}}</span><div class="muted">创建：${{escapeHtml(formatDate(item.created_at))}} · 有效期至：${{escapeHtml(formatDate(item.expires_at))}}</div><button class="danger" onclick="uiAction(event,'删除配对记录', () => deletePairingToken(${{jsArg(item.token_id || '')}}))">删除记录</button></div>`);
  }}
  const box = document.getElementById('pairingHistory');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无历史配对记录。</div>';
}}
function renderBoundDevices(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    if ((item.status || 'active') !== 'active') continue;
    const label = deviceLabel(item);
    rows.push(`<div class="item"><strong>${{escapeHtml(label)}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(roleLabel(item.device_role || item.role || ''))}} · 已绑定</span><div class="muted">最近使用：${{escapeHtml(formatDate(item.last_seen_at || item.bound_at))}}</div><button class="danger" onclick="uiAction(event,'撤销绑定', () => revokeDeviceBinding(${{jsArg(item.token_id || '')}}, ${{jsArg(label)}}))">撤销绑定</button></div>`);
  }}
  const box = document.getElementById('boundDevices');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无已绑定设备</div>';
}}
function renderBindingHistory(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    const label = deviceLabel(item);
    const status = statusLabel(item.status || '');
    const when = item.revoked_at || item.replaced_at || item.last_seen_at || item.created_at || '';
    rows.push(`<div class="item"><strong>${{escapeHtml(label)}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(roleLabel(item.device_role || item.role || ''))}} · ${{escapeHtml(status)}}</span><div class="muted">记录时间：${{escapeHtml(formatDate(when))}}</div></div>`);
  }}
  const box = document.getElementById('bindingHistory');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无历史绑定记录。</div>';
}}
function renderOnlineDevices(items) {{
  const rows = [];
  for (const item of Array.isArray(items) ? items : []) {{
    rows.push(`<div class="item"><strong>${{escapeHtml(item.device_id || 'Android 手机')}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(statusLabel(item.status || ''))}}</span><div class="muted">最近心跳：${{escapeHtml(formatDate(item.last_seen_at))}}</div></div>`);
  }}
  const box = document.getElementById('androidDevices');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无在线手机心跳</div>';
}}
function setSummaryValue(id, value) {{
  const el = document.getElementById(id);
  if (el) el.textContent = String(value ?? 0);
}}
function renderTargetDevices(devices) {{
  const select = document.getElementById('targetDevice');
  if (!select) return;
  const previous = select.value || '*';
  const filtered = (Array.isArray(devices) ? devices : []).filter(item => !item.workspace_id || item.workspace_id === workspace());
  const options = ['<option value="*">当前工作区全部 Android 手机</option>'];
  for (const item of filtered) {{
    const id = item.device_id || '';
    if (!id) continue;
    options.push(`<option value="${{escapeHtml(id)}}">${{escapeHtml(id)}} · ${{escapeHtml(item.status || '')}}</option>`);
  }}
  select.innerHTML = options.join('');
  select.value = Array.from(select.options).some(option => option.value === previous) ? previous : '*';
}}
function renderCommandMetadata() {{
  const box = document.getElementById('commandMetadata');
  const select = document.getElementById('operation');
  if (!box || !select) return;
  const item = commandCatalog()[select.value] || {{}};
  const capabilities = Array.isArray(item.required_capabilities) ? item.required_capabilities.join(', ') : '';
  const packages = Array.isArray(item.required_packages) && item.required_packages.length ? item.required_packages.join(', ') : '';
  const flags = [];
  if (item.requires_accessibility) flags.push('需要无障碍');
  if (item.requires_artifact) flags.push('需要先选一张图片');
  if (packages) flags.push('需要包 ' + packages);
  box.innerHTML = `<strong>${{escapeHtml(item.label || select.value)}} · 风险 ${{escapeHtml(item.risk || 'unknown')}}</strong><span>${{escapeHtml(capabilities ? '能力 ' + capabilities : '未声明能力')}}</span><div class="muted">${{escapeHtml(flags.join(' · ') || '无额外权限要求')}}</div>`;
}}
function renderAndroidDiagnostics(items) {{
  const rows = items.map(diagnosticRowHtml);
  const summary = document.getElementById('diagnosticsSummary');
  if (summary) summary.textContent = `在线诊断（${{rows.length}} 台）`;
  const box = document.getElementById('androidDiagnostics');
  if (box) box.innerHTML = rows.length ? rows.join('') : '<div class="muted">暂无 Android 诊断</div>';
}}
function workflowRunRow(item, terminal) {{
    const status = item.status || '';
    const buttons = terminal
      ? `<button class="secondary" onclick="uiAction(event,'重试工作流', () => retryWorkflowRun(${{jsArg(item.run_id)}}))">重试</button>`
      : `<button class="secondary" onclick="uiAction(event,'取消工作流', () => cancelWorkflowRun(${{jsArg(item.run_id)}}))">取消</button>`;
    const deleteButton = `<button class="danger" onclick="uiAction(event,'删除工作流记录', () => deleteWorkflowRun(${{jsArg(item.run_id)}}))">删除记录</button>`;
    return `<div class="item"><strong>${{escapeHtml(workflowDisplayName(item.template_id || ''))}}</strong><span>${{escapeHtml(item.workspace_id)}} · ${{escapeHtml(statusLabel(status))}} · ${{escapeHtml(shortId(item.run_id || ''))}}</span><div class="muted">更新时间：${{escapeHtml(formatDate(item.updated_at || item.created_at || ''))}}</div><div class="row">${{buttons}}${{deleteButton}}</div></div>`;
}}
function workflowDeviceKey(item) {{
  const inputs = item.inputs || {{}};
  return inputs.device_id || inputs.android_device_id || inputs.target_device_id || item.device_id || '未指定设备';
}}
function workflowRowsByDevice(items, terminal) {{
  const groups = new Map();
  for (const item of items) {{
    const key = workflowDeviceKey(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }}
  return Array.from(groups.entries()).map(function(entry) {{
    const deviceId = entry[0];
    const rows = entry[1].map(item => workflowRunRow(item, terminal)).join('');
    return `<details class="management-panel" data-detail-key="workflow-runs:${{terminal ? 'history' : 'active'}}:${{escapeHtml(deviceId)}}" ${{terminal ? '' : 'open'}}><summary>${{escapeHtml(deviceId)}} · ${{entry[1].length}} 条</summary><div class="list">${{rows}}</div></details>`;
  }}).join('');
}}
function renderWorkflowRuns(runs) {{
  const data = runs || {{}};
  const active = Array.isArray(data.active) ? data.active : [];
  const history = Array.isArray(data.history) ? data.history : [];
  const activeRows = workflowRowsByDevice(active, false);
  const historyRows = workflowRowsByDevice(history, true);
  const html = [
    `<details class="management-panel" data-detail-key="workflow-runs:active" ${{active.length ? 'open' : ''}}><summary>正在运行（${{active.length}}）</summary>${{activeRows || '<div class="muted">当前没有正在运行的工作流。</div>'}}</details>`,
    `<details class="management-panel" data-detail-key="workflow-runs:history"><summary>已结束记录（${{history.length}}）</summary>${{historyRows || '<div class="muted">暂无已结束记录。</div>'}}</details>`
  ].join('');
  const box = document.getElementById('workflowRuns');
  if (box) box.innerHTML = html;
}}
function renderArtifacts(items) {{
  const visible = (Array.isArray(items) ? items : []).filter(item => (item.status || 'available') === 'available');
  const rows = visible.map(function(item, groupIndex) {{
    const files = Array.isArray(item.files) ? item.files : [];
    const countLabel = files.length ? `${{files.length}} 张图片` : '暂无图片';
    const sourceLabel = sourceLabelFor(item.source, item.purpose);
    const previewId = `artifactPreview-${{groupIndex}}`;
    const fileRows = files.map(function(file, index) {{
      const mime = file.mime_type || 'application/octet-stream';
      const imageNo = index + 1;
      const name = file.name || `第 ${{imageNo}} 张图片`;
      return `<div class="item image-file"><div><strong>第 ${{imageNo}} 张</strong><span>${{escapeHtml(name)}} · ${{formatBytes(file.size_bytes || 0)}}</span></div><div class="row"><button class="secondary" onclick="uiAction(event,'预览图片', () => previewArtifact(${{jsArg(item.artifact_id)}}, ${{jsArg(item.workspace_id)}}, ${{index}}, ${{jsArg(mime)}}, ${{jsArg(previewId)}}))">预览</button><button class="secondary" onclick="uiAction(event,'设为当前图片', () => useArtifactFile(${{jsArg(item.artifact_id)}}, ${{index}}))">设为当前图片</button><button class="danger" onclick="uiAction(event,'删除图片', () => deleteArtifactFile(${{jsArg(item.artifact_id)}}, ${{jsArg(item.workspace_id)}}, ${{index}}))">删除这张</button></div></div>`;
    }}).join('');
    return `<div class="item product-group"><strong>商品图片组 ${{groupIndex + 1}}</strong><span>${{escapeHtml(countLabel)}} · ${{escapeHtml(sourceLabel)}} · ${{formatDate(item.created_at || '')}}</span><div id="${{previewId}}" class="preview muted inline-preview">选择本组图片查看预览</div><div class="list">${{fileRows || '<div class="muted">这组里没有可用图片</div>'}}</div></div>`;
  }});
  const recentRows = rows.slice(0, 5).join('');
  const oldRows = rows.slice(5).join('');
  const summary = document.getElementById('artifactsSummary');
  if (summary) summary.textContent = `商品图片组（${{rows.length}} 组）`;
  const history = oldRows ? `<details class="management-panel" data-detail-key="artifact-groups:more"><summary>更多图片组（${{rows.length - 5}}）</summary><div class="list">${{oldRows}}</div></details>` : '';
  const box = document.getElementById('artifacts');
  if (box) box.innerHTML = rows.length ? recentRows + history : '<div class="muted">暂无商品图片</div>';
}}
function sourceLabelFor(source, purpose) {{
  const text = `${{source || ''}} ${{purpose || ''}}`;
  if (text.includes('android')) return '来自 Android 手机';
  if (text.includes('ios')) return '来自主控上传';
  if (text.includes('screen')) return '屏幕截图';
  return '工作图片';
}}
function shortId(value) {{
  const text = String(value || '');
  return text.length <= 18 ? text : text.slice(0, 10) + '...' + text.slice(-5);
}}
function formatBytes(value) {{
  const bytes = Number(value || 0);
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}}
function formatDate(value) {{
  const text = String(value || '');
  if (!text) return '--';
  const normalized = text.includes('T') ? text : text.replace(' ', 'T');
  const date = new Date(normalized);
  if (!Number.isNaN(date.getTime())) {{
    const pad = value => String(value).padStart(2, '0');
    return `${{String(date.getFullYear()).slice(2)}}-${{pad(date.getMonth() + 1)}}-${{pad(date.getDate())}} ${{pad(date.getHours())}}:${{pad(date.getMinutes())}}:${{pad(date.getSeconds())}}`;
  }}
  return text;
}}
function artifactUrl(artifactId, workspaceId, fileIndex) {{
  const query = new URLSearchParams({{workspace_id: workspaceId || workspace(), file_index: String(fileIndex || 0)}});
  return `/mobile/artifacts/${{encodeURIComponent(artifactId)}}?${{query.toString()}}`;
}}
async function previewArtifact(artifactId, workspaceId, fileIndex, mimeType, previewId) {{
  const url = artifactUrl(artifactId, workspaceId, fileIndex);
  const preview = document.getElementById(previewId || 'artifactPreview');
  if (!preview) return;
  preview.className = 'preview muted';
  preview.textContent = `正在加载第 ${{Number(fileIndex || 0) + 1}} 张图片...`;
  setActionStatus('正在加载图片预览...');
  const res = await fetch(url, {{headers: requestHeaders(false)}});
  if (!res.ok) {{
    preview.className = 'preview muted';
    preview.textContent = await res.text();
    setActionStatus('预览加载失败');
    return;
  }}
  if ((mimeType || '').startsWith('image/')) {{
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    preview.className = 'preview';
    preview.innerHTML = `<img src="${{escapeHtml(objectUrl)}}" alt="商品图片预览" onload="setActionStatus('预览已加载')"><div class="muted">第 ${{Number(fileIndex || 0) + 1}} 张 · 图片组 ${{escapeHtml(shortId(artifactId))}}</div>`;
    return;
  }}
  const text = await res.text();
  const popup = window.open('', '_blank');
  if (popup) {{
    popup.document.write(`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><pre style="white-space:pre-wrap;font:13px ui-monospace,SFMono-Regular,Consolas,monospace;padding:16px">${{escapeHtml(text)}}</pre>`);
    popup.document.close();
  }}
  preview.className = 'preview muted';
  preview.textContent = `已在新窗口打开第 ${{Number(fileIndex || 0) + 1}} 个文件`;
  setActionStatus('预览已打开');
}}
function useArtifactFile(artifactId, fileIndex, workspaceId) {{
  const workspaceInput = document.getElementById('workspace');
  if (workspaceId && workspaceInput) workspaceInput.value = workspaceId;
  document.getElementById('param').value = artifactId;
  const fileIndexInput = document.getElementById('artifactFileIndex');
  if (fileIndexInput) fileIndexInput.value = String(fileIndex || 0);
  setActionStatus(`已设为当前图片：第 ${{Number(fileIndex || 0) + 1}} 张`);
  navigator.clipboard && navigator.clipboard.writeText(artifactId);
}}
function copyArtifactId(artifactId) {{
  useArtifactFile(artifactId, 0);
}}
async function deleteArtifactFile(artifactId, workspaceId, fileIndex) {{
  if (!confirm(`删除这组里的第 ${{Number(fileIndex || 0) + 1}} 张图片？`)) return;
  await postJson('/ios/control/action', {{
    action: 'delete_artifact_file',
    workspace_id: workspaceId || workspace(),
    artifact_id: artifactId,
    file_index: Number(fileIndex || 0)
  }});
  await refresh();
}}
async function cancelPairingToken(tokenId) {{
  return cancelPairingTokenForWorkspace(workspace(), tokenId);
}}
async function cancelPairingTokenForWorkspace(workspaceId, tokenId) {{
  if (!tokenId) return;
  if (!confirm('取消这个待使用配对码？取消后手机需要重新生成配对码。')) return;
  await postJson('/ios/control/action', {{
    action: 'cancel_pairing_token',
    workspace_id: workspaceId || workspace(),
    token_id: tokenId
  }});
  await refresh();
}}
async function deletePairingToken(tokenId) {{
  return deletePairingTokenForWorkspace(workspace(), tokenId);
}}
async function deletePairingTokenForWorkspace(workspaceId, tokenId) {{
  if (!tokenId) return;
  if (!confirm('删除这条配对记录？这只清理主控列表，不影响已经撤销的设备。')) return;
  await postJson('/ios/control/action', {{
    action: 'delete_pairing_token',
    workspace_id: workspaceId || workspace(),
    token_id: tokenId
  }});
  await refresh();
}}
async function clearPairingHistory() {{
  return clearPairingHistoryForWorkspace(workspace());
}}
async function clearPairingHistoryForWorkspace(workspaceId) {{
  if (!confirm('清理当前工作区已用、过期、取消和撤销的配对记录？不会影响待使用配对码和已绑定设备。')) return;
  await postJson('/ios/control/action', {{
    action: 'clear_pairing_history',
    workspace_id: workspaceId || workspace()
  }});
  await refresh();
}}
async function clearBindingHistory() {{
  return clearBindingHistoryForWorkspace(workspace());
}}
async function clearBindingHistoryForWorkspace(workspaceId) {{
  if (!confirm('清理当前工作区已撤销、已替换的历史绑定记录？不会影响当前仍有效的设备绑定。')) return;
  await postJson('/ios/control/action', {{
    action: 'clear_binding_history',
    workspace_id: workspaceId || workspace()
  }});
  await refresh();
}}
async function clearIosTerminalHistory(workspaceId) {{
  if (!confirm('只保留当前工作区最近使用的一个主控端记录，清理旧记录？')) return;
  await postJson('/ios/control/action', {{
    action: 'clear_ios_terminal_history',
    workspace_id: workspaceId || workspace(),
    keep_latest: 1
  }});
  await refresh();
}}
async function revokeDeviceBinding(tokenId, label) {{
  return revokeDeviceBindingForWorkspace(workspace(), tokenId, label);
}}
async function revokeDeviceBindingForWorkspace(workspaceId, tokenId, label) {{
  if (!tokenId) return;
  if (!confirm(`撤销“${{label || '这个设备'}}”的绑定？撤销后该端需要重新配对。`)) return;
  await postJson('/ios/control/action', {{
    action: 'revoke_device_binding',
    workspace_id: workspaceId || workspace(),
    token_id: tokenId
  }});
  await refresh();
}}
async function approvePairingRequest(requestId) {{
  return approvePairingRequestForWorkspace(workspace(), requestId);
}}
async function approvePairingRequestForWorkspace(workspaceId, requestId) {{
  if (!requestId) return;
  await postJson('/ios/control/action', {{
    action: 'approve_pairing_request',
    workspace_id: workspaceId || workspace(),
    request_id: requestId,
    ttl_minutes: pairingTtlMinutes(workspaceId || workspace())
  }});
  await refresh();
}}
async function rejectPairingRequest(requestId) {{
  return rejectPairingRequestForWorkspace(workspace(), requestId);
}}
async function rejectPairingRequestForWorkspace(workspaceId, requestId) {{
  if (!requestId) return;
  if (!confirm('拒绝这台手机的绑定请求？')) return;
  await postJson('/ios/control/action', {{
    action: 'reject_pairing_request',
    workspace_id: workspaceId || workspace(),
    request_id: requestId
  }});
  await refresh();
}}
async function cancelWorkflowRun(runId) {{
  await postJson('/ios/control/action', {{action: 'cancel_workflow_run', workspace_id: workspace(), run_id: runId}});
  await refresh();
}}
async function retryWorkflowRun(runId) {{
  await postJson('/ios/control/action', {{action: 'retry_workflow_run', workspace_id: workspace(), run_id: runId}});
  await refresh();
}}
async function deleteWorkflowRun(runId) {{
  if (!runId) return;
  if (!confirm('删除这条工作流记录？对应的执行任务也会一并清掉。')) return;
  await postJson('/ios/control/action', {{action: 'delete_workflow_run', workspace_id: workspace(), run_id: runId}});
  await refresh();
}}
async function clearWorkflowRuns() {{
  return clearWorkflowRunsForWorkspace(workspace());
}}
async function clearWorkflowRunsForWorkspace(workspaceId) {{
  if (!confirm('清理当前工作区所有已完成、失败或已取消的工作流记录？正在运行的不会删除。')) return;
  await postJson('/ios/control/action', {{action: 'clear_workflow_runs', workspace_id: workspaceId || workspace()}});
  await refresh();
}}
async function uploadFiles() {{
  const files = Array.from(document.getElementById('files').files || []);
  const encoded = [];
  for (const file of files) {{
    const dataUrl = await new Promise((resolve, reject) => {{
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    }});
    encoded.push({{name: file.name, mime_type: file.type || 'application/octet-stream', data_url: dataUrl}});
  }}
  await postJson('/mobile/artifacts', {{workspace_id: workspace(), source: 'ios_terminal', purpose: 'mobile_work_image', tags: ['ios', 'upload'], files: encoded}});
}}
async function cleanupArtifacts() {{
  await postJson('/ios/control/action', {{action: 'cleanup_artifacts', workspace_id: workspace(), older_than_hours: 168}});
}}
async function cleanupState() {{
  await postJson('/ios/control/action', {{action: 'cleanup_state', workspace_id: workspace(), older_than_hours: 168}});
}}
async function validateState() {{
  await postJson('/ios/control/action', {{action: 'validate_state', workspace_id: workspace()}});
}}
async function loadActionLog() {{
  return loadActionLogForWorkspace(workspace());
}}
async function loadActionLogForWorkspace(workspaceId) {{
  await postJson('/ios/control/action', {{action: 'action_log', workspace_id: workspaceId || workspace(), limit: 50}});
}}
async function startWorkflow() {{
  return startWorkflowForWorkspace(workspace());
}}
async function startWorkflowForWorkspace(workspaceId) {{
  const field = name => document.getElementById(workflowFieldId(workspaceId || workspace(), name));
  const templateId = (field('template') && field('template').value) || 'ecommerce.auto_listing.v1';
  const command = ((field('command') && field('command').value) || '').trim();
  const args = ((field('args') && field('args').value) || '').trim().split(/\\s+/).filter(Boolean);
  const inputs = {{
    source: 'ios_terminal',
    promote_mode: (field('mode') && field('mode').value) || 'dry_run',
    budget: {{max_runtime_seconds: Number((field('budget') && field('budget').value) || 0)}}
  }};
  if (templateId === 'local.cli.run.v1' && command) {{
    inputs.command = command;
  }} else if ((templateId === 'langgraph.run.v1' || templateId === 'crewai.run.v1') && command) {{
    inputs.module = command;
    inputs.args = args;
  }}
  await postJson('/ios/control/action', {{
    action: 'start_workflow_run',
    template_id: templateId,
    workspace_id: workspaceId || workspace(),
    inputs
  }});
}}
async function updateRuntimeProfile() {{
  const allowed = document.getElementById('allowedCommands').value.split(',').map(item => item.trim()).filter(Boolean);
  await postJson('/ios/control/action', {{
    action: 'update_runtime_profile',
    workspace_id: workspace(),
    runtime_profile: {{
      venv_path: document.getElementById('runtimeVenv').value,
      dependency_policy: document.getElementById('dependencyPolicy').value,
      allowed_local_commands: allowed
    }}
  }});
  await refresh();
}}
async function queueAndroidCommand() {{
  const operation = document.getElementById('operation').value;
  const deviceId = document.getElementById('targetDevice').value || '*';
  const value = document.getElementById('param').value;
  const fileIndex = Number(document.getElementById('artifactFileIndex').value || 0);
  const targetPackage = document.getElementById('targetPackage').value;
  const title = document.getElementById('title').value;
  const price = document.getElementById('price').value;
  const description = document.getElementById('description').value;
  const allowSubmit = document.getElementById('allowSubmit').checked;
  let params = {{}};
  if (operation === 'app.launch') params = {{app_name: value}};
  else if (operation === 'url.open') params = {{url: value}};
  else if (operation === 'clipboard.write') params = {{text: value}};
  else if (operation === 'artifact.download') params = {{artifact_id: value, file_index: fileIndex}};
  else if (operation === 'image.share_to_app') params = {{artifact_id: value, file_index: fileIndex, target_package: targetPackage}};
  else if (operation === 'pdd.share_image') params = {{artifact_id: value, file_index: fileIndex}};
  else if (operation === 'pdd.create_listing') params = {{artifact_id: value, file_index: fileIndex, title, price, description, allow_submit: allowSubmit}};
  await postJson('/ios/control/action', {{action: 'queue_android_command', workspace_id: workspace(), device_id: deviceId, operation, params}});
}}
async function runDiagnosticAction(action) {{
  const payload = typeof action === 'string' ? JSON.parse(action) : action;
  await postJson('/ios/control/action', {{
    action: 'queue_android_command',
    workspace_id: workspace(),
    device_id: payload.device_id || '*',
    operation: payload.operation || payload.command,
    params: payload.params || {{}},
    requested_by: payload.kind || 'diagnostic'
  }});
  await refresh();
}}
async function setDeviceWorkflow(workspaceId, deviceId, workflowId, enabled) {{
  await postJson('/ios/control/action', {{
    action: 'set_device_workflow_state',
    workspace_id: workspaceId || workspace(),
    device_id: deviceId,
    workflow_id: workflowId || 'ecommerce.auto_listing.v1',
    enabled: !!enabled,
    reason: enabled ? '' : '主控端手动暂停'
  }});
  await refresh();
}}
async function addDeviceWorkflow(workspaceId, deviceId, workflowId) {{
  await postJson('/ios/control/action', {{
    action: 'add_device_workflow',
    workspace_id: workspaceId || workspace(),
    device_id: deviceId,
    workflow_id: workflowId || 'ecommerce.auto_listing.v1',
    enabled: true
  }});
  await refresh();
}}
async function deleteDeviceWorkflow(workspaceId, deviceId, workflowId) {{
  if (!confirm('从这台设备移除这个工作流？不会删除其他设备的工作流配置。')) return;
  await postJson('/ios/control/action', {{
    action: 'delete_device_workflow',
    workspace_id: workspaceId || workspace(),
    device_id: deviceId,
    workflow_id: workflowId || 'ecommerce.auto_listing.v1'
  }});
  await refresh();
}}
async function repairDeviceWorkflow(workspaceId, deviceId, workflowId, repairType) {{
  await postJson('/ios/control/action', {{
    action: 'repair_device_workflow',
    workspace_id: workspaceId || workspace(),
    device_id: deviceId,
    workflow_id: workflowId || 'ecommerce.auto_listing.v1',
    repair_type: repairType || 'status'
  }});
  await refresh();
}}
async function clearAndroidCommandsForDevice(workspaceId, deviceId) {{
  await postJson('/ios/control/action', {{
    action: 'clear_android_commands',
    workspace_id: workspaceId || workspace(),
    device_id: deviceId || '*'
  }});
  await refresh();
}}
document.getElementById('managementToken').value = localStorage.getItem('spiritkin_control_token') || localStorage.getItem('spiritkin_management_token') || '';
document.getElementById('operation').addEventListener('change', renderCommandMetadata);
document.getElementById('workspace').addEventListener('change', function() {{
  renderSnapshot(currentSnapshot);
}});
document.addEventListener('toggle', function(event) {{
  if (event && event.target && event.target.tagName === 'DETAILS') {{
    rememberDetailState(event.target);
  }}
}}, true);
setInterval(function() {{
  refresh({{silent: true}});
}}, 3000);
window.addEventListener('unhandledrejection', function(event) {{
  const reason = event && event.reason ? event.reason : '未知错误';
  const message = friendlyError(reason && reason.message ? reason.message : reason);
  setActionStatus(message, 'error');
  showOutput({{ok: false, error: message}});
}});
window.addEventListener('error', function(event) {{
  const message = friendlyError(event && event.message ? event.message : '页面脚本错误');
  setActionStatus(message, 'error');
  showOutput({{ok: false, error: message}});
}});
if ('serviceWorker' in navigator) {{
  navigator.serviceWorker.register('/ios/service-worker.js').catch(function() {{}});
}}
renderSnapshot(initialSnapshot);
refresh();
</script>
</body>
</html>
"""


def ios_terminal_manifest() -> dict[str, object]:
    return {
        "name": "SpiritKin iOS Terminal",
        "short_name": "SpiritKin",
        "description": "SpiritKin mobile control terminal for workflows, safety, services, Android bridge, and artifacts.",
        "id": "/ios/terminal",
        "start_url": "/ios/terminal",
        "scope": "/ios/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f6f8fb",
        "theme_color": "#0a1220",
        "icons": [
            {"src": "/ios/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
            {"src": "/ios/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/ios/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


def ios_service_worker_js() -> str:
    return """const CACHE_NAME = "spiritkin-ios-terminal-v1";
const CORE_ASSETS = ["/ios/terminal", "/ios/terminal.webmanifest", "/ios/icon.svg"];
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (url.pathname === "/ios/terminal" || url.pathname === "/ios/terminal.webmanifest" || url.pathname === "/ios/icon.svg") {
    event.respondWith(fetch(event.request).then(response => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
      return response;
    }).catch(() => caches.match(event.request)));
  }
});
"""


def ios_icon_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0a1220"/>
  <path d="M126 154h260v204H126z" fill="#f8fbff"/>
  <path d="M156 190h200v28H156zm0 58h138v28H156zm0 58h170v28H156z" fill="#0250cc"/>
  <circle cx="376" cy="316" r="38" fill="#16a34a"/>
  <path d="M366 315l8 9 19-23" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def pairing_html(pairing: dict[str, object]) -> str:
    workspace_id = html.escape(str(pairing.get("workspace_id") or ""))
    server_url = html.escape(str(pairing.get("server_url") or ""))
    token = html.escape(str(pairing.get("pairing_token") or ""))
    deep_link = html.escape(str(pairing.get("deep_link") or ""))
    expires_at = html.escape(str(pairing.get("expires_at") or ""))
    qr_png = str(pairing.get("qr_png_data_url") or "")
    qr_html = (
        f'<img class="qr" src="{html.escape(qr_png)}" alt="Pairing QR">'
        if qr_png.startswith("data:image/png;base64,")
        else '<div class="empty-qr">QR library not installed</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SpiritKin Android Pairing</title>
  <style>
    :root {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; background: #f3f6fb; }}
    body {{ margin: 0; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 20px; }}
    h1 {{ font-size: 24px; margin: 0 0 6px; }}
    .muted {{ color: #64748b; font-size: 13px; margin-bottom: 16px; }}
    .card {{ background: #fff; border: 1px solid #dbe5f3; border-radius: 8px; padding: 16px; margin: 12px 0; }}
    .label {{ color: #64748b; font-size: 12px; margin-top: 12px; }}
    .value {{ overflow-wrap: anywhere; font-size: 15px; padding: 10px; background: #eef4ff; border-radius: 6px; margin-top: 5px; }}
    .token {{ font-size: 19px; font-weight: 700; color: #0757c8; letter-spacing: 0; }}
    .qr {{ display: block; width: min(260px, 100%); height: auto; margin: 10px auto; }}
    .empty-qr {{ padding: 28px; text-align: center; background: #eef4ff; border-radius: 8px; color: #64748b; }}
    button, a.button {{ display: block; box-sizing: border-box; width: 100%; border: 0; border-radius: 7px; padding: 12px 14px; margin-top: 10px; background: #0757c8; color: white; text-align: center; text-decoration: none; font-weight: 700; }}
    button.secondary {{ background: #e8eef8; color: #172033; }}
  </style>
</head>
<body>
<main>
  <h1>Android 手机端配对</h1>
  <div class="muted">打开本页就会生成一个新的临时配对码。已绑定的手机不需要每次重新生成。</div>
  <section class="card">
    {qr_html}
    <a class="button" href="{deep_link}">在本机打开配对链接</a>
    <button class="secondary" onclick="copyText('{token}', 'pairing token')">复制配对码</button>
    <button class="secondary" onclick="copyText('{server_url}', 'server URL')">复制服务器地址</button>
  </section>
  <section class="card">
    <div class="label">服务器地址</div>
    <div class="value">{server_url}</div>
    <div class="label">工作区</div>
    <div class="value">{workspace_id}</div>
    <div class="label">配对码</div>
    <div class="value token">{token}</div>
    <div class="label">有效期</div>
    <div class="value">{expires_at}</div>
    <div class="label">一键配对链接</div>
    <div class="value">{deep_link}</div>
  </section>
  <section class="card">
    <div class="muted">手动配对：在 APK 里填服务器地址、工作区、配对码，然后点“绑定手机”。二维码/一键配对：用手机打开本页或扫码后选择 SpiritKin Control Bridge。</div>
  </section>
</main>
<script>
function copyText(text, label) {{
  navigator.clipboard.writeText(text).then(function() {{
    alert(label + ' 已复制');
  }}, function() {{
    prompt('复制 ' + label, text);
  }});
}}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    store = ControlPlaneStore()
    ios_runtime = None
    ios_runtime_lock = RLock()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/worker/package":
            try:
                package = ensure_worker_package_file()
            except (FileNotFoundError, ValueError):
                self.send_error(404)
                return
            self._send_file(
                package,
                filename=package.name,
                mime_type="application/zip",
                include_body=False,
            )
            return
        if parsed.path == "/android/apk":
            query = parse_qs(parsed.query)
            version_code = query.get("version_code", [""])[0]
            try:
                apk = android_apk_file(version_code)
            except (KeyError, FileNotFoundError):
                self.send_error(404)
                return
            self._send_file(
                apk["path"],
                filename=str(apk["filename"]),
                mime_type="application/vnd.android.package-archive",
                include_body=False,
            )
            return
        self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        requested_workspace = self._requested_workspace_id(parse_qs(parsed.query))
        if path in {"/health", "/android/health"}:
            payload: dict[str, object] = {
                "ok": True,
                "service": "spiritkin-control-plane",
                "production_mode": self._production_mode(),
            }
            self._send_json(payload)
            return
        if path == "/extension/status":
            try:
                binding = self._authorize_extension({})
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            self._send_json(
                {
                    "ok": True,
                    "service": "spiritkin-pdd-browser-extension",
                    "workspace_id": str((binding or {}).get("workspace_id") or DEFAULT_WORKSPACE_ID),
                    "extension_id": str((binding or {}).get("device_id") or "browser_extension"),
                }
            )
            return
        if path in {"/", "/control", "/management"}:
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
                snapshot = self._snapshot(workspace_id or None)
            except PermissionError:
                snapshot = ios_terminal_bootstrap_snapshot(auth_required=True)
            self._send_html(control_home_html(snapshot))
            return
        if path == "/ios/terminal.webmanifest":
            self._send_json(ios_terminal_manifest())
            return
        if path == "/ios/service-worker.js":
            self._send_text(ios_service_worker_js(), content_type="application/javascript; charset=utf-8")
            return
        if path == "/ios/icon.svg":
            self._send_text(ios_icon_svg(), content_type="image/svg+xml; charset=utf-8")
            return
        if path == "/ios/schemas/shortcuts.json":
            from backend.mobile.ios_shortcuts_catalog import SHORTCUT_CATALOG

            self._send_json(
                {
                    "ok": True,
                    "shortcuts": [
                        {
                            "name": item.name,
                            "description": item.description,
                            "icon": item.icon,
                            "color": item.color,
                            "input_schema": item.input_schema,
                            "output_type": item.output_type,
                            "example_usage": item.example_usage,
                            "confirmation_required": item.confirmation_required,
                        }
                        for item in SHORTCUT_CATALOG
                    ],
                }
            )
            return
        if path == "/ios/sessions":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_sessions import ios_sessions_snapshot

            self._send_json(ios_sessions_snapshot(workspace_id=workspace_id, include_unscoped=self._is_management_token() or self._trusted_local_origin()))
            return
        if path == "/ios/capabilities":
            try:
                self._authorized_control_workspace(requested_workspace)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_capabilities import ios_capabilities_snapshot

            self._send_json({"ok": True, **ios_capabilities_snapshot()})
            return
        if path == "/ios/domains":
            try:
                self._authorized_control_workspace(requested_workspace)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_domains import ios_domains_snapshot

            self._send_json({"ok": True, **ios_domains_snapshot()})
            return
        if path == "/ios/pools":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("skills")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_pools import build_ios_pools_snapshot

            self._send_json({"ok": True, **build_ios_pools_snapshot(workspace_id=workspace_id, management=self._is_management_token())})
            return
        if path == "/ios/resources":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("resources")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_resources import build_ios_resources_snapshot

            self._send_json({"ok": True, "resource_management": build_ios_resources_snapshot(workspace_id=workspace_id, management=self._is_management_token())})
            return
        if path == "/ios/monitor":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("monitoring")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_monitoring import build_ios_monitor_snapshot

            self._send_json({"ok": True, "monitor": build_ios_monitor_snapshot(self.store, workspace_id=workspace_id)})
            return
        if path == "/ios/ecommerce":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("workflows")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_ecommerce import build_ios_ecommerce_snapshot

            self._send_json({"ok": True, "ecommerce": build_ios_ecommerce_snapshot(self.store, workspace_id=workspace_id)})
            return
        if path == "/ios/music":
            try:
                workspace_id = self._authorized_owner_workspace(requested_workspace) or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("music")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_music import build_ios_music_snapshot

            self._send_json({"ok": True, "workspace_id": workspace_id, "music": build_ios_music_snapshot()})
            return
        if path == "/ios/channels":
            try:
                self._authorized_owner_workspace(requested_workspace)
                from backend.mobile.ios_capabilities import require_ios_capability

                require_ios_capability("channels")
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_channels import build_ios_channels_snapshot

            self._send_json({"ok": True, "channels": build_ios_channels_snapshot()})
            return
        if path == "/ios/growth":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.capability.growth.runtime import build_growth_snapshot

            self._send_json({"ok": True, "growth": build_growth_snapshot(workspace_id=workspace_id or None, include_unscoped=True)})
            return
        if path == "/ios/runtime-host":
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace) or DEFAULT_WORKSPACE_ID
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_runtime_host import build_ios_runtime_host_snapshot

            self._send_json({"ok": True, "runtime_host": build_ios_runtime_host_snapshot(workspace_id=workspace_id)})
            return
        if path in {"/ios/world", "/ios/observations"}:
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace) or DEFAULT_WORKSPACE_ID
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_world import build_ios_world_snapshot

            self._send_json({"ok": True, "world_state": build_ios_world_snapshot(workspace_id=workspace_id)})
            return
        if path == "/ios/voice-preview":
            try:
                self._authorized_control_workspace(requested_workspace)
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_voice import resolve_ios_voice_preview

                require_ios_capability("voice")
                preview_path, _ = resolve_ios_voice_preview()
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            except (FileNotFoundError, ValueError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_file(preview_path, filename="fairy-preview.wav", mime_type="audio/wav")
            return
        if path.startswith("/ios/jobs/"):
            try:
                workspace_id = self._authorized_control_workspace(requested_workspace)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            from backend.mobile.ios_jobs import ios_job_snapshot

            job = ios_job_snapshot(path.rsplit("/", 1)[-1], workspace_id=workspace_id)
            if job is None:
                self._send_json({"ok": False, "error": "job not found"}, status=404)
                return
            self._send_json({"ok": True, "job": job})
            return
        if path in {"/ios/apple-touch-icon.png", "/ios/icon-192.png", "/ios/icon-512.png"}:
            self._send_text(ios_icon_svg(), content_type="image/svg+xml; charset=utf-8")
            return
        if path in {"/android/apk/manifest", "/android/update"}:
            self._send_json({"ok": True, "apk": android_apk_manifest(self._public_base_url())})
            return
        if path in {"/worker/package/manifest", "/worker/update"}:
            try:
                manifest = worker_package_manifest(self._public_base_url())
            except (FileNotFoundError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._send_json({"ok": True, "worker_package": manifest})
            return
        if path == "/worker/package":
            try:
                package = ensure_worker_package_file()
            except (FileNotFoundError, ValueError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_file(package, filename=package.name, mime_type="application/zip")
            return
        if path == "/android/apk":
            query = parse_qs(parsed.query)
            version_code = query.get("version_code", [""])[0]
            try:
                apk = android_apk_file(version_code)
            except (KeyError, FileNotFoundError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_file(apk["path"], filename=str(apk["filename"]), mime_type="application/vnd.android.package-archive")
            return
        if path == "/android/artifacts":
            query = parse_qs(parsed.query)
            try:
                binding = self._authorize_android({})
                workspace_id = str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID) if binding else query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0]
                device_id = str(binding.get("device_id") or "") if binding else query.get("device_id", [""])[0]
                result = self.store.list_artifact_files(
                    workspace_id=workspace_id,
                    device_id=device_id,
                    source="android_bridge",
                    status="available",
                    limit=int(query.get("limit", ["80"])[0]),
                )
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            if query.get("format", [""])[0].lower() == "lines":
                self._send_text(self._android_artifacts_lines(result))
            else:
                self._send_json(result)
            return
        if path == "/android/links":
            query = parse_qs(parsed.query)
            try:
                binding = self._authorize_android({})
                workspace_id = str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID) if binding else query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0]
                device_id = str(binding.get("device_id") or "") if binding else query.get("device_id", [""])[0]
                result = self.store.list_mobile_links(
                    workspace_id=workspace_id,
                    device_id=device_id,
                    source="android-bridge",
                    status="available",
                    limit=int(query.get("limit", ["80"])[0]),
                )
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            if query.get("format", [""])[0].lower() == "lines":
                self._send_text(self._android_links_lines(result))
            else:
                self._send_json(result)
            return
        if path == "/android/pairing/status":
            query = parse_qs(parsed.query)
            request_id = query.get("request_id", [""])[0]
            workspace_id = query.get("workspace_id", [""])[0] or None
            request_secret = query.get("request_secret", [""])[0]
            if not request_secret and not self._is_loopback_client():
                self._authorize_management()
            try:
                try:
                    pairing = self.store.pairing_request_status(request_id, workspace_id=workspace_id, request_secret=request_secret)
                except TypeError:
                    pairing = self.store.pairing_request_status(request_id, workspace_id=workspace_id)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            except (KeyError, ValueError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            response: dict[str, object] = {
                "ok": True,
                "request_id": pairing.get("request_id") or pairing.get("token_id"),
                "workspace_id": pairing.get("workspace_id"),
                "device_id": pairing.get("device_id") or "",
                "status": pairing.get("status"),
                "expires_at": pairing.get("expires_at") or "",
            }
            if pairing.get("status") == "pending" and pairing.get("token"):
                response["pairing"] = self._pairing_response(pairing)
                response["token"] = pairing.get("token")
                response["receiver_url"] = f"{self._public_base_url()}/android/link"
            self._send_json(response)
            return
        if path in {"/pairing", "/management/pairing", "/android/pairing"}:
            try:
                self._authorize_pairing_endpoint(path)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            query = parse_qs(parsed.query)
            response = self._create_pairing_response(query, requested_by_default="management")
            wants_json = query.get("format", [""])[0].lower() == "json"
            accepts_html = "text/html" in (self.headers.get("accept") or self.headers.get("Accept") or "").lower()
            if accepts_html and not wants_json:
                self._send_html(pairing_html(response))
            else:
                self._send_json({"ok": True, "pairing": response})
            return
        if path == "/ios/control/pairing":
            try:
                query = self._authorized_control_pairing_query(parse_qs(parsed.query))
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            self._send_json({"ok": True, "pairing": self._create_pairing_response(query, requested_by_default="ios_terminal")})
            return
        if path == "/ios/native/snapshot":
            try:
                query = parse_qs(parsed.query)
                workspace_id = self._authorized_control_workspace(query.get("workspace_id", [""])[0]) or DEFAULT_WORKSPACE_ID
                force_refresh = str(query.get("refresh", [""])[0]).lower() in {"1", "true", "yes"}
                from backend.mobile.ios_endpoint import _ios_control_snapshot

                self._send_json(_ios_control_snapshot(force_refresh=force_refresh, workspace_id=workspace_id))
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
            return
        if path.startswith("/android/artifact/") or path.startswith("/mobile/artifacts/"):
            artifact_id = path.rsplit("/", 1)[-1]
            query = parse_qs(parsed.query)
            try:
                file_index = int(query.get("file_index", ["0"])[0])
                workspace_id = self._authorized_artifact_download_workspace(path, query)
                artifact_file = self.store.artifact_file(artifact_id, file_index=file_index, workspace_id=workspace_id)
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            except (KeyError, ValueError, FileNotFoundError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            self._send_file(
                artifact_file["path"],
                filename=str(artifact_file["filename"]),
                mime_type=str(artifact_file["mime_type"]),
            )
            return
        if path in {"/snapshot", "/management/snapshot", "/android/snapshot", "/ios/control/snapshot"}:
            try:
                query = parse_qs(parsed.query)
                workspace_id = query.get("workspace_id", [""])[0] or ""
                account_id = ""
                if path.startswith("/management/") or path.startswith("/ios/"):
                    if path.startswith("/ios/"):
                        account_binding = self._account_console_binding()
                        if account_binding:
                            account_id = str(account_binding.get("account_id") or "")
                            workspace_id = self._account_console_workspace(account_id, workspace_id)
                        else:
                            workspace_id = self._authorized_control_workspace(workspace_id)
                    if path.startswith("/management/"):
                        self._authorize_management()
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            if path.startswith("/ios/") and not account_id:
                terminal_id = query.get("terminal_id", ["ios-web"])[0]
                self.store.register_ios_terminal(terminal_id, self.client_address[0], workspace_id=workspace_id or DEFAULT_WORKSPACE_ID)
            self._send_json(self._snapshot(workspace_id or None, account_id=account_id or None))
            return
        if path in {"/action-log", "/management/action-log", "/ios/control/action-log"}:
            try:
                query = parse_qs(parsed.query)
                workspace_id = query.get("workspace_id", [""])[0] or ""
                if path.startswith("/management/") or path.startswith("/ios/"):
                    workspace_id = self._authorized_control_workspace(workspace_id) if path.startswith("/ios/") else workspace_id
                    if path.startswith("/management/"):
                        self._authorize_management()
            except PermissionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=401)
                return
            self._send_json(
                self.store.action_log(
                    workspace_id=workspace_id or None,
                    action=query.get("action", [""])[0],
                    status=query.get("status", [""])[0],
                    limit=int(query.get("limit", ["50"])[0]),
                )
            )
            return
        if path in {"/ios/terminal", "/ios/control"}:
            try:
                account_binding = self._account_console_binding()
                if account_binding:
                    account_id = str(account_binding.get("account_id") or "")
                    workspace_id = self._account_console_workspace(account_id, "")
                    snapshot = self._snapshot(workspace_id or None, account_id=account_id or None)
                else:
                    workspace_id = self._authorized_control_workspace("")
                    self.store.register_ios_terminal("ios-web", self.client_address[0], workspace_id=workspace_id or DEFAULT_WORKSPACE_ID)
                    snapshot = self._snapshot(workspace_id or None)
            except PermissionError:
                snapshot = ios_terminal_bootstrap_snapshot(auth_required=True)
            self._send_html(ios_terminal_html(snapshot))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/ios/sessions":
                workspace_id = self._authorized_control_workspace(str(payload.get("workspace_id") or ""))
                from backend.mobile.ios_sessions import update_ios_sessions

                self._send_json(update_ios_sessions(payload, workspace_id=workspace_id, include_unscoped=self._is_management_token() or self._trusted_local_origin()))
                return
            if path == "/ios/capabilities":
                self._authorized_control_workspace("")
                from backend.mobile.ios_capabilities import update_ios_capabilities

                self._send_json({"ok": True, **update_ios_capabilities(payload)})
                return
            if path == "/ios/domains":
                self._authorized_control_workspace("")
                from backend.mobile.ios_domains import update_ios_domains

                self._send_json(update_ios_domains(payload))
                return
            if path == "/ios/pools":
                workspace_id = self._authorized_control_workspace("")
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_pools import handle_ios_pool_action

                pool = str(payload.get("pool") or payload.get("kind") or "")
                require_ios_capability("skills" if pool == "skills" else "workflows")
                self._send_json(handle_ios_pool_action(payload, workspace_id=workspace_id, management=self._is_management_token()))
                return
            if path == "/ios/resources":
                workspace_id = self._authorized_control_workspace("")
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_resources import handle_ios_resource_action

                require_ios_capability("resources")
                self._send_json(handle_ios_resource_action(payload, workspace_id=workspace_id, management=self._is_management_token()))
                return
            if path == "/ios/monitor":
                workspace_id = self._authorized_control_workspace("")
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_monitoring import handle_ios_monitor_action

                require_ios_capability("monitoring")
                self._send_json(handle_ios_monitor_action(self.store, payload, workspace_id=workspace_id))
                return
            if path == "/ios/ecommerce":
                workspace_id = self._authorized_control_workspace("")
                workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_ecommerce import handle_ios_ecommerce_action

                require_ios_capability("workflows")
                self._send_json(handle_ios_ecommerce_action(self.store, payload, workspace_id=workspace_id))
                return
            if path == "/ios/music":
                workspace_id = self._authorized_owner_workspace("") or DEFAULT_WORKSPACE_ID
                from backend.mobile.ios_capabilities import require_ios_capability
                from backend.mobile.ios_music import handle_ios_music_action

                require_ios_capability("music")
                self._send_json(handle_ios_music_action(self.store, payload, workspace_id=workspace_id))
                return
            if path == "/ios/growth":
                try:
                    self._send_json(self._handle_ios_growth_action(payload))
                except PermissionError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=403)
                except (KeyError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                return
            if path == "/ios/runtime-host":
                workspace_id = self._authorized_control_workspace("") or DEFAULT_WORKSPACE_ID
                binding = self._control_terminal_binding() or {}
                actor_id = str(binding.get("terminal_id") or binding.get("device_id") or ("management" if self._is_management_token() else "ios-controller"))
                from backend.mobile.ios_runtime_host import handle_ios_runtime_host_action

                self._send_json(
                    handle_ios_runtime_host_action(
                        payload,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        management=self._is_management_token(),
                    )
                )
                return
            if path in {"/ios/world", "/ios/observations"}:
                workspace_id = self._authorized_control_workspace("") or DEFAULT_WORKSPACE_ID
                binding = self._control_terminal_binding() or {}
                actor_id = str(binding.get("terminal_id") or binding.get("device_id") or ("management" if self._is_management_token() else "ios-controller"))
                from backend.mobile.ios_world import handle_ios_world_action

                world_payload = dict(payload)
                if path == "/ios/observations":
                    world_payload["action"] = "publish_observation"
                self._send_json(
                    handle_ios_world_action(
                        world_payload,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        management=self._is_management_token(),
                    )
                )
                return
            if path in {"/ios/shortcut", "/ios/intent"}:
                self._handle_ios_shortcut(path, payload)
                return
            if path in {"/link", "/android/link"}:
                payload = self._with_android_binding(payload)
                self._handle_link(payload)
                return
            if path == "/android/pair":
                binding = self.store.bind_device(payload, client=self.client_address[0], required_role="android_bridge")
                self._send_json(
                    {
                        "ok": True,
                        "binding": {
                            "device_id": binding["device_id"],
                            "workspace_id": binding["workspace_id"],
                            "device_role": binding["device_role"],
                            "receiver_url": f"{self._public_base_url()}/android/link",
                        },
                    }
                )
                return
            if path == "/extension/pair":
                binding = self.store.bind_device(payload, client=self.client_address[0], required_role="browser_extension")
                self._send_json(
                    {
                        "ok": True,
                        "binding": {
                            "extension_id": binding.get("device_id") or "browser_extension",
                            "workspace_id": binding["workspace_id"],
                            "device_role": binding["device_role"],
                            "server_url": self._public_base_url(),
                            "token": binding["token"],
                        },
                    }
                )
                return
            if path == "/android/pairing/request":
                workspace_id = normalize_workspace_id(str(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID))
                device_id = str(payload.get("device_id") or "").strip()
                pairing = self.store.create_pairing_request(
                    workspace_id=workspace_id,
                    device_id=device_id,
                    device_role="android_bridge",
                    requested_by=str(payload.get("requested_by") or "android_bridge"),
                    server_url=self._public_base_url(),
                    device_state=payload.get("device_state") if isinstance(payload.get("device_state"), dict) else {},
                )
                self._send_json(
                    {
                        "ok": True,
                        "status": "requested",
                        "request_id": pairing["request_id"],
                        "workspace_id": pairing["workspace_id"],
                        "device_id": pairing.get("device_id") or device_id,
                        "message": "binding request is waiting for controller approval",
                        "request_secret": pairing.get("request_secret") or "",
                    }
                )
                return
            if path == "/android/unpair":
                binding = self._authorize_android(payload)
                if not binding:
                    self._send_json({"ok": False, "error": "android pairing token required"}, status=401)
                    return
                result = self.store.revoke_device_binding(
                    str(binding.get("token_id") or ""),
                    workspace_id=str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                    requested_by=str(binding.get("device_id") or "android_bridge"),
                )
                self._send_json({"ok": True, "binding": result.get("binding") or {}})
                return
            if path == "/worker/pair":
                binding = self.store.bind_device(payload, client=self.client_address[0], required_role="remote_worker")
                self._send_json(
                    {
                        "ok": True,
                        "binding": {
                            "worker_id": binding.get("worker_id") or binding["device_id"],
                            "workspace_id": binding["workspace_id"],
                            "device_role": binding["device_role"],
                            "server_url": self._public_base_url(),
                        },
                    }
                )
                return
            if path == "/ios/control/pair":
                binding = self.store.bind_device(payload, client=self.client_address[0], required_role="ios_terminal")
                self._send_json(
                    {
                        "ok": True,
                        "binding": {
                            "terminal_id": binding.get("terminal_id") or binding["device_id"],
                            "workspace_id": binding["workspace_id"],
                            "device_role": binding["device_role"],
                            "server_url": self._public_base_url(),
                            "token": binding["token"],
                        },
                    }
                )
                return
            if path == "/ios/heartbeat":
                workspace_id = self._authorized_owner_workspace(str(payload.get("workspace_id") or "")) or DEFAULT_WORKSPACE_ID
                binding = self._control_terminal_binding() or {}
                terminal_id = str(
                    binding.get("terminal_id")
                    or binding.get("device_id")
                    or payload.get("terminal_id")
                    or "ios-web"
                )
                terminal = self.store.register_ios_terminal(
                    terminal_id,
                    self.client_address[0],
                    workspace_id=workspace_id,
                )
                self._send_json({"ok": True, "terminal": terminal})
                return
            if path == "/android/heartbeat":
                payload = self._with_android_binding(payload)
                self._send_json(self.store.android_heartbeat(payload, client=self.client_address[0]))
                return
            if path == "/android/command":
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                params = self._with_artifact_download_url(params)
                workspace_id = self._authorized_android_command_workspace(payload)
                self._send_json(
                    {
                        "ok": True,
                        "command": self.store.queue_android_command(
                            operation=str(payload.get("operation") or ""),
                            params=params,
                            device_id=str(payload.get("device_id") or "*"),
                            workspace_id=workspace_id,
                            requested_by=str(payload.get("requested_by") or "api"),
                        ),
                    }
                )
                return
            if path in {"/android/artifact", "/mobile/artifacts"}:
                payload = self._authorized_artifact_upload_payload(path, payload)
                artifact = self.store.record_artifact(payload, client=self.client_address[0], default_source="android_bridge")
                artifact = self._with_artifact_urls(artifact)
                self._send_json({"ok": True, "artifact": artifact, "artifacts": [artifact]})
                return
            if path == "/android/artifacts/delete-file":
                binding = self._authorize_android(payload)
                if not binding:
                    raise PermissionError("android pairing token required")
                result = self.store.delete_artifact_file(
                    str(payload.get("artifact_id") or ""),
                    file_index=int(payload.get("file_index") or 0),
                    workspace_id=str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                    device_id=str(binding.get("device_id") or ""),
                    requested_by=str(binding.get("device_id") or "android_bridge"),
                )
                self._send_json({"ok": True, "result": result})
                return
            if path == "/android/links/delete":
                binding = self._authorize_android(payload)
                if not binding:
                    raise PermissionError("android pairing token required")
                result = self.store.delete_mobile_link(
                    str(payload.get("link_id") or ""),
                    workspace_id=str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                    device_id=str(binding.get("device_id") or ""),
                    requested_by=str(binding.get("device_id") or "android_bridge"),
                )
                self._send_json({"ok": True, "result": result})
                return
            if path == "/extension/links/claim":
                binding = self._authorize_extension(payload)
                workspace_id = str((binding or {}).get("workspace_id") or payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
                extension_id = str((binding or {}).get("device_id") or payload.get("extension_id") or "browser_extension")
                result = self.store.claim_mobile_links_for_extension(
                    workspace_id=workspace_id,
                    extension_id=extension_id,
                    limit=int(payload.get("limit") or 10),
                )
                self._send_json(result)
                return
            if path == "/extension/results":
                binding = self._authorize_extension(payload)
                workspace_id = str((binding or {}).get("workspace_id") or payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
                extension_id = str((binding or {}).get("device_id") or payload.get("extension_id") or "browser_extension")
                link_id = str(payload.get("link_id") or "").strip()
                link_record = self.store.mobile_link(link_id, workspace_id=workspace_id)
                success = bool(payload.get("success"))
                artifact_id = ""
                ecommerce_task: dict[str, object] = {}
                product_data = payload.get("product_data")
                if success:
                    if not isinstance(product_data, dict):
                        raise ValueError("product_data is required for a successful extraction")
                    from scripts.ecommerce_task_queue import attach_productdata_artifact, ensure_mobile_link_task

                    ecommerce_state_dir = self.store.state_dir.parent / "ecommerce_tasks"
                    ensured = ensure_mobile_link_task(
                        str(link_record.get("link") or ""),
                        receiver_event=link_record,
                        state_dir=ecommerce_state_dir,
                    )
                    task = ensured.get("task") if isinstance(ensured.get("task"), dict) else {}
                    task_id = str(task.get("id") or "")
                    goods_id = safe_name(product_data.get("goodsId") or product_data.get("goods_id") or link_id, "product")
                    artifact = self.store.record_artifact(
                        {
                            "workspace_id": workspace_id,
                            "source": "browser_extension",
                            "device_id": extension_id,
                            "task_id": task_id,
                            "purpose": "pdd_product_data",
                            "tags": ["pdd", "product_data", "browser_extension"],
                            "files": [
                                {
                                    "name": f"pdd-product-{goods_id}.json",
                                    "mime_type": "application/json",
                                    "text": json.dumps(product_data, ensure_ascii=False, indent=2),
                                }
                            ],
                        },
                        client=self.client_address[0],
                        default_source="browser_extension",
                    )
                    artifact_id = str(artifact.get("artifact_id") or "")
                    artifact_file = self.store.artifact_file(artifact_id, workspace_id=workspace_id)
                    ecommerce_task = attach_productdata_artifact(
                        task_id,
                        product_data_json=artifact_file["path"],
                        control_plane_artifact_id=artifact_id,
                        state_dir=ecommerce_state_dir,
                    )
                result = self.store.record_mobile_link_extraction_result(
                    link_id,
                    workspace_id=workspace_id,
                    extension_id=extension_id,
                    success=success,
                    artifact_id=artifact_id,
                    summary=payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
                    error=str(payload.get("error") or ""),
                )
                self._send_json({"ok": True, "artifact_id": artifact_id, "ecommerce_task": ecommerce_task, "result": result})
                return
            if path == "/extension/links/requeue":
                binding = self._authorize_extension(payload)
                workspace_id = str((binding or {}).get("workspace_id") or payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
                extension_id = str((binding or {}).get("device_id") or payload.get("extension_id") or "browser_extension")
                result = self.store.requeue_mobile_link_for_extension(
                    str(payload.get("link_id") or ""),
                    workspace_id=workspace_id,
                    requested_by=extension_id,
                )
                self._send_json(result)
                return
            if path == "/worker/heartbeat":
                payload = self._with_worker_binding(payload)
                result = self.store.worker_heartbeat(payload, client=self.client_address[0])
                from backend.orchestrator.runtime_host import RuntimeHostRegistry

                registry = RuntimeHostRegistry(ROOT / "state" / "runtime" / "hosts.json")
                runtime_host_id = f"remote:{result['worker_id']}"
                runtime_host = registry.register_host(
                    host_id=runtime_host_id,
                    workspace_id=str(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                    host_type="remote",
                    label=str(payload.get("label") or result["worker_id"]),
                    capabilities=payload.get("capabilities") or [],
                    can_execute_workflows=False,
                    can_observe="observation.publish" in (payload.get("capabilities") or []),
                    priority=-20,
                    requested_by=str(result["worker_id"]),
                )
                result["runtime_host"] = runtime_host
                self._send_json(result)
                return
            if path == "/worker/result":
                payload = self._with_worker_binding(payload)
                self._send_json(self.store.worker_result(payload, client=self.client_address[0]))
                return
            if path == "/management/action":
                self._authorize_management()
                payload = dict(payload)
                payload["actor_role"] = "management"
                if str(payload.get("action") or "") == "queue_android_command":
                    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                    payload["params"] = self._with_artifact_download_url(params)
                self._send_json({"ok": True, "result": self.store.management_action(payload, client=self.client_address[0])})
                return
            if path == "/ios/control/action":
                payload = self._authorized_control_action_payload(payload)
                self._require_ios_capability_for_action(str(payload.get("action") or ""))
                if str(payload.get("action") or "") == "queue_android_command":
                    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                    payload = dict(payload)
                    payload["params"] = self._with_artifact_download_url(params)
                self._send_json({"ok": True, "result": self.store.management_action(payload, client=self.client_address[0])})
                return
            if path == "/ios/native/action":
                payload = self._authorized_control_action_payload(payload)
                self._require_ios_capability_for_action(str(payload.get("action") or ""))
                workspace_id = str(payload.get("workspace_id") or "").strip() or DEFAULT_WORKSPACE_ID
                binding = self._control_terminal_binding() or {}
                actor = str(binding.get("terminal_id") or binding.get("device_id") or "ios_terminal")
                from backend.mobile.ios_endpoint import _ios_control_action

                try:
                    status, response = _ios_control_action(payload, workspace_id=workspace_id, actor=actor)
                except PermissionError as exc:
                    self._send_json({"ok": False, "error": "forbidden", "detail": str(exc)}, status=403)
                    return
                self._send_json(response, status=status)
                return
            self.send_error(404)
        except (KeyError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"ok": False, "error": exc.__class__.__name__}, status=500)

    def _handle_ios_shortcut(self, path: str, payload: dict[str, object]) -> None:
        workspace_id = self._authorized_control_workspace(str(payload.get("workspace_id") or ""))
        action = str(payload.get("action") or payload.get("intent_name") or "ask_spirit").strip() or "ask_spirit"
        from backend.mobile.ios_capabilities import require_ios_capability

        require_ios_capability("shortcuts")
        if action in {"ask_spirit", "askSpirit"}:
            require_ios_capability("conversations")
        elif action.startswith(("device.", "android.", "app.", "clipboard.", "pdd.", "url.")):
            require_ios_capability("devices")
        from backend.mobile.ios_bridge import validate_ios_action

        allowed, reason = validate_ios_action(action)
        if not allowed:
            self._send_json({"ok": False, "error": "forbidden", "reason": reason}, status=403)
            return

        text = str(payload.get("text") or payload.get("input_text") or "").strip()
        if not text:
            self._send_json({"ok": False, "error": "missing text"}, status=400)
            return

        metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
        metadata.update(
            {
                "shortcut_name": str(payload.get("shortcut_name") or "Ask Spirit"),
                "ios_action": action,
                "ios_route": path,
                "workspace_id": workspace_id or DEFAULT_WORKSPACE_ID,
            }
        )
        metadata.setdefault("text_mode", "fast")
        metadata.setdefault("max_new_tokens", 96)
        metadata.setdefault("reasoning_effort", "none")
        metadata.setdefault("model_timeout_seconds", 90)
        from backend.app.runtime import InteractionInput, SpiritKinRuntime
        from backend.mobile.ios_conversation import handle_ios_direct_chat, should_use_ios_direct_chat

        def execute() -> dict[str, object]:
            if action in {"ask_spirit", "askSpirit"} and should_use_ios_direct_chat(text, metadata):
                reply = handle_ios_direct_chat(text, metadata)
            else:
                runtime = self._ios_runtime()
                reply = runtime.handle_input(InteractionInput(text=text, channel="ios", metadata=metadata))
            if reply is None:
                return {"ok": True, "reply": None, "shortcut_output": {"result": "", "emotion": "neutral"}}
            return {
                "ok": True,
                "reply": SpiritKinRuntime.build_output_payload(reply),
                "shortcut_output": {"result": reply.text, "emotion": reply.emotion},
            }

        if bool(payload.get("async") or payload.get("prefer_async")):
            from backend.mobile.ios_jobs import submit_ios_job

            job = submit_ios_job(execute, workspace_id=workspace_id or DEFAULT_WORKSPACE_ID)
            self._send_json(
                {
                    "ok": True,
                    "accepted": True,
                    "job_id": job["job_id"],
                    "status": job["status"],
                    "poll_after_ms": 600,
                },
                status=202,
            )
            return

        try:
            result = execute()
        except Exception as exc:
            timed_out = "timed out" in str(exc).lower() or "timeout" in str(exc).lower()
            self._send_json(
                {
                    "ok": False,
                    "error": "model_timeout" if timed_out else "runtime_unavailable",
                    "message": "主模型响应超时，请稍后重试。" if timed_out else "主运行时暂不可用，请检查模型服务。",
                    "retryable": True,
                },
                status=503,
            )
            return
        self._send_json(result)

    def _require_ios_capability_for_action(self, action: str) -> None:
        from backend.mobile.ios_capabilities import require_ios_capability

        if action.startswith(("workflow", "start_run", "run_", "compose", "upsert", "save_builtin", "assign_agent", "claim_agent", "complete_agent", "approve_review", "signal_node", "retry_node", "reset_run", "archive_run", "delete_run", "delete_definition", "rollback_definition")):
            require_ios_capability("workflows")
        elif action in {"enqueue_android_command", "clear_android_commands", "start_android_endpoint", "restart_android_endpoint"}:
            require_ios_capability("devices")
        elif action in {"ingest_mobile_artifacts", "cleanup_mobile_artifacts"}:
            require_ios_capability("artifacts")
        elif action in {"panic_stop", "resume", "hard_stop"}:
            require_ios_capability("safety")

    def _handle_ios_growth_action(self, source: dict[str, object]) -> dict[str, object]:
        from backend.capability.growth.runtime import handle_growth_action
        from backend.mobile.ios_capabilities import require_ios_capability

        payload = dict(source)
        requested_workspace = str(payload.get("workspace_id") or "").strip()
        workspace_id = self._authorized_control_workspace(requested_workspace)
        management = self._is_management_token()
        workspace_id = workspace_id or ("" if management else DEFAULT_WORKSPACE_ID)
        require_ios_capability("growth_governance")
        action = str(payload.get("action") or "snapshot").strip().lower()
        binding = self._control_terminal_binding()
        actor = (
            f"ios-terminal:{binding.get('device_id')}"
            if binding and binding.get("device_id")
            else ("management-console" if management else "local-ios-controller")
        )
        if action in {
            "research_candidate",
            "remote_research",
            "prepare_sandbox_bundle",
            "sandbox_bundle_prepare",
            "execute_builder_sandbox",
            "sandbox_execute",
            "verify_builder_artifact",
            "builder_verify",
            "record_candidate_benchmark",
            "benchmark_candidate",
            "run_model_jury",
            "model_jury",
            "review_candidate",
            "register_candidate",
            "escalate_candidate",
            "route_candidate",
        }:
            if payload.get("confirmed") is not True:
                raise PermissionError("explicit confirmation is required for growth governance actions")
            if action in {"research_candidate", "remote_research"}:
                payload["researched_by"] = actor
            elif action in {"prepare_sandbox_bundle", "sandbox_bundle_prepare"}:
                payload["prepared_by"] = actor
            elif action in {"execute_builder_sandbox", "sandbox_execute"}:
                payload["executed_by"] = actor
            elif action in {"verify_builder_artifact", "builder_verify"}:
                payload["verified_by"] = actor
            elif action in {"record_candidate_benchmark", "benchmark_candidate"}:
                payload["recorded_by"] = actor
            elif action in {"run_model_jury", "model_jury"}:
                payload["requested_by"] = actor
            elif action == "review_candidate":
                payload["reviewer"] = actor
            elif action == "register_candidate":
                payload["registered_by"] = actor
            else:
                payload["requested_by"] = actor
        if action in {"advance_stage", "record_stage_evidence"}:
            payload["submitted_by"] = actor
        payload["workspace_id"] = workspace_id
        payload["allow_unscoped_governance"] = management
        return handle_growth_action(payload)

    @classmethod
    def _ios_runtime(cls):
        os.environ.setdefault(
            "SPIRITKIN_LLM_REQUEST_TIMEOUT",
            os.getenv("SPIRITKIN_IOS_SHORTCUT_LLM_TIMEOUT", str(DEFAULT_IOS_SHORTCUT_LLM_TIMEOUT_SECONDS)),
        )
        with cls.ios_runtime_lock:
            if cls.ios_runtime is None:
                from backend.app.runtime import SpiritKinRuntime

                cls.ios_runtime = SpiritKinRuntime(emit_runtime_events=True)
            return cls.ios_runtime

    def _handle_link(self, payload: dict[str, object]) -> None:
        link = extract_pdd_link(payload.get("link") or payload.get("text") or "")
        if not is_supported_pdd_link(link):
            self._send_json({"ok": False, "error": "missing pdd link"}, status=400)
            return
        event = {
            "link": link,
            "source": payload.get("source") or "android-bridge",
            "receivedAt": datetime.now(UTC).isoformat(),
            "client": self.client_address[0],
            "workspace_id": payload.get("workspace_id") or DEFAULT_WORKSPACE_ID,
        }
        write_legacy_mobile_link(event)
        control_event = self.store.record_mobile_link(
            link,
            source=str(event["source"]),
            client=self.client_address[0],
            workspace_id=str(event["workspace_id"]),
            device_id=str(payload.get("device_id") or ""),
        )
        print(json.dumps(event, ensure_ascii=False), flush=True)
        self._send_json(
            {
                "ok": True,
                "link": control_event,
                "link_id": control_event.get("link_id"),
                "workspace_id": control_event.get("workspace_id"),
                "stored_at": {
                    "control_state": str(getattr(self.store, "state_file", "")),
                    "legacy_queue": str(QUEUE),
                    "legacy_latest": str(LATEST),
                },
            }
        )

    def _read_json(self) -> dict[str, object]:
        try:
            size = int(self.headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content-length") from exc
        if size <= 0 or size > MAX_BODY_BYTES:
            raise ValueError("invalid body size")
        body = self.rfile.read(size).decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid json") from exc
        if not isinstance(payload, dict):
            raise ValueError("json body must be an object")
        return payload

    def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, *, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, *, filename: str, mime_type: str, include_body: bool = True) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", mime_type)
        self.send_header("content-length", str(len(data)))
        self.send_header("content-disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def _pairing_response(self, pairing: dict[str, object]) -> dict[str, object]:
        role = str(pairing.get("device_role") or "android_bridge")
        base_url = self._public_base_url()
        receiver_url = f"{base_url}/android/link" if role == "android_bridge" else base_url
        deep_link = "spiritkin://pair?" + urlencode(
            {
                "server_url": receiver_url,
                "workspace_id": str(pairing.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                "pairing_token": str(pairing.get("token") or ""),
                "device_role": role,
            }
        )
        response = {
            "token_id": pairing.get("token_id"),
            "workspace_id": pairing.get("workspace_id"),
            "device_role": role,
            "expires_at": pairing.get("expires_at"),
            "server_url": receiver_url,
            "pairing_token": pairing.get("token"),
            "deep_link": deep_link,
            "qr_png_data_url": self._qr_data_url(deep_link),
        }
        if role == "remote_worker":
            response["package_manifest_url"] = f"{base_url}/worker/package/manifest"
            response["package_download_url"] = f"{base_url}/worker/package"
            response["setup_command"] = (
                "powershell -ExecutionPolicy Bypass -File .\\setup-worker.ps1 "
                f"-ServerUrl {base_url} "
                f"-WorkspaceId {pairing.get('workspace_id') or DEFAULT_WORKSPACE_ID} "
                "-WorkerId <worker-id> "
                f"-PairingToken {pairing.get('token') or ''}"
            )
            response["gui_install_command"] = (
                "powershell -ExecutionPolicy Bypass -File .\\install-worker-gui.ps1 "
                f"-ServerUrl {base_url} "
                f"-WorkspaceId {pairing.get('workspace_id') or DEFAULT_WORKSPACE_ID} "
                "-WorkerId <worker-id> "
                f"-PairingToken {pairing.get('token') or ''}"
            )
            response["pairing_command"] = (
                "python scripts\\control_plane_worker.py "
                f"--server {base_url} "
                f"--workspace-id {pairing.get('workspace_id') or DEFAULT_WORKSPACE_ID} "
                "--worker-id <worker-id> "
                f"--pairing-token {pairing.get('token') or ''} "
                "--prepare-runtime --allow-cli"
            )
        return response

    def _create_pairing_response(self, query: dict[str, list[str]], *, requested_by_default: str) -> dict[str, object]:
        pairing = self.store.create_pairing_token(
            workspace_id=query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0],
            account_id=query.get("account_id", [DEFAULT_ACCOUNT_ID])[0],
            device_role=query.get("device_role", ["android_bridge"])[0],
            requested_by=query.get("requested_by", [requested_by_default])[0],
            server_url=self._public_base_url(),
            ttl_minutes=int(query.get("ttl_minutes", ["30"])[0]),
        )
        return self._pairing_response(pairing)

    def _qr_data_url(self, value: str) -> str:
        try:
            import qrcode
        except ModuleNotFoundError:
            return ""
        img = qrcode.make(value)
        out = BytesIO()
        img.save(out, format="PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")

    def _with_artifact_download_url(self, params: dict[str, object]) -> dict[str, object]:
        next_params = dict(params)
        artifact_id = str(next_params.get("artifact_id") or "").strip()
        if artifact_id and not next_params.get("download_url"):
            file_index = int(next_params.get("file_index") or 0)
            next_params["download_url"] = f"{self._public_base_url()}/android/artifact/{artifact_id}?file_index={file_index}"
        return next_params

    def _with_artifact_urls(self, artifact: dict[str, object]) -> dict[str, object]:
        next_artifact = dict(artifact)
        artifact_id = str(next_artifact.get("artifact_id") or "").strip()
        if artifact_id and not next_artifact.get("download_url"):
            next_artifact["download_url"] = f"{self._public_base_url()}/android/artifact/{artifact_id}?file_index=0"
        return next_artifact

    def _android_artifacts_lines(self, result: dict[str, object]) -> str:
        lines = [
            "# artifact_id\tfile_index\tname\tmime_type\tsize_bytes\tcreated_at\tpurpose",
        ]
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            files = artifact.get("files") if isinstance(artifact.get("files"), list) else []
            for file_meta in files:
                if not isinstance(file_meta, dict):
                    continue
                fields = [
                    artifact.get("artifact_id") or "",
                    file_meta.get("file_index") if file_meta.get("file_index") is not None else 0,
                    file_meta.get("name") or "",
                    file_meta.get("mime_type") or "",
                    file_meta.get("size_bytes") or 0,
                    artifact.get("created_at") or "",
                    artifact.get("purpose") or "",
                ]
                lines.append("\t".join(self._line_field(item) for item in fields))
        return "\n".join(lines) + "\n"

    def _android_links_lines(self, result: dict[str, object]) -> str:
        lines = [
            "# link_id\tlink\treceived_at\tsource",
        ]
        links = result.get("links") if isinstance(result.get("links"), list) else []
        for item in links:
            if not isinstance(item, dict):
                continue
            fields = [
                item.get("link_id") or "",
                item.get("link") or "",
                item.get("received_at") or "",
                item.get("source") or "",
            ]
            lines.append("\t".join(self._line_field(field) for field in fields))
        return "\n".join(lines) + "\n"

    def _line_field(self, value: object) -> str:
        return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

    def _with_android_binding(self, payload: dict[str, object]) -> dict[str, object]:
        binding = self._authorize_android(payload)
        if not binding:
            return payload
        next_payload = dict(payload)
        next_payload["token"] = self._auth_token(payload)
        next_payload["workspace_id"] = binding["workspace_id"]
        next_payload["device_id"] = binding["device_id"]
        return next_payload

    def _with_worker_binding(self, payload: dict[str, object]) -> dict[str, object]:
        binding = self._authorize_worker(payload)
        if not binding:
            return payload
        next_payload = dict(payload)
        next_payload["token"] = self._auth_token(payload)
        next_payload["workspace_id"] = binding["workspace_id"]
        next_payload["worker_id"] = binding.get("worker_id") or binding.get("device_id")
        return next_payload

    def _snapshot(self, workspace_id: str | None = None, *, account_id: str | None = None) -> dict[str, object]:
        try:
            return self.store.snapshot(workspace_id=workspace_id, account_id=account_id)
        except TypeError:
            try:
                return self.store.snapshot(workspace_id=workspace_id)
            except TypeError:
                return self.store.snapshot()

    def _authorized_artifact_upload_payload(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        if path.startswith("/android/"):
            return self._with_android_binding(payload)
        workspace_id = self._authorized_control_workspace(str(payload.get("workspace_id") or ""))
        if not workspace_id:
            return payload
        next_payload = dict(payload)
        next_payload["workspace_id"] = workspace_id
        return next_payload

    def _authorized_artifact_download_workspace(self, path: str, query: dict[str, list[str]]) -> str | None:
        requested = str(query.get("workspace_id", [""])[0] or "").strip()
        if path.startswith("/android/"):
            binding = self._authorize_android({})
            if not binding:
                return requested or None
            workspace_id = str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
            if requested and requested != workspace_id:
                raise PermissionError("android token cannot read artifacts outside its workspace")
            return workspace_id
        workspace_id = self._authorized_control_workspace(requested)
        return workspace_id or None

    def _authorized_control_pairing_query(self, query: dict[str, list[str]]) -> dict[str, list[str]]:
        device_role = str(query.get("device_role", ["android_bridge"])[0] or "android_bridge")
        account_binding = self._account_console_binding()
        if account_binding:
            if device_role != "remote_worker":
                raise PermissionError("account console token can only create remote worker pairing")
            account_id = str(account_binding.get("account_id") or "")
            workspace_id = self._account_console_workspace(account_id, query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0])
            next_query = dict(query)
            next_query["workspace_id"] = [workspace_id or DEFAULT_WORKSPACE_ID]
            next_query["account_id"] = [account_id]
            next_query["device_role"] = ["remote_worker"]
            next_query["requested_by"] = [str(account_binding.get("console_id") or account_binding.get("device_id") or "account_console")]
            return next_query
        try:
            self._authorize_management()
        except PermissionError:
            if device_role == "ios_terminal":
                raise PermissionError("management token required for iOS terminal pairing") from None
            workspace_id = self._authorized_control_workspace(query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0])
            binding = self._control_terminal_binding()
            next_query = dict(query)
            next_query["workspace_id"] = [workspace_id or DEFAULT_WORKSPACE_ID]
            if binding:
                next_query["requested_by"] = [str(binding.get("terminal_id") or binding.get("device_id") or "ios_terminal")]
            return next_query
        if device_role != "ios_terminal":
            return query
        next_query = dict(query)
        next_query["requested_by"] = ["management"]
        return next_query

    def _authorized_control_action_payload(self, payload: dict[str, object]) -> dict[str, object]:
        if self._is_account_console_token():
            return self._authorized_account_console_payload(payload)
        workspace_id = self._authorized_control_workspace(str(payload.get("workspace_id") or ""))
        action = str(payload.get("action") or "")
        if not workspace_id:
            if self._is_management_token():
                next_payload = dict(payload)
                next_payload["actor_role"] = "management"
                return next_payload
            return payload
        if action in {"snapshot", "action_log", "validate_state"} or payload.get("workspace_id") or self._is_ios_terminal_token():
            next_payload = dict(payload)
            next_payload["workspace_id"] = workspace_id
            next_payload["actor_role"] = "ios_terminal" if self._is_ios_terminal_token() else "management"
            if self._is_ios_terminal_token() and not next_payload.get("requested_by"):
                next_payload["requested_by"] = str(self._control_terminal_binding().get("terminal_id") or "ios_terminal")
            return next_payload
        return payload

    def _authorized_account_console_payload(self, payload: dict[str, object]) -> dict[str, object]:
        binding = self._account_console_binding()
        if not binding:
            raise PermissionError("account console token required")
        account_id = str(binding.get("account_id") or "").strip()
        if not account_id:
            raise PermissionError("account console token is missing account scope")
        action = str(payload.get("action") or "").strip()
        if action.startswith("workflow.graph."):
            raise PermissionError("account console cannot call workflow graph actions before control-plane metering bridge is available")
        allowed_actions = {
            "snapshot",
            "action_log",
            "validate_state",
            "get_account_usage",
            "register_workspace",
            "start_workflow_run",
            "cancel_workflow_run",
            "retry_workflow_run",
            "delete_workflow_run",
            "clear_workflow_runs",
        }
        if action not in allowed_actions:
            raise PermissionError(f"account console action is not allowed: {action}")
        requested_workspace = str(payload.get("workspace_id") or "").strip()
        workspace_id = self._account_console_workspace(account_id, requested_workspace)
        next_payload = dict(payload)
        next_payload["account_id"] = account_id
        next_payload["actor_role"] = "account_console"
        next_payload["requested_by"] = str(binding.get("console_id") or binding.get("device_id") or "account_console")
        if workspace_id:
            next_payload["workspace_id"] = workspace_id
        return next_payload

    def _requested_workspace_id(self, query: dict[str, list[str]] | None = None) -> str:
        query_value = str((query or {}).get("workspace_id", [""])[0] or "").strip()
        header_value = str(
            self.headers.get("x-spiritkin-workspace")
            or self.headers.get("X-SpiritKin-Workspace")
            or self.headers.get("x-spiritkin-workspace-id")
            or self.headers.get("X-SpiritKin-Workspace-ID")
            or ""
        ).strip()
        return query_value or header_value

    def _authorized_control_workspace(self, requested: str | None) -> str:
        requested_id = normalize_workspace_id(requested) if requested else ""
        expected = self._management_token()
        token = self._auth_token({})
        if expected and token and secrets_compare(token, expected):
            return requested_id
        binding = self._control_terminal_binding()
        if binding:
            workspace_id = normalize_workspace_id(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
            if requested_id and requested_id != workspace_id:
                raise PermissionError("iOS terminal token cannot access another workspace")
            return workspace_id
        account_binding = self._account_console_binding()
        if account_binding:
            return self._account_console_workspace(str(account_binding.get("account_id") or ""), requested_id)
        if not expected and not self._production_mode() and self._trusted_local_origin():
            return requested_id
        raise PermissionError("management or iOS terminal token required")

    def _authorized_owner_workspace(self, requested: str | None) -> str:
        if self._is_account_console_token():
            raise PermissionError("owner controller token required")
        return self._authorized_control_workspace(requested)

    def _control_terminal_binding(self) -> dict[str, object] | None:
        token = self._auth_token({})
        if not token:
            return None
        if self._management_token() and secrets_compare(token, self._management_token()):
            return None
        return self._binding_from_token(token, required_role="ios_terminal")

    def _account_console_binding(self) -> dict[str, object] | None:
        token = self._auth_token({})
        if not token:
            return None
        if self._management_token() and secrets_compare(token, self._management_token()):
            return None
        try:
            binding = self._binding_from_token(token, required_role="account_console")
        except PermissionError:
            return None
        if str(binding.get("device_role") or "") != "account_console":
            return None
        if not str(binding.get("account_id") or "").strip():
            return None
        return binding

    def _account_console_workspace(self, account_id: str, requested: str | None) -> str:
        requested_id = normalize_workspace_id(requested) if requested else ""
        snapshot = self.store.snapshot()
        accounts = snapshot.get("accounts") if isinstance(snapshot.get("accounts"), dict) else {}
        records = accounts.get("items") if isinstance(accounts.get("items"), list) else []
        workspace_ids: set[str] = set()
        for account in records:
            if not isinstance(account, dict):
                continue
            if str(account.get("account_id") or "") != account_id:
                continue
            workspace_ids = {normalize_workspace_id(item) for item in account.get("workspace_ids") or [] if str(item).strip()}
            break
        if requested_id:
            if requested_id not in workspace_ids:
                raise PermissionError("account console token cannot access another account workspace")
            return requested_id
        return sorted(workspace_ids)[0] if workspace_ids else ""

    def _is_management_token(self) -> bool:
        token = self._auth_token({})
        expected = self._management_token()
        return bool(expected and token and secrets_compare(token, expected))

    def _is_ios_terminal_token(self) -> bool:
        token = self._auth_token({})
        expected = self._management_token()
        if not token or (expected and secrets_compare(token, expected)):
            return False
        try:
            return bool(self._binding_from_token(token, required_role="ios_terminal"))
        except PermissionError:
            return False

    def _is_account_console_token(self) -> bool:
        return bool(self._account_console_binding())

    def _authorize_pairing_endpoint(self, path: str) -> None:
        if path.startswith("/management/") or self._management_token() or self._production_mode():
            self._authorize_management()

    def _authorize_android(self, payload: dict[str, object]) -> dict[str, object] | None:
        token = self._auth_token(payload)
        if token:
            return self._binding_from_token(token)
        if self._trusted_local_origin() and not self._pairing_required():
            return None
        raise PermissionError("android pairing token required")

    def _authorize_worker(self, payload: dict[str, object]) -> dict[str, object] | None:
        token = self._auth_token(payload)
        if token and self._management_token() and secrets_compare(token, self._management_token()):
            return None
        if token:
            return self._binding_from_token(token, required_role="remote_worker")
        if self._trusted_local_origin() and not self._worker_token_required():
            return None
        raise PermissionError("remote worker pairing token required")

    def _authorize_extension(self, payload: dict[str, object]) -> dict[str, object] | None:
        token = self._auth_token(payload)
        if token and self._management_token() and secrets_compare(token, self._management_token()):
            return None
        if token:
            return self._binding_from_token(token, required_role="browser_extension")
        if self._management_token() or self._production_mode() or not self._trusted_local_origin():
            raise PermissionError("browser extension pairing token required")
        return None

    def _binding_from_token(self, token: str | None = None, *, required_role: str = "android_bridge") -> dict[str, object] | None:
        token = token if token is not None else self._auth_token({})
        if not token:
            return None
        binding = self.store.authenticate_token(token, required_role=required_role)
        if not binding:
            labels = {
                "android_bridge": "android",
                "remote_worker": "remote worker",
                "ios_terminal": "iOS terminal",
                "account_console": "account console",
                "browser_extension": "browser extension",
            }
            label = labels.get(required_role, required_role or "device")
            raise PermissionError(f"invalid {label} pairing token")
        return binding

    def _authorized_android_command_workspace(self, payload: dict[str, object]) -> str:
        requested = str(payload.get("workspace_id") or "").strip()
        token = self._auth_token(payload)
        if token and self._management_token() and secrets_compare(token, self._management_token()):
            return requested or DEFAULT_WORKSPACE_ID
        if token:
            binding = self._binding_from_token(token, required_role="ios_terminal")
            workspace_id = str(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
            if requested and requested != workspace_id:
                raise PermissionError("android token cannot queue commands outside its workspace")
            return workspace_id
        if self._management_token() or self._production_mode() or not self._trusted_local_origin():
            raise PermissionError("management token required")
        return requested or DEFAULT_WORKSPACE_ID

    def _authorize_management(self) -> None:
        expected = self._management_token()
        if not expected:
            if self._production_mode():
                raise PermissionError("management token required in production mode")
            if self._trusted_local_origin():
                return
            raise PermissionError("management token required")
        token = self._auth_token({})
        if not token:
            raise PermissionError("management token required")
        if not secrets_compare(token, expected):
            raise PermissionError("invalid management token")

    def _auth_token(self, payload: dict[str, object] | None = None) -> str:
        auth = self.headers.get("authorization") or self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        header_token = (
            self.headers.get("x-spiritkin-ios-token")
            or self.headers.get("X-SpiritKin-iOS-Token")
            or self.headers.get("x-spiritkin-token")
            or self.headers.get("X-SpiritKin-Token")
            or ""
        )
        if header_token:
            return header_token.strip()
        if isinstance(payload, dict):
            return str(payload.get("token") or payload.get("pairing_token") or "").strip()
        return ""

    def _pairing_required(self) -> bool:
        return self._env_enabled("SPIRITKIN_REQUIRE_PAIRING_TOKEN") or self._production_mode()

    def _worker_token_required(self) -> bool:
        return self._env_enabled("SPIRITKIN_REQUIRE_WORKER_TOKEN") or self._production_mode()

    def _management_token(self) -> str:
        return os.environ.get("SPIRITKIN_MANAGEMENT_TOKEN", "").strip()

    def _production_mode(self) -> bool:
        return self._env_enabled("SPIRITKIN_PRODUCTION_MODE")

    def _env_enabled(self, name: str) -> bool:
        value = os.environ.get(name, "").strip().lower()
        return value in {"1", "true", "yes", "on", "required", "production"}

    def _public_base_url(self) -> str:
        proto = self.headers.get("x-forwarded-proto") or "http"
        host = self.headers.get("x-forwarded-host") or self.headers.get("host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"{proto}://{host}".rstrip("/")

    def _cors(self) -> None:
        origin = self.headers.get("Origin") or self.headers.get("origin") or ""
        allowed = self._allowed_origins()
        if origin and origin in allowed:
            self.send_header("access-control-allow-origin", origin)
            self.send_header("vary", "Origin")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type, authorization, x-spiritkin-ios-token, x-spiritkin-token, x-spiritkin-workspace, x-spiritkin-workspace-id")

    def _allowed_origins(self) -> set[str]:
        configured = os.environ.get("SPIRITKIN_ALLOWED_ORIGINS", "")
        origins = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
        origins.update({"http://127.0.0.1:8791", "http://localhost:8791", "http://127.0.0.1:8792", "http://localhost:8792", "http://127.0.0.1:8787", "http://localhost:8787"})
        return origins

    def _trusted_local_origin(self) -> bool:
        if not hasattr(self, "client_address"):
            # Unit-test doubles do not have a socket peer; real requests always
            # carry client_address and must also provide an allowlisted Origin.
            return True
        is_loopback = self._is_loopback_client()
        origin = (self.headers.get("Origin") or self.headers.get("origin") or "").rstrip("/")
        if not is_loopback:
            return False
        if origin in self._allowed_origins():
            return True
        # Top-level iframe navigation does not reliably send an Origin header.
        # Accept only an allowlisted loopback Referer so the local PWA terminal
        # can bootstrap without putting a bearer token in its iframe URL.
        referer = (self.headers.get("Referer") or self.headers.get("referer") or "").rstrip("/")
        if not referer:
            return False
        try:
            referer_origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}".rstrip("/")
        except ValueError:
            return False
        return referer_origin in self._allowed_origins()

    def _is_loopback_client(self) -> bool:
        try:
            return ipaddress.ip_address(getattr(self, "client_address", ("",))[0]).is_loopback
        except (ValueError, IndexError, TypeError):
            return False

    def log_message(self, fmt: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SpiritKin lightweight mobile/control-plane receiver.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state-dir", default="")
    return parser


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.state_dir:
        Handler.store = ControlPlaneStore(args.state_dir)
    from backend.orchestrator.runtime_host import (
        RuntimeCheckpointStore,
        RuntimeHostHeartbeatService,
        RuntimeHostRegistry,
        RuntimeWorkflowHostService,
    )
    from backend.orchestrator.workflow_store import JsonWorkflowStore

    runtime_host_type = os.getenv("SPIRITKIN_RUNTIME_HOST_TYPE", "desktop").strip().lower() or "desktop"
    runtime_workspace_id = normalize_workspace_id(os.getenv("SPIRITKIN_RUNTIME_WORKSPACE_ID", DEFAULT_WORKSPACE_ID))
    hostname = re.sub(r"[^A-Za-z0-9._-]+", "-", socket.gethostname()).strip("-") or "local"
    runtime_host_id = os.getenv("SPIRITKIN_RUNTIME_HOST_ID", f"{runtime_host_type}:{hostname}").strip()
    runtime_registry = RuntimeHostRegistry(ROOT / "state" / "runtime" / "hosts.json")
    runtime_checkpoints = RuntimeCheckpointStore(
        runtime_registry,
        path=ROOT / "state" / "runtime" / "checkpoints.json",
        workflow_store=JsonWorkflowStore(project_root=ROOT),
    )
    runtime_host_service = RuntimeHostHeartbeatService(
        runtime_registry,
        runtime_checkpoints,
        host_id=runtime_host_id,
        workspace_id=runtime_workspace_id,
        host_type=runtime_host_type,
        capabilities=["workflow.execute", "checkpoint.create", "checkpoint.resume", "worker.dispatch"],
        priority=int(os.getenv("SPIRITKIN_RUNTIME_HOST_PRIORITY", "50")),
    )
    runtime_workflow_service = RuntimeWorkflowHostService(
        runtime_host_service,
        execution_interval_seconds=float(os.getenv("SPIRITKIN_RUNTIME_EXECUTION_INTERVAL", "2")),
        max_runs=int(os.getenv("SPIRITKIN_RUNTIME_MAX_RUNS", "20")),
        max_steps_per_run=int(os.getenv("SPIRITKIN_RUNTIME_MAX_STEPS", "10")),
    )
    runtime_workflow_service.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening on http://{args.host}:{args.port}/android/link", flush=True)
    print(f"control entry: http://127.0.0.1:{args.port}/control", flush=True)
    print(f"iOS terminal: http://127.0.0.1:{args.port}/ios/terminal", flush=True)
    try:
        server.serve_forever()
    finally:
        runtime_workflow_service.stop()


if __name__ == "__main__":
    main()
