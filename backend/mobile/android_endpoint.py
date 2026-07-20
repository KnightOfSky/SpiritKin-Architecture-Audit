from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from backend.app.runtime import InteractionInput, SpiritKinRuntime
from backend.mobile.android_apk_promotion import build_apk_promotion_gate
from backend.mobile.android_bridge import AndroidCompanionRegistry
from backend.mobile.android_companion_store import AndroidCompanionStore
from backend.mobile.android_push import AndroidPushQueue
from backend.mobile.artifact_store import MobileArtifactStore
from backend.mobile.link_receiver import MobileLinkError, record_mobile_pdd_link
from backend.security.http import add_cors_headers, is_local_request, localhost_auth_bypass_enabled, token_matches

DEFAULT_ANDROID_HOST = os.getenv("SPIRITKIN_ANDROID_HOST", "0.0.0.0")
DEFAULT_ANDROID_PORT = int(os.getenv("SPIRITKIN_ANDROID_PORT", "8791"))
ANDROID_AUTH_HEADER = "X-SpiritKin-Android-Token"
DEFAULT_WORKSPACE_ID = "local-ecommerce"
DEFAULT_ANDROID_BRIDGE_ROOT = "mobile-link-bridge"


class AndroidDeviceEndpoint(BaseHTTPRequestHandler):
    runtime: SpiritKinRuntime | None = None
    auth_token: str = ""
    push_queue: AndroidPushQueue | None = None
    companion_registry: AndroidCompanionRegistry | None = None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {ANDROID_AUTH_HEADER}", env_key="SPIRITKIN_ANDROID_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {ANDROID_AUTH_HEADER}", env_key="SPIRITKIN_ANDROID_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, *, filename: str, mime_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {ANDROID_AUTH_HEADER}", env_key="SPIRITKIN_ANDROID_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json(204, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/android/health":
            self._send_json(200, {"ok": True, "service": "spiritkin-android-endpoint"})
            return
        if path in {"/android/apk/manifest", "/android/update"}:
            self._send_json(200, {"ok": True, "apk": _apk_manifest(_public_base_url(self))})
            return
        if path == "/android/apk":
            apk = _android_apk_path()
            if not apk.exists():
                self._send_json(404, {"ok": False, "error": "apk not built", "path": str(apk)})
                return
            release_manifest = _read_release_manifest(_android_release_manifest_path())
            promotion_gate = build_apk_promotion_gate(apk_path=apk, release_manifest=release_manifest)
            if not promotion_gate.get("serving_allowed"):
                self._send_json(
                    403,
                    {
                        "ok": False,
                        "error": "apk release not approved",
                        "promotion_gate": promotion_gate,
                    },
                )
                return
            self._send_file(apk, filename="mobile-link-bridge.apk", mime_type="application/vnd.android.package-archive")
            return
        if path in {"/pairing", "/android/pairing"}:
            query = parse_qs(parsed.query)
            workspace_id = query.get("workspace_id", [DEFAULT_WORKSPACE_ID])[0] or DEFAULT_WORKSPACE_ID
            token = secrets.token_urlsafe(24)
            expires_at = time.time() + int(query.get("ttl_minutes", ["30"])[0]) * 60
            pairing = {
                "token_id": f"pair_{int(time.time())}_{secrets.token_hex(4)}",
                "workspace_id": workspace_id,
                "device_role": "android_bridge",
                "expires_at": expires_at,
                "server_url": f"{_public_base_url(self)}/android/link",
                "pairing_token": token,
            }
            pairing["deep_link"] = "spiritkin://pair?" + urlencode(
                {
                    "server_url": pairing["server_url"],
                    "workspace_id": workspace_id,
                    "pairing_token": token,
                    "device_role": "android_bridge",
                }
            )
            pairing["qr_png_data_url"] = _qr_data_url(str(pairing["deep_link"]))
            wants_json = query.get("format", [""])[0].lower() == "json"
            accepts_html = "text/html" in (self.headers.get("accept") or "").lower()
            if accepts_html and not wants_json:
                self._send_html(200, _pairing_html(pairing))
            else:
                self._send_json(200, {"ok": True, "pairing": pairing})
            return
        if path.startswith("/android/artifact/"):
            artifact_id = path.rsplit("/", 1)[-1]
            try:
                artifact_file = MobileArtifactStore().artifact_file(artifact_id)
            except (KeyError, FileNotFoundError) as exc:
                self._send_json(404, {"ok": False, "error": str(exc)})
                return
            self._send_file(artifact_file["path"], filename=artifact_file["filename"], mime_type=artifact_file["mime_type"])
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        if path not in ("/android/command", "/android/heartbeat", "/android/link", "/android/artifact", "/android/pair"):
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        client_ip = str(self.client_address[0]) if getattr(self, "client_address", None) else ""
        if not _android_authorized(self.headers, self.auth_token, client_ip=client_ip):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return

        if path == "/android/pair":
            self._send_json(
                200,
                {
                    "ok": True,
                    "binding": {
                        "device_id": str(payload.get("device_id") or "android_device"),
                        "workspace_id": str(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID),
                        "device_role": "android_bridge",
                        "receiver_url": f"{_public_base_url(self)}/android/link",
                    },
                },
            )
            return

        if path == "/android/link":
            try:
                result = record_mobile_pdd_link(payload, client=self.client_address[0])
            except MobileLinkError as exc:
                self._send_json(400, {"ok": False, "error": exc.error_code, "message": str(exc)})
                return
            self._send_json(200, {"ok": True, **result})
            return

        if path == "/android/artifact":
            device_id = str(payload.get("device_id") or payload.get("serial") or "")
            result = MobileArtifactStore().ingest(payload, source="android_bridge", device_id=device_id)
            if result.get("ok") and result.get("artifacts"):
                for artifact in result.get("artifacts", []):
                    if isinstance(artifact, dict) and artifact.get("artifact_id"):
                        artifact["download_url"] = f"{_public_base_url(self)}/android/artifact/{artifact['artifact_id']}"
            self._send_json(200 if result.get("ok") else 400, result)
            return

        if path == "/android/heartbeat":
            device_id = str(payload.get("device_id") or "")
            pending = self.push_queue.drain(device_id) if self.push_queue is not None else []
            status = self.companion_registry.update_heartbeat(payload) if self.companion_registry is not None else {"device_id": device_id}
            pending_commands = self.companion_registry.drain_commands(device_id) if self.companion_registry is not None else []
            self._send_json(200, {"ok": True, "device_id": device_id, "status": status, "pending_notifications": pending, "pending_commands": pending_commands})
            return

        if path == "/android/command":
            text = str(payload.get("text") or "").strip()
            if not text:
                self._send_json(400, {"ok": False, "error": "missing text"})
                return
            if self.runtime is None:
                self._send_json(500, {"ok": False, "error": "runtime not configured"})
                return
            metadata = dict(payload.get("metadata") or {})
            metadata["device_id"] = payload.get("device_id", "")
            metadata["device_state"] = payload.get("device_state", {})
            reply = self.runtime.handle_input(InteractionInput(text=text, channel="android", metadata=metadata))
            if reply is None:
                self._send_json(204, {"ok": True, "reply": None})
                return
            self._send_json(200, {"ok": True, "reply": SpiritKinRuntime.build_output_payload(reply)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[android-endpoint] {self.address_string()} - {format % args}")


def serve_android_endpoint(host: str = DEFAULT_ANDROID_HOST, port: int = DEFAULT_ANDROID_PORT) -> None:
    AndroidDeviceEndpoint.runtime = SpiritKinRuntime(emit_runtime_events=True)
    AndroidDeviceEndpoint.auth_token = os.getenv("SPIRITKIN_ANDROID_TOKEN", "").strip()
    AndroidDeviceEndpoint.push_queue = AndroidPushQueue()
    AndroidDeviceEndpoint.companion_registry = AndroidCompanionRegistry(store=AndroidCompanionStore())
    server = ThreadingHTTPServer((host, port), AndroidDeviceEndpoint)
    print(f"[android] Endpoint started at http://{host}:{port}/android/command")
    print(f"[android] Mobile link receiver at http://{host}:{port}/android/link")
    server.serve_forever()


def _android_authorized(headers: Any, expected_token: str, *, client_ip: str = "") -> bool:
    token = expected_token.strip()
    if not token:
        return localhost_auth_bypass_enabled() and is_local_request(headers, client_ip=client_ip)
    return token_matches(headers, expected_token=token, header_name=ANDROID_AUTH_HEADER)


def _android_bridge_root() -> Path:
    path = Path(os.getenv("SPIRITKIN_ANDROID_BRIDGE_ROOT", DEFAULT_ANDROID_BRIDGE_ROOT))
    if path.is_absolute():
        return path.resolve()
    workspace = Path(os.getenv("SPIRITKIN_WORKSPACE_ROOT", "") or Path.cwd()).resolve()
    return (workspace / path).resolve()


def _android_apk_path() -> Path:
    return _android_bridge_root() / "out" / "mobile-link-bridge.apk"


def _android_apk_manifest_path() -> Path:
    return _android_bridge_root() / "AndroidManifest.xml"


def _android_release_manifest_path() -> Path:
    return _android_bridge_root() / "out" / "release-manifest.json"


def _apk_manifest(base_url: str) -> dict[str, Any]:
    apk = _android_apk_path()
    manifest = _android_apk_manifest_path()
    release_manifest = _read_release_manifest(_android_release_manifest_path())
    text = manifest.read_text(encoding="utf-8", errors="replace") if manifest.exists() else ""
    version_code = _regex(text, r'android:versionCode="([^"]+)"')
    version_name = _regex(text, r'android:versionName="([^"]+)"')
    package_name = _regex(text, r'package="([^"]+)"') or str(release_manifest.get("package_name") or release_manifest.get("app_id") or "com.spiritkin.mobilelinkbridge")
    min_sdk = _regex(text, r'android:minSdkVersion="([^"]+)"')
    target_sdk = _regex(text, r'android:targetSdkVersion="([^"]+)"')
    sha256 = hashlib.sha256(apk.read_bytes()).hexdigest() if apk.exists() else ""
    size_bytes = apk.stat().st_size if apk.exists() else 0
    compatibility = dict(release_manifest.get("compatibility") or {})
    compatibility.setdefault("min_sdk", int(min_sdk) if min_sdk.isdigit() else 23)
    compatibility.setdefault("target_sdk", int(target_sdk) if target_sdk.isdigit() else 35)
    compatibility.setdefault("max_sdk", 0)
    compatibility.setdefault("requires_unknown_app_install_permission", True)
    integrity = dict(release_manifest.get("integrity") or {})
    integrity.setdefault("algorithm", "sha256")
    integrity.setdefault("sha256", sha256)
    integrity.setdefault("size_bytes", size_bytes)
    integrity.setdefault("signature_scheme", "apk_signature_v2_or_newer")
    integrity.setdefault("same_package_signature_required", True)
    if apk.exists():
        integrity["sha256"] = sha256
        integrity["size_bytes"] = size_bytes
    rollback = dict(release_manifest.get("rollback") or {})
    rollback.setdefault("supported", bool(release_manifest.get("rollback")))
    rollback.setdefault("strategy", "serve an older signed APK with matching package/signing key if release-manifest.json is rolled back")
    rollback.setdefault("previous_versions", [])
    build = {
        "status": "ready" if apk.exists() and bool(sha256) else "missing_apk",
        "bridge_root": str(_android_bridge_root()),
        "apk_path": str(apk),
        "source_manifest_path": str(manifest),
        "release_manifest_path": str(_android_release_manifest_path()),
        "release_manifest_present": bool(release_manifest),
    }
    return {
        "manifest_version": int(release_manifest.get("manifest_version") or 2),
        "app_id": package_name,
        "package_name": package_name,
        "version_code": int(str(release_manifest.get("version_code") or version_code or "0")) if str(release_manifest.get("version_code") or version_code or "0").isdigit() else 0,
        "version_name": str(release_manifest.get("version_name") or version_name or ""),
        "download_url": f"{base_url}/android/apk",
        "sha256": sha256,
        "size_bytes": size_bytes,
        "updated_at": release_manifest.get("updated_at") or (apk.stat().st_mtime if apk.exists() else 0),
        "compatibility": compatibility,
        "integrity": integrity,
        "rollback": rollback,
        "promotion_gate": build_apk_promotion_gate(apk_path=apk, release_manifest=release_manifest),
        "build": build,
        "notes": str(release_manifest.get("notes") or "SpiritKin Control Bridge Android update"),
    }


def _read_release_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _public_base_url(handler: BaseHTTPRequestHandler) -> str:
    proto = handler.headers.get("x-forwarded-proto") or "http"
    host = handler.headers.get("x-forwarded-host") or handler.headers.get("host") or f"127.0.0.1:{DEFAULT_ANDROID_PORT}"
    return f"{proto}://{host}".rstrip("/")


def _pairing_html(pairing: dict[str, Any]) -> str:
    workspace_id = html.escape(str(pairing.get("workspace_id") or ""))
    server_url = html.escape(str(pairing.get("server_url") or ""))
    token = html.escape(str(pairing.get("pairing_token") or ""))
    deep_link = html.escape(str(pairing.get("deep_link") or ""))
    qr = str(pairing.get("qr_png_data_url") or "")
    qr_html = f'<img style="width:260px;max-width:100%" src="{html.escape(qr)}" alt="Pairing QR">' if qr.startswith("data:image/png;base64,") else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SpiritKin Android Pairing</title><style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f3f6fb;color:#111827;margin:0}}main{{max-width:720px;margin:auto;padding:20px}}.card{{background:white;border:1px solid #dbe5f3;border-radius:8px;padding:16px;margin:12px 0}}.value{{overflow-wrap:anywhere;background:#eef4ff;border-radius:6px;padding:10px}}a.button{{display:block;background:#0757c8;color:white;text-decoration:none;text-align:center;border-radius:7px;padding:12px;font-weight:700}}</style></head><body><main><h1>Android Bridge 配对</h1><p>打开本页会生成临时 token。已绑定手机不需要每次重新生成。</p><section class="card">{qr_html}<a class="button" href="{deep_link}">在本机打开配对链接</a></section><section class="card"><p>Receiver URL</p><div class="value">{server_url}</div><p>Workspace ID</p><div class="value">{workspace_id}</div><p>Pairing Token</p><div class="value">{token}</div></section></main></body></html>"""


def _qr_data_url(value: str) -> str:
    try:
        import qrcode
    except ModuleNotFoundError:
        return ""
    image = qrcode.make(value)
    out = BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _regex(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def main() -> None:
    serve_android_endpoint()


if __name__ == "__main__":
    main()
