from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.app.runtime import InteractionInput, SpiritKinRuntime
from backend.mobile.ios_bridge import validate_ios_action
from backend.security.http import (
    add_cors_headers,
    token_matches,
)

DEFAULT_IOS_HOST = os.getenv("SPIRITKIN_IOS_HOST", "0.0.0.0")
DEFAULT_IOS_PORT = int(os.getenv("SPIRITKIN_IOS_PORT", "8792"))
MAX_IOS_BODY_BYTES = 2 * 1024 * 1024
PROJECT_ROOT_POSIX = Path(__file__).resolve().parents[2].as_posix()
IOS_AUTH_HEADER = "X-SpiritKin-iOS-Token"
IOS_WORKSPACE_HEADER = "X-SpiritKin-Workspace"
DEFAULT_IOS_WORKSPACE_ID = os.getenv("SPIRITKIN_IOS_WORKSPACE_ID", "local-ecommerce").strip() or "local-ecommerce"
IOS_CONTROL_SNAPSHOT_TTL_SECONDS = max(0.0, float(os.getenv("SPIRITKIN_IOS_SNAPSHOT_TTL_SECONDS", "3.0") or "3.0"))
_IOS_CONTROL_CACHE_LOCK = RLock()
_IOS_CONTROL_SNAPSHOT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class iOSShortcutEndpoint(BaseHTTPRequestHandler):
    runtime: SpiritKinRuntime | None = None
    auth_token: str = ""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        add_cors_headers(self, allowed_headers=f"Content-Type, Authorization, {IOS_AUTH_HEADER}, {IOS_WORKSPACE_HEADER}", env_key="SPIRITKIN_IOS_ALLOWED_ORIGINS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, status: int, body: str, *, content_type: str, cache_control: str = "no-store") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: Any, *, filename: str, mime_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.auth_token:
            return False
        if token_matches(self.headers, expected_token=self.auth_token, header_name=IOS_AUTH_HEADER):
            return True
        return False

    def _workspace_id(self, payload: dict[str, Any] | None = None) -> str:
        query = parse_qs(urlparse(self.path).query)
        requested = (
            str((payload or {}).get("workspace_id") or "").strip()
            or str(self.headers.get(IOS_WORKSPACE_HEADER) or self.headers.get("X-SpiritKin-Workspace-Id") or "").strip()
            or str(query.get("workspace_id", [""])[0] or "").strip()
        )
        allowed_raw = os.getenv("SPIRITKIN_IOS_ALLOWED_WORKSPACES", "")
        allowed = {item.strip() for item in allowed_raw.split(",") if item.strip()} or {DEFAULT_IOS_WORKSPACE_ID}
        if requested and requested not in allowed:
            raise PermissionError("iOS token cannot access the requested workspace")
        return requested or DEFAULT_IOS_WORKSPACE_ID

    def do_OPTIONS(self) -> None:
        self._send_json(204, {"ok": True})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/ios/health":
            self._send_json(200, {"ok": True, "service": "spiritkin-ios-endpoint"})
            return
        if path in ("/ios/terminal", "/ios/control"):
            self._send_html(200, _ios_terminal_html())
            return
        if path == "/ios/terminal.webmanifest":
            self._send_text(
                200,
                json.dumps(_ios_terminal_manifest(), ensure_ascii=False, sort_keys=True),
                content_type="application/manifest+json; charset=utf-8",
                cache_control="no-store",
            )
            return
        if path == "/ios/service-worker.js":
            self._send_text(200, _ios_service_worker_js(), content_type="application/javascript; charset=utf-8", cache_control="no-store")
            return
        if path == "/ios/icon.svg":
            self._send_text(200, _ios_icon_svg(), content_type="image/svg+xml; charset=utf-8", cache_control="public, max-age=3600")
            return
        if path in {"/ios/apple-touch-icon.png", "/ios/icon-192.png", "/ios/icon-512.png"}:
            icon = _ios_app_icon_path()
            if icon.exists():
                self._send_file(icon, filename=icon.name, mime_type="image/png")
            else:
                self._send_text(200, _ios_icon_svg(), content_type="image/svg+xml; charset=utf-8", cache_control="public, max-age=3600")
            return
        if path in {"/ios/control/snapshot", "/ios/native/snapshot"}:
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            query = parse_qs(urlparse(self.path).query)
            force_refresh = str(query.get("refresh", [""])[0]).lower() in {"1", "true", "yes"}
            try:
                workspace_id = self._workspace_id()
                self._send_json(200, _ios_control_snapshot(force_refresh=force_refresh, workspace_id=workspace_id))
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/schemas/shortcuts.json":
            from backend.mobile.ios_shortcuts_catalog import SHORTCUT_CATALOG
            schemas = [{"name": s.name, "description": s.description, "input_schema": s.input_schema, "output_type": s.output_type} for s in SHORTCUT_CATALOG]
            self._send_json(200, {"ok": True, "shortcuts": schemas})
            return
        if path == "/ios/sessions":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_sessions import ios_sessions_snapshot

            try:
                self._send_json(200, ios_sessions_snapshot(workspace_id=self._workspace_id(), include_unscoped=False))
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/capabilities":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_capabilities import ios_capabilities_snapshot

            self._send_json(200, {"ok": True, **ios_capabilities_snapshot()})
            return
        if path == "/ios/pools":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_pools import build_ios_pools_snapshot

            try:
                self._send_json(200, {"ok": True, **build_ios_pools_snapshot(workspace_id=self._workspace_id())})
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/domains":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_domains import ios_domains_snapshot

            self._send_json(200, {"ok": True, **ios_domains_snapshot()})
            return
        if path == "/ios/resources":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_resources import build_ios_resources_snapshot

            try:
                require_ios_capability("resources")
                self._send_json(200, {"ok": True, "resource_management": build_ios_resources_snapshot(workspace_id=self._workspace_id())})
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/monitor":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_monitoring import build_ios_monitor_snapshot
            from scripts.control_plane_store import ControlPlaneStore

            try:
                self._send_json(200, {"ok": True, "monitor": build_ios_monitor_snapshot(ControlPlaneStore(), workspace_id=self._workspace_id())})
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/ecommerce":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_ecommerce import build_ios_ecommerce_snapshot
            from scripts.control_plane_store import ControlPlaneStore

            try:
                require_ios_capability("workflows")
                self._send_json(200, {"ok": True, "ecommerce": build_ios_ecommerce_snapshot(ControlPlaneStore(), workspace_id=self._workspace_id())})
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path == "/ios/growth":
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.capability.growth.runtime import build_growth_snapshot

            try:
                self._send_json(200, {"ok": True, "growth": build_growth_snapshot(workspace_id=self._workspace_id(), include_unscoped=False)})
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return
        if path.startswith("/mobile/artifacts/"):
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            from backend.mobile.artifact_store import MobileArtifactStore

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
        path = urlparse(self.path).path.rstrip("/")
        if path not in ("/ios/shortcut", "/ios/intent", "/ios/sessions", "/ios/capabilities", "/ios/domains", "/ios/pools", "/ios/resources", "/ios/monitor", "/ios/ecommerce", "/ios/growth", "/ios/control/action", "/ios/native/action", "/mobile/artifacts"):
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        if not self._authorized():
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > MAX_IOS_BODY_BYTES:
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return

        if path == "/ios/sessions":
            from backend.mobile.ios_sessions import update_ios_sessions

            try:
                self._send_json(200, update_ios_sessions(payload, workspace_id=self._workspace_id(payload), include_unscoped=False))
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/capabilities":
            from backend.mobile.ios_capabilities import update_ios_capabilities

            try:
                self._send_json(200, {"ok": True, **update_ios_capabilities(payload)})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/domains":
            from backend.mobile.ios_domains import update_ios_domains

            try:
                self._send_json(200, update_ios_domains(payload))
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/pools":
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_pools import handle_ios_pool_action

            try:
                pool = str(payload.get("pool") or payload.get("kind") or "").strip().lower()
                require_ios_capability("skills" if pool == "skills" else "workflows")
                self._send_json(200, handle_ios_pool_action(payload, workspace_id=self._workspace_id(payload)))
            except (PermissionError, ValueError, KeyError) as exc:
                self._send_json(400 if isinstance(exc, (ValueError, KeyError)) else 403, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/resources":
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_resources import handle_ios_resource_action

            try:
                require_ios_capability("resources")
                self._send_json(200, handle_ios_resource_action(payload, workspace_id=self._workspace_id(payload)))
            except (PermissionError, ValueError, KeyError) as exc:
                self._send_json(400 if isinstance(exc, (ValueError, KeyError)) else 403, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/ecommerce":
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_ecommerce import handle_ios_ecommerce_action
            from scripts.control_plane_store import ControlPlaneStore

            try:
                require_ios_capability("workflows")
                result = handle_ios_ecommerce_action(ControlPlaneStore(), payload, workspace_id=self._workspace_id(payload))
                self._send_json(200, result)
            except (PermissionError, ValueError, KeyError) as exc:
                self._send_json(400 if isinstance(exc, (ValueError, KeyError)) else 403, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/monitor":
            from backend.mobile.ios_capabilities import require_ios_capability
            from backend.mobile.ios_monitoring import handle_ios_monitor_action
            from scripts.control_plane_store import ControlPlaneStore

            try:
                require_ios_capability("monitoring")
                self._send_json(200, handle_ios_monitor_action(ControlPlaneStore(), payload, workspace_id=self._workspace_id(payload)))
            except (PermissionError, ValueError, KeyError) as exc:
                self._send_json(400 if isinstance(exc, (ValueError, KeyError)) else 403, {"ok": False, "error": str(exc)})
            return

        if path == "/ios/growth":
            from backend.capability.growth.runtime import handle_growth_action
            from backend.mobile.ios_capabilities import require_ios_capability

            try:
                workspace_id = self._workspace_id(payload)
                require_ios_capability("growth_governance")
                action = str(payload.get("action") or "snapshot").strip().lower()
                if action in {"record_candidate_benchmark", "benchmark_candidate", "run_model_jury", "model_jury", "review_candidate", "register_candidate"}:
                    if payload.get("confirmed") is not True:
                        raise PermissionError("explicit confirmation is required for growth governance actions")
                    if action in {"record_candidate_benchmark", "benchmark_candidate"}:
                        payload["recorded_by"] = "ios-endpoint"
                    elif action in {"run_model_jury", "model_jury"}:
                        payload["requested_by"] = "ios-endpoint"
                    elif action == "review_candidate":
                        payload["reviewer"] = "ios-endpoint"
                    else:
                        payload["registered_by"] = "ios-endpoint"
                payload["workspace_id"] = workspace_id
                payload["allow_unscoped_governance"] = False
                self._send_json(200, handle_growth_action(payload))
            except PermissionError as exc:
                detail = str(exc).lower()
                code = (
                    "growth_confirmation_required"
                    if "confirmation" in detail
                    else ("growth_review_required" if "review" in detail else "workspace_forbidden")
                )
                self._send_json(403, {"ok": False, "error": code, "detail": str(exc)})
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": "growth_action_failed", "detail": str(exc)})
            return

        if path == "/mobile/artifacts":
            from backend.app.command_gateway import build_mobile_artifacts_ingest_response

            status, response = build_mobile_artifacts_ingest_response(payload, client_id=self.client_address[0])
            self._send_json(status, response)
            return

        if path in {"/ios/control/action", "/ios/native/action"}:
            try:
                status, response = _ios_control_action(
                    payload,
                    workspace_id=self._workspace_id(payload),
                    actor="ios-endpoint",
                )
                self._send_json(status, response)
            except PermissionError as exc:
                self._send_json(403, {"ok": False, "error": str(exc)})
            return

        text = str(payload.get("text") or payload.get("input_text") or "").strip()
        action = str(payload.get("action") or payload.get("intent_name") or "ask_spirit")
        allowed, reason = validate_ios_action(action)
        if not allowed:
            self._send_json(403, {"ok": False, "error": "forbidden", "reason": reason})
            return
        if not text:
            self._send_json(400, {"ok": False, "error": "missing text"})
            return
        if self.runtime is None:
            self._send_json(500, {"ok": False, "error": "runtime not configured"})
            return

        shortcut_name = str(payload.get("shortcut_name") or "Ask Spirit")
        metadata = dict(payload.get("metadata") or {})
        metadata["shortcut_name"] = shortcut_name
        metadata["ios_action"] = action
        reply = self.runtime.handle_input(InteractionInput(text=text, channel="ios", metadata=metadata))
        if reply is None:
            self._send_json(204, {"ok": True, "reply": None})
            return
        self._send_json(200, {
            "ok": True,
            "reply": SpiritKinRuntime.build_output_payload(reply),
            "shortcut_output": {"result": reply.text, "emotion": reply.emotion},
        })

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[ios-endpoint] {self.address_string()} - {format % args}")


def serve_ios_endpoint(host: str = DEFAULT_IOS_HOST, port: int = DEFAULT_IOS_PORT) -> None:
    iOSShortcutEndpoint.runtime = SpiritKinRuntime(emit_runtime_events=True)
    iOSShortcutEndpoint.auth_token = os.getenv("SPIRITKIN_IOS_TOKEN", "").strip()
    server = ThreadingHTTPServer((host, port), iOSShortcutEndpoint)
    print(f"[ios] Shortcut endpoint started at http://{host}:{port}/ios/shortcut")
    server.serve_forever()


def main() -> None:
    serve_ios_endpoint()


def _ios_control_snapshot(*, force_refresh: bool = False, workspace_id: str = "") -> dict[str, Any]:
    now = time.monotonic()
    cache_key = str(workspace_id or "").strip() or "__desktop__"
    with _IOS_CONTROL_CACHE_LOCK:
        cached_entry = _IOS_CONTROL_SNAPSHOT_CACHE.get(cache_key)
        if (
            not force_refresh
            and cached_entry is not None
            and IOS_CONTROL_SNAPSHOT_TTL_SECONDS > 0
            and now - cached_entry[0] <= IOS_CONTROL_SNAPSHOT_TTL_SECONDS
        ):
            snapshot = json.loads(json.dumps(cached_entry[1], ensure_ascii=False, default=str))
            meta = dict(snapshot.get("snapshot_meta") or {})
            meta["cache"] = "hit"
            meta["age_seconds"] = round(now - cached_entry[0], 3)
            snapshot["snapshot_meta"] = meta
            return snapshot
    snapshot = _build_ios_control_snapshot(workspace_id=workspace_id)
    _store_ios_control_snapshot(snapshot, workspace_id=workspace_id)
    return snapshot


def _build_ios_control_snapshot(*, workspace_id: str = "") -> dict[str, Any]:
    from backend.app.command_gateway import (
        build_desktop_mobile_management_response,
        build_desktop_safety_response,
        build_desktop_state_maintenance_response,
        build_desktop_workflows_response,
    )

    started = time.perf_counter()
    if workspace_id:
        from backend.mobile.ios_workflows import build_ios_workflow_snapshot

        def workflow_builder() -> tuple[int, dict[str, Any]]:
            return 200, {"ok": True, "workflows": build_ios_workflow_snapshot(workspace_id=workspace_id)}
    else:
        workflow_builder = build_desktop_workflows_response
    section_builders = {
        "services": _build_ios_services_response,
        "safety": build_desktop_safety_response,
        "mobile_management": build_desktop_mobile_management_response,
        "state_maintenance": build_desktop_state_maintenance_response,
        "workflows": workflow_builder,
        "model_governance": _ios_model_governance_response,
    }
    payloads: dict[str, dict[str, Any]] = {}
    statuses: dict[str, int] = {}
    durations: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=len(section_builders)) as executor:
        futures = {executor.submit(builder): key for key, builder in section_builders.items()}
        section_starts = {future: time.perf_counter() for future in futures}
        for future in as_completed(futures):
            key = futures[future]
            try:
                status, payload = future.result()
            except Exception as exc:
                status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            payloads[key] = payload if isinstance(payload, dict) else {}
            statuses[key] = int(status)
            durations[key] = int((time.perf_counter() - section_starts[future]) * 1000)

    services = payloads.get("services", {})
    safety = payloads.get("safety", {})
    mobile = payloads.get("mobile_management", {})
    state_maintenance = payloads.get("state_maintenance", {})
    workflows = payloads.get("workflows", {})
    model_governance = payloads.get("model_governance", {})
    snapshot = {
        "ok": all(status == 200 for status in statuses.values()),
        "workspace_id": str(workspace_id or ""),
        "services": services.get("services", {}),
        "service_ports": services.get("service_ports", {}),
        "safety": safety.get("safety", {}),
        "mobile_management": mobile.get("mobile_management", {}),
        "state_maintenance": state_maintenance.get("state_maintenance", {}),
        "workflows": workflows.get("workflows", {}),
        "model_governance": model_governance.get("model_governance", {}),
        "snapshot_meta": {
            "cache": "miss",
            "generated_at": time.time(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "section_statuses": statuses,
            "section_durations_ms": durations,
            "ttl_seconds": IOS_CONTROL_SNAPSHOT_TTL_SECONDS,
            "module_summary": "ios_compact",
        },
    }
    snapshot["module_management"] = _ios_compact_module_management(snapshot)
    return snapshot


def _build_ios_services_response() -> tuple[int, dict[str, Any]]:
    from backend.app.operations_center import default_managed_services
    from backend.app.service_ports import build_service_port_snapshot

    services = []
    for service in default_managed_services():
        running = _ios_port_open("127.0.0.1", service.port) if int(service.port or 0) > 0 else False
        services.append(
            {
                "service_id": service.service_id,
                "label": service.label,
                "host": "127.0.0.1",
                "port": service.port,
                "status": "running" if running else "stopped",
                "running": running,
                "health_url": f"http://127.0.0.1:{service.port}{service.health_path}" if service.health_path and service.port > 0 else "",
                "autostart": service.autostart,
                "enabled": service.enabled,
                "description": service.description,
            }
        )
    return 200, {
        "ok": True,
        "schema_version": "spiritkin.ios.services.v1",
        "services": {"services": services, "source": "ios_light"},
        "service_ports": build_service_port_snapshot(),
    }


def _ios_model_governance_response() -> tuple[int, dict[str, Any]]:
    from backend.app.command_gateway import build_model_catalog_response

    status, payload = build_model_catalog_response()
    if status != 200 or not isinstance(payload, dict):
        return 200, {
            "ok": False,
            "model_governance": {
                "schema_version": "spiritkin.ios.model_governance.v1",
                "status": "needs_attention",
                "summary": f"model catalog HTTP {status}",
                "local_roles": [],
                "adapters": [],
            },
        }
    return 200, {"ok": True, "model_governance": _ios_compact_model_governance(payload)}


def _ios_compact_model_governance(payload: dict[str, Any]) -> dict[str, Any]:
    local_policy = dict(payload.get("local_model_policy") or {})
    hardware = dict(local_policy.get("hardware") or {})
    benchmark = dict(local_policy.get("scheduler_benchmark") or {})
    brain = dict(payload.get("brain_replacement") or {})
    registry = dict(brain.get("adapter_registry") or {})
    gate = dict(brain.get("replacement_gate") or {})
    benchmark_categories = benchmark.get("categories") or {}
    if isinstance(benchmark_categories, dict):
        category_count = len(benchmark_categories)
    elif isinstance(benchmark_categories, list):
        category_count = len(benchmark_categories)
    else:
        category_count = 0
    roles = [dict(item) for item in local_policy.get("role_assignments") or [] if isinstance(item, dict)]
    adapters = [dict(item) for item in registry.get("adapters") or [] if isinstance(item, dict)]
    auto_replace = bool(gate.get("auto_replace_allowed"))
    status = "needs_attention" if auto_replace else "ready"
    compact_roles = [
        {
            "role_id": role.get("role_id") or "",
            "label": role.get("label") or role.get("role_id") or "Local role",
            "model_id": role.get("model_id") or "",
            "architecture": role.get("architecture") or "",
            "role_scope": list(role.get("role_scope") or [])[:6],
            "quantization_profile": role.get("quantization_profile") or "",
            "vram_policy": role.get("vram_policy") or "",
        }
        for role in roles[:3]
    ]
    compact_adapters = [
        {
            "adapter_id": adapter.get("adapter_id") or "",
            "label": adapter.get("label") or adapter.get("adapter_id") or "Brain adapter",
            "adapter_type": adapter.get("adapter_type") or "",
            "model_id": adapter.get("model_id") or adapter.get("base_model_id") or "",
            "status": adapter.get("status") or "",
            "review_state": adapter.get("review_state") or "",
            "capability_ids": list(adapter.get("capability_ids") or [])[:6],
        }
        for adapter in adapters[:4]
    ]
    return {
        "schema_version": "spiritkin.ios.model_governance.v1",
        "status": status,
        "summary": f"{hardware.get('hardware_class') or '--'} · roles {len(roles)} · adapters {registry.get('adapter_count', len(adapters))}",
        "hardware_class": hardware.get("hardware_class") or "",
        "default_mode": dict(local_policy.get("policy") or {}).get("default_mode") or "",
        "role_count": len(roles),
        "local_roles": compact_roles,
        "scheduler_benchmark": {
            "status": benchmark.get("status") or "not_run",
            "case_count": benchmark.get("case_count") or 0,
            "category_count": category_count,
            "history_count": benchmark.get("history_count") or 0,
            "history": list(benchmark.get("history") or [])[:3],
        },
        "adapter_count": registry.get("adapter_count", len(adapters)),
        "adapters": compact_adapters,
        "replacement_gate": {
            "minimum_average_score": gate.get("minimum_average_score"),
            "critical_cases_must_pass": bool(gate.get("critical_cases_must_pass")),
            "auto_replace_allowed": auto_replace,
        },
        "source": "desktop_model_catalog",
    }


def _ios_port_open(host: str, port: int, timeout: float = 0.12) -> bool:
    if int(port or 0) <= 0:
        return False
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _store_ios_control_snapshot(snapshot: dict[str, Any], *, workspace_id: str = "") -> None:
    cached = json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))
    cache_key = str(workspace_id or "").strip() or "__desktop__"
    with _IOS_CONTROL_CACHE_LOCK:
        _IOS_CONTROL_SNAPSHOT_CACHE[cache_key] = (time.monotonic(), cached)


def _clear_ios_control_snapshot_cache() -> None:
    with _IOS_CONTROL_CACHE_LOCK:
        _IOS_CONTROL_SNAPSHOT_CACHE.clear()


def _ios_control_snapshot_with_updates(updates: dict[str, Any], *, source: str, workspace_id: str = "") -> dict[str, Any]:
    snapshot = _ios_control_snapshot(force_refresh=False, workspace_id=workspace_id)
    merged = dict(snapshot)
    for key, value in updates.items():
        if value is not None:
            merged[key] = value
    merged["module_management"] = _ios_compact_module_management(merged)
    meta = dict(merged.get("snapshot_meta") or {})
    meta["cache"] = "merged"
    meta["merge_source"] = source
    meta["merged_at"] = time.time()
    merged["snapshot_meta"] = meta
    _store_ios_control_snapshot(merged, workspace_id=workspace_id)
    return merged


def _ios_compact_module_management(snapshot: dict[str, Any]) -> dict[str, Any]:
    services = dict(snapshot.get("services") or {})
    service_items = [item for item in services.get("services") or [] if isinstance(item, dict)]
    running_services = sum(1 for item in service_items if item.get("status") == "running" or item.get("running") is True)
    service_status = "ready" if service_items and running_services >= max(1, min(3, len(service_items))) else "needs_attention"

    mobile = dict(snapshot.get("mobile_management") or {})
    android = dict(mobile.get("android") or {})
    ios = dict(mobile.get("ios") or {})
    android_health = dict(dict(android.get("endpoint") or {}).get("health") or {})
    ios_health = dict(dict(ios.get("endpoint") or {}).get("health") or {})
    mobile_attention = not bool(android_health.get("ok"))
    mobile_summary = f"Android {'online' if android_health.get('ok') else 'offline'} · iOS {'online' if ios_health.get('ok') else 'optional/offline'}"

    workflows = dict(snapshot.get("workflows") or {})
    workflow_overview = dict(workflows.get("overview") or {})
    workflow_count = int(workflow_overview.get("available_definition_count") or workflow_overview.get("definition_count") or 0)

    model_governance = dict(snapshot.get("model_governance") or {})
    model_status = str(model_governance.get("status") or "needs_attention")

    safety = dict(snapshot.get("safety") or {})
    safety_active = bool(safety.get("active"))
    modules = [
        {
            "module_id": "services",
            "label": "桌面服务",
            "status": service_status,
            "summary": f"running {running_services}/{len(service_items)}",
            "endpoint": "/desktop/services",
        },
        {
            "module_id": "mobile_management",
            "label": "移动端 Bridge",
            "status": "needs_attention" if mobile_attention else "ready",
            "summary": mobile_summary,
            "endpoint": "/desktop/mobile-management",
        },
        {
            "module_id": "workflows",
            "label": "工作流",
            "status": "ready" if workflow_count else "needs_attention",
            "summary": f"available definitions {workflow_count}",
            "endpoint": "/desktop/workflows",
        },
        {
            "module_id": "model_governance",
            "label": "模型治理",
            "status": model_status,
            "summary": str(model_governance.get("summary") or "local roles / adapters --"),
            "endpoint": "/desktop/model-catalog",
        },
        {
            "module_id": "safety",
            "label": "安全控制",
            "status": "blocked" if safety_active else "ready",
            "summary": str(safety.get("mode") or "normal"),
            "endpoint": "/desktop/safety",
        },
    ]
    ready_count = sum(1 for item in modules if item["status"] == "ready")
    attention_count = sum(1 for item in modules if item["status"] == "needs_attention")
    blocked_count = sum(1 for item in modules if item["status"] == "blocked")
    overview = {
        "status": "blocked" if blocked_count else ("needs_attention" if attention_count else "ready"),
        "module_count": len(modules),
        "ready_count": ready_count,
        "attention_count": attention_count,
        "blocked_count": blocked_count,
    }
    return {
        "schema_version": "spiritkin.ios.module_summary.v1",
        "generated_at": time.time(),
        **overview,
        "overview": overview,
        "modules": modules,
        "source": "ios_compact",
    }


def _ios_control_action(
    payload: dict[str, Any],
    *,
    workspace_id: str = "",
    actor: str = "ios_terminal",
) -> tuple[int, dict[str, Any]]:
    from backend.app.command_gateway import (
        build_desktop_mobile_management_update_response,
        build_desktop_safety_update_response,
        build_desktop_services_update_response,
        build_desktop_state_maintenance_update_response,
        build_desktop_workflows_update_response,
        build_model_catalog_update_response,
    )

    action = str(payload.get("action") or "refresh").strip()
    from backend.mobile.ios_capabilities import require_ios_capability

    if action not in {"refresh", "snapshot"}:
        if action.startswith(("workflow", "start_run", "run_", "compose", "upsert", "save_builtin", "assign_agent", "claim_agent", "complete_agent", "approve_review", "signal_node", "retry_node", "reset_run", "archive_run", "delete_run", "delete_definition", "rollback_definition")):
            require_ios_capability("workflows")
        elif action in {"enqueue_android_command", "clear_android_commands", "start_android_endpoint", "restart_android_endpoint"}:
            require_ios_capability("devices")
        elif action in {"ingest_mobile_artifacts", "cleanup_mobile_artifacts"}:
            require_ios_capability("artifacts")
        elif action in {"panic_stop", "resume"}:
            require_ios_capability("safety")
    if action in {"refresh", "snapshot"}:
        return 200, _ios_control_snapshot(workspace_id=workspace_id)
    if action in {
        "workflow_snapshot",
        "save_builtin_definition",
        "compose_definition",
        "upsert_definition",
        "start_run",
        "run_next",
        "run_node",
        "assign_agent",
        "claim_agent_task",
        "complete_agent_task",
        "approve_review",
        "signal_node",
        "retry_node",
        "reset_run",
        "archive_run",
        "archive_workflow_run",
        "delete_run",
        "delete_workflow_run",
        "cleanup_runs",
        "cleanup_workflow_runs",
        "delete_definition",
        "rollback_definition",
    }:
        if workspace_id:
            from backend.mobile.ios_workflows import handle_ios_workflow_action

            result = handle_ios_workflow_action(payload, workspace_id=workspace_id, actor=actor)
            response = {"ok": bool(result.get("ok", True)), **result}
            response["ios_control"] = _ios_control_snapshot_with_updates(
                {"workflows": response.get("workflows")},
                source=f"workflow:{action}",
                workspace_id=workspace_id,
            )
            return 200, response
        workflow_payload = {**payload, "action": "snapshot" if action == "workflow_snapshot" else action, "actor": payload.get("actor") or actor}
        status, response = build_desktop_workflows_update_response(workflow_payload)
        response["ios_control"] = _ios_control_snapshot_with_updates({"workflows": response.get("workflows")}, source=f"workflow:{action}")
        return status, response
    if action in {
        "start_android_endpoint",
        "restart_android_endpoint",
        "start_ios_endpoint",
        "restart_ios_endpoint",
        "approve_android_apk_release",
        "assign_workspace_to_account",
        "create_account",
        "enqueue_android_command",
        "clear_android_commands",
        "get_account_usage",
        "ingest_mobile_artifacts",
        "list_accounts",
        "cleanup_mobile_artifacts",
        "set_account_status",
        "update_account_plan",
    }:
        status, response = build_desktop_mobile_management_update_response(payload)
        response["ios_control"] = _ios_control_snapshot_with_updates({"mobile_management": response.get("mobile_management")}, source=f"mobile:{action}", workspace_id=workspace_id)
        return status, response
    if action == "evaluate_scheduler_benchmark":
        status, response = build_model_catalog_update_response({**payload, "actor": payload.get("actor") or "ios_terminal"})
        response["ios_control"] = _ios_control_snapshot_with_updates({"model_governance": _ios_compact_model_governance(response)}, source=f"model:{action}", workspace_id=workspace_id)
        return status, response
    if action in {"state_maintenance_snapshot", "cleanup_state", "cleanup_all", "cleanup_android_command_history", "cleanup_workflow_runs", "cleanup_knowledge_jobs", "truncate_large_logs", "migrate_state"}:
        state_payload = {**payload, "action": "snapshot" if action == "state_maintenance_snapshot" else action, "actor": payload.get("actor") or "ios_terminal"}
        status, response = build_desktop_state_maintenance_update_response(state_payload)
        response["ios_control"] = _ios_control_snapshot_with_updates({"state_maintenance": response.get("state_maintenance")}, source=f"state:{action}", workspace_id=workspace_id)
        return status, response
    if action in {"start", "stop", "restart"}:
        service_id = str(payload.get("service_id") or "").strip()
        if service_id not in {"frontend", "event_bridge", "command_gateway", "remote_worker", "android_endpoint", "ios_endpoint"}:
            return 400, {"ok": False, "error": "unsupported service_id"}
        status, response = build_desktop_services_update_response({"action": action, "service_id": service_id, "actor": "ios_terminal"})
        _clear_ios_control_snapshot_cache()
        response["ios_control"] = _ios_control_snapshot(force_refresh=True, workspace_id=workspace_id)
        return status, response
    if action in {"panic_stop", "resume"}:
        status, response = build_desktop_safety_update_response({**payload, "actor": payload.get("actor") or "ios_terminal"})
        response["ios_control"] = _ios_control_snapshot_with_updates({"safety": response.get("safety")}, source=f"safety:{action}", workspace_id=workspace_id)
        return status, response
    return 400, {"ok": False, "error": f"unsupported ios control action: {action}"}


def _ios_terminal_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a1220">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SpiritKin">
<link rel="manifest" href="/ios/terminal.webmanifest">
<link rel="apple-touch-icon" href="/ios/apple-touch-icon.png">
<link rel="icon" href="/ios/icon.svg" type="image/svg+xml">
<title>SpiritKin 电商运营 Terminal</title>
<style>
:root{color-scheme:light;--bg:#f6f8fb;--panel:#fff;--text:#111827;--muted:#667085;--line:#d9e2f2;--primary:#0250cc;--ok:#16803c;--warn:#b45309;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{padding:calc(env(safe-area-inset-top) + 14px) 14px calc(env(safe-area-inset-bottom) + 20px);max-width:760px;margin:0 auto}.hero{background:#0a1220;color:#fff;border-radius:8px;padding:16px;margin-bottom:12px}.hero h1{font-size:22px;margin:4px 0}.muted{color:var(--muted)}.hero .muted{color:#cdd8ec}.row{display:flex;gap:8px;flex-wrap:wrap}.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}h2{font-size:15px;margin:0 0 8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{border:1px solid var(--line);border-radius:8px;padding:10px;background:#fbfdff}.value{font-size:18px;font-weight:700}.pill{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:4px 8px;background:#fff;margin:2px 4px 2px 0}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}button,input,select,textarea{font:inherit;border-radius:8px;border:1px solid var(--line);padding:10px;background:#fff;min-height:42px}button{font-weight:650}button.primary{background:var(--primary);border-color:var(--primary);color:#fff}button.warn{background:#fff7ed;border-color:#fed7aa;color:#9a3412}input,select,textarea{width:100%}textarea{min-height:82px;resize:vertical}.stack{display:grid;gap:8px}.list{display:grid;gap:6px}.item{border-top:1px solid var(--line);padding-top:8px;margin-top:8px}.small{font-size:12px}.hide{display:none}@media (max-width:520px){.grid{grid-template-columns:1fr}.row button{flex:1 1 auto}}
</style>
</head>
<body>
<main>
<section class="hero"><div class="small">领域：电商</div><h1>电商运营 Terminal</h1><div class="muted">管理商品素材、发布预检、Android 上架、工作区、远程执行端和安全状态。</div></section>
<section class="card"><div class="row"><button id="refresh" class="primary">刷新</button><button id="startAndroid">启动 Android</button><button id="restartAndroid">重启 Android</button><button id="approveApk">批准 APK</button><button id="startLifecycle" class="primary">启动验收</button><button id="runBenchmark">Benchmark</button><button id="refreshMobile">刷新移动端</button><button id="softStop" class="warn">Soft Stop</button></div><div id="status" class="muted small" style="margin-top:8px">--</div></section>
<section class="card"><h2>总览</h2><div id="metrics" class="grid"></div></section>
<section class="card"><h2>模型治理</h2><div id="models" class="list"></div></section>
<section class="card"><h2>移动端安全</h2><div id="security" class="small muted">--</div></section>
<section class="card"><h2>配对与工作区</h2><div id="binding" class="small muted">--</div></section>
<section class="card"><h2>工作区与设备</h2><div id="workspaceDevices" class="list"></div></section>
<section class="card"><h2>电商工作流</h2><div class="stack"><select id="workflowSelect"></select><textarea id="workflowInputs" placeholder="{&quot;project_root&quot;:&quot;__SPIRITKIN_PROJECT_ROOT__&quot;}"></textarea><div class="row"><button id="startWorkflow" class="primary">启动工作流</button><button id="refreshWorkflows">刷新工作流</button></div><input id="comboName" value="custom.workflow.ios_ecommerce_combo.v1" placeholder="组合工作流 ID"><input id="comboDisplay" value="iOS 电商组合工作流" placeholder="显示名称"><select id="comboMode"><option value="serial">串行</option><option value="parallel">并行</option></select><textarea id="comboComponents">ecommerce.auto_listing.v1</textarea><div class="row"><button id="composeWorkflow" class="primary">保存组合</button><button id="composeAndStart">保存并启动</button></div><div id="workflowRuns" class="list"></div></div></section>
<section class="card"><h2>Android 手机端</h2><div id="android"></div><div class="stack" style="margin-top:10px"><input id="deviceId" placeholder="设备编号，默认当前工作区手机"><select id="operation"><option value="device.status">设备状态</option><option value="list_installed_apps">应用列表</option><option value="app.launch">启动应用</option><option value="app.close">关闭应用</option><option value="clipboard.write">写入剪贴板</option><option value="android.open_bridge">打开 Bridge</option><option value="android.open_accessibility_settings">打开无障碍设置</option><option value="android.ui_snapshot">页面快照</option><option value="android.screenshot.request_permission">请求截图授权</option><option value="android.screenshot.capture">截图</option><option value="accessibility.tap">点击坐标</option><option value="pdd.launch">打开拼多多</option><option value="pdd.share_image">分享图片到拼多多</option><option value="pdd.create_listing">创建拼多多商品</option><option value="url.open">打开 URL</option></select><input id="commandValue" placeholder="应用名 / URL / artifact_id / 截图目的 / 坐标"><div class="row"><button id="queueCommand" class="primary">下发步骤</button><button id="clearCommands">清空队列</button></div></div></section>
<section class="card"><h2>手机工作上传</h2><div class="stack"><input id="mobileFiles" type="file" accept="image/*" multiple><input id="uploadPurpose" value="ios_work_image" placeholder="用途，例如商品图片"><div class="row"><button id="uploadMobileFiles" class="primary">上传到工作素材</button><button id="cleanupArtifacts">清理过期图片</button></div><div id="uploadStatus" class="small muted">图片会进入工作素材，后续工作流和手机端可通过素材编号使用。</div></div></section>
<section class="card"><h2>状态维护</h2><div id="maintenance" class="small muted">--</div><div class="row" style="margin-top:10px"><button id="refreshMaintenance">刷新维护</button><button id="cleanupState" class="primary">清理增长状态</button></div></section>
<section class="card"><h2>通用能力</h2><div id="bridge"></div></section>
<section class="card"><h2>服务</h2><div id="services" class="list"></div></section>
<section class="card"><h2>模块与安全</h2><div id="modules"></div><div id="safety" class="item"></div></section>
</main>
<script>
const $=id=>document.getElementById(id);const params=new URLSearchParams(location.search);const token=params.get("token")||localStorage.getItem("spiritkin_ios_token")||"";if(token)localStorage.setItem("spiritkin_ios_token",token);let activeWorkflowName=localStorage.getItem("spiritkin_ios_workflow")||"ecommerce.auto_listing.v1";
function headers(extra={}){return token?{"X-SpiritKin-iOS-Token":token,...extra}:extra}
function esc(v){return String(v??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function statusClass(ok){return ok?"ok":"warn"}
function riskLabel(v){return({high:"高风险",medium:"中风险",low:"低风险"})[v]||String(v||"--")}
function scopeLabel(v){return({local_only:"本机",tailscale:"Tailscale",private_lan:"局域网",public_or_unknown:"公网/未知",mixed:"混合"})[v]||String(v||"--")}
function securityHtml(s){if(!s||!Object.keys(s).length)return"当前快照没有移动端安全摘要。";const t=s.tokens||{},w=s.warnings||[],tokenLine=`命令网关 ${t.command_gateway?"已配置":"未配置"} · Android ${t.android_endpoint?"已配置":"未配置"} · iOS ${t.ios_endpoint?"已配置":"未配置"}`;const warn=w.length?w.slice(0,5).map(x=>`<div class="item"><b>${esc(riskLabel(x.severity))}</b> · ${esc(x.title||x.warning_id||"--")}<div class="small muted">${esc(x.detail||"")}</div></div>`).join(""):`<div class="item">暂无阻断项；公网访问仍必须使用 HTTPS/WSS 或 Tailscale。</div>`;return [`安全姿态 ${esc(s.status||"review")} · 网络 ${esc(scopeLabel(s.network_scope))}`,`访问令牌: ${esc(tokenLine)}`,warn,s.operator_hint?`<div class="item">${esc(s.operator_hint)}</div>`:""].filter(Boolean).join("<br>")}
function bindingHtml(b){if(!b||!Object.keys(b).length)return"当前快照没有统一绑定信息。";const n=b.network||{},a=b.android||{},i=b.ios||{},steps=b.setup_steps||[];const stepHtml=steps.length?steps.slice(0,4).map(s=>`<div class="item"><b>${esc(riskLabel(s.severity||"low"))}</b> · ${esc(s.title||s.id||"--")}<div class="small muted">${esc(s.detail||"")}</div></div>`).join(""):"";return [`工作区: ${esc(b.workspace_id||"--")} · 网络 ${esc(scopeLabel(n.scope||"--"))}`,`Android 配对页: <a href="${esc(a.pairing_page_url||"#")}">${esc(a.pairing_page_url||"--")}</a>`,`iOS 主控台: <a href="${esc(i.pwa_url||"#")}">${esc(i.pwa_url||"--")}</a>`,stepHtml].filter(Boolean).join("<br>")}
function workspaceDevicesHtml(mobile){const items=mobile?.workspace_devices?.items||[];if(!items.length)return'<div class="item muted">暂无工作区设备。先让手机端绑定到工作区。</div>';return items.map(w=>{const c=w.counts||{},line=(label,arr)=>(arr||[]).length?`<div class="small muted">${esc(label)}：${(arr||[]).slice(0,6).map(x=>`${esc(x.device_id||"设备")}(${esc(x.status||"--")}${x.foreground_package?` · 前台 ${esc(x.foreground_package)}`:""})`).join("；")}</div>`:"";return `<div class="item"><b>${esc(w.workspace_id||"--")} · ${esc(w.name||"")}</b><div class="small muted">手机端 ${esc(c.android||0)} · iOS 主控 ${esc(c.ios_controllers||0)} · 远程执行 ${esc(c.remote_workers||0)}</div><div class="small muted">绑定 ${esc(c.active_bindings||0)} · 待用配对码 ${esc(c.pending_pairings||0)} · 最近活动 ${esc(w.last_seen_at||"--")}</div>${line("Android 手机端",w.android_devices)}${line("iOS 主控端",w.ios_controllers)}${line("远程执行端",w.remote_workers)}</div>`}).join("")}
function modelsHtml(m){if(!m||!Object.keys(m).length)return'<div class="item muted">暂无模型治理快照。</div>';const roles=m.local_roles||[],adapters=m.adapters||[],bench=m.scheduler_benchmark||{},gate=m.replacement_gate||{},history=bench.history||[];const roleRows=roles.slice(0,3).map(r=>`<div class="item"><b>${esc(r.label||r.role_id||"Local role")}</b><div class="small muted">${esc(r.model_id||"--")} · ${esc(r.quantization_profile||"--")} · ${esc(r.vram_policy||"--")}</div></div>`).join("");const adapterRows=adapters.slice(0,3).map(a=>`<div class="item"><b>${esc(a.label||a.adapter_id||"Brain adapter")}</b><div class="small muted">${esc(a.model_id||"--")} · ${esc(a.status||"--")} · ${esc(a.review_state||"--")}</div></div>`).join("");const historyRows=history.slice(0,2).map(h=>`<div class="item small muted">Benchmark history ${esc(h.passed?"passed":"failed")} · ${esc(h.score??"--")} · ${esc(h.created_at||"--")}</div>`).join("");return [`<div>${esc(m.hardware_class||"--")} · ${esc(m.default_mode||"--")} · roles ${esc(m.role_count||0)} · adapters ${esc(m.adapter_count||0)}</div>`,`<div class="small muted">Benchmark ${esc(bench.status||"not_run")} · cases ${esc(bench.case_count||0)} · Gate ${esc(gate.minimum_average_score??"--")} · ${gate.auto_replace_allowed?"自动替换开启":"自动替换关闭"}</div>`,historyRows,roleRows,adapterRows].filter(Boolean).join("")}
function maintenanceHtml(m){if(!m||!Object.keys(m).length)return"暂无状态维护快照。";const s=m.summary||{},components=m.components||[];return [`组件 ${esc(s.component_count||components.length)} · 条目 ${esc(s.total_count||0)} · 关注 ${esc(s.attention_count||0)}`,...components.slice(0,5).map(c=>`<div class="item"><b>${esc(c.label||c.component_id)}</b> · ${c.needs_attention?"需关注":"正常"} · ${esc(c.count||0)} 项<div class="small muted">${esc(c.schema_version||"--")}</div></div>`)].join("<br>")}
async function getSnapshot(){const r=await fetch("/ios/control/snapshot",{cache:"no-store",headers:headers()});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||`HTTP ${r.status}`);return d}
async function postAction(payload){const r=await fetch("/ios/control/action",{method:"POST",headers:headers({"Content-Type":"application/json"}),body:JSON.stringify(payload)});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||d.detail||`HTTP ${r.status}`);return d.ios_control||d}
function renderCore(d){const mobile=d.mobile_management||{},security=mobile.security||{},android=mobile.android||{},worker=android.worker||{},promotion=worker.promotion_gate||{},comp=android.companion||{},devices=comp.devices||[],recent=comp.recent_commands||[],counts=comp.command_status_counts||{},services=(d.services||{}).services||[],mods=d.module_management||{},models=d.model_governance||{},safety=d.safety||{},meta=d.snapshot_meta||{};const countLine=Object.entries(counts).map(([k,v])=>`${k} ${v}`).join(" · ")||"commands --";$("metrics").innerHTML=[metric("服务",`${services.filter(s=>s.running).length}/${services.length}`,"running"),metric("模块",`${mods.ready_count||0}/${mods.module_count||0}`,"ready"),metric("模型",models.role_count||0,`adapters ${models.adapter_count||0}`),metric("手机端队列",comp.pending_command_count||0,countLine),metric("工作流",(d.workflows?.overview?.available_definition_count??0),`运行 ${d.workflows?.overview?.run_count??0}`),metric("安全",safety.active?"STOP":"normal",safety.mode||"--")].join("");$("security").innerHTML=securityHtml(security);$("workspaceDevices").innerHTML=workspaceDevicesHtml(mobile);$("models").innerHTML=modelsHtml(models);renderWorkflowPanel(d);$("android").innerHTML=`<div class="muted">手机端地址: ${esc(android.receiver_url||"--")}</div><div class="small muted">APK promotion: ${esc(promotion.status||"--")} · serving ${promotion.serving_allowed?"allowed":"blocked"}</div>`+(devices.length?devices.map(x=>`<div class="item"><b>${esc(x.device_id)}</b> <span class="${statusClass(x.online)}">${x.online?"online":"offline"}</span><div class="small muted">battery ${esc(x.battery_pct??"--")} · app ${esc(x.current_app||"--")} · pending ${esc(x.pending_command_count||0)} · running ${esc(x.inflight_command_count||0)} · apps ${esc(x.installed_app_count||0)}${x.last_command?` · last ${esc(x.last_command.operation||"--")}:${esc(x.last_command.status||"--")}`:""}</div></div>`).join(""):'<div class="item muted">暂无手机端后台同步心跳</div>')+recent.slice(-8).map(x=>`<div class="item"><b>${esc(x.device_id||"--")}</b> ${esc(x.operation||"--")} · ${esc(x.status||"--")}<div class="small muted">${esc(x.message||"")}</div></div>`).join("");$("bridge").innerHTML=[`<span class="pill">启动工作流</span><span class="pill">组合工作流</span><span class="pill">分享链接</span><span class="pill">后台同步</span><span class="pill">命令队列</span><span class="pill">启动应用</span><span class="pill">页面快照</span><span class="pill">PDD 流程</span>`,`<div class="item small muted">手机端状态文件: ${esc(comp.state_path||"--")}</div>`,`<div class="small muted">安装包: ${esc((android.apk||{}).exists?"已构建":"缺失")} · 已安装: ${esc((android.installed||{}).installed?"是":"否")} · 快照: ${esc(meta.cache||"--")} ${esc(meta.duration_ms??"")}ms</div>`].join("");$("services").innerHTML=services.map(s=>`<div class="item"><b>${esc(s.label||s.service_id)}</b> <span class="${statusClass(s.running)}">${s.running?"running":"stopped"}</span><div class="small muted">${esc(s.service_id)} · ${esc(s.port||"--")} · ${esc(s.description||"")}</div><div class="row" style="margin-top:6px"><button onclick="serviceAction('start','${esc(s.service_id)}')">启动</button><button onclick="serviceAction('restart','${esc(s.service_id)}')">重启</button></div></div>`).join("");$("modules").innerHTML=`<div>Ready ${esc(mods.ready_count||0)} / ${esc(mods.module_count||0)} · Attention ${esc(mods.attention_count||0)} · Blocked ${esc(mods.blocked_count||0)}</div>`;$("safety").innerHTML=`Safety: <b>${esc(safety.mode||"normal")}</b><div class="small muted">${esc(safety.reason||"")}</div>`}
function render(d){renderCore(d);$("binding").innerHTML=bindingHtml((d.mobile_management||{}).binding||{});$("maintenance").innerHTML=maintenanceHtml(d.state_maintenance||{})}
function metric(label,value,detail){return `<div class="metric"><div class="value">${esc(value)}</div><div class="muted small">${esc(label)} · ${esc(detail)}</div></div>`}
function isEcommerceWorkflow(name){const id=String(name||"").toLowerCase();return ["ecommerce","commerce","listing","product","pdd"].some(keyword=>id.includes(keyword))}
function workflowDefinitions(d){const wf=d.workflows||{},by={};[...(wf.builtin_definitions||[]),...(wf.definitions||[])].forEach(x=>{if(x?.name&&isEcommerceWorkflow(x.name))by[x.name]=x});return Object.values(by)}
function workflowLabel(x){return x?.metadata?.display_name||x?.name||"--"}
function renderWorkflowPanel(d){const defs=workflowDefinitions(d),select=$("workflowSelect"),runs=(d.workflows?.runs||[]).filter(x=>isEcommerceWorkflow(x.workflow_name));if(select){if(!defs.some(x=>x.name===activeWorkflowName))activeWorkflowName=defs[0]?.name||"ecommerce.auto_listing.v1";select.innerHTML=defs.map(x=>`<option value="${esc(x.name)}" ${x.name===activeWorkflowName?"selected":""}>${esc(workflowLabel(x))}</option>`).join("");select.onchange=()=>{activeWorkflowName=select.value;localStorage.setItem("spiritkin_ios_workflow",activeWorkflowName)}}$("workflowRuns").innerHTML=runs.slice(0,10).map(x=>`<div class="item"><b>${esc(x.workflow_name)}</b> · ${esc(x.status)}<div class="small muted">${esc(x.run_id)} · ${esc(x.updated_at||x.created_at||"--")}</div></div>`).join("")||'<div class="item muted">暂无电商运行实例</div>'}
function parseJsonBox(id){const text=($(id)?.value||"").trim();if(!text)return{};try{return JSON.parse(text)}catch(e){throw new Error(`${id} JSON 无效`)}}
function comboComponents(){return ($("comboComponents").value||"").split(/[\\n,]+/).map(x=>x.trim()).filter(Boolean).map(workflow_name=>({workflow_name}))}
async function load(){try{$("status").textContent="加载中...";window.snapshot=await getSnapshot();render(window.snapshot);$("status").textContent=`已刷新 ${new Date().toLocaleTimeString()}`}catch(e){$("status").textContent=`加载失败：${e.message||e}`}}
async function act(payload){try{$("status").textContent="执行中...";window.snapshot=await postAction(payload);render(window.snapshot);$("status").textContent=`动作完成 ${payload.action}`}catch(e){$("status").textContent=`动作失败：${e.message||e}`}}
function schedulerBenchmarkOutputs(){return {json_validity_route_plan:{route:"tool",tool_calls:[],workflow_steps:[],confidence:0.9},tool_call_accuracy_browser:{route:"executor",tool_calls:[{name:"browser.open_url",url:"https://example.com"}]},workflow_step_completeness_publish:{route:"workflow",workflow_steps:["intake","asset_check","review_gate","upload_product"]},context_drift_followup:{route:"agent",context_retained_ids:["order-42","ecom-demo"],irrelevant_context_ids:[]}}}
function lifecycleInputs(){return {project_root:"__SPIRITKIN_PROJECT_ROOT__",device_id:$("deviceId").value||"android_device",artifact_id:"ios_acceptance_artifact",artifact_label:"android_acceptance",caption:"ios acceptance",product_data_path:"state/ecommerce_tasks/productData.json",draft_only:true,confirmed_high_risk:false}}
async function startAndroidLifecycle(){activeWorkflowName="android.command_lifecycle_acceptance.v1";localStorage.setItem("spiritkin_ios_workflow",activeWorkflowName);await act({action:"save_builtin_definition",workflow_name:activeWorkflowName});await act({action:"start_run",workflow_name:activeWorkflowName,inputs:lifecycleInputs()})}
function commandParams(){const op=$("operation").value,val=$("commandValue").value.trim();if(op==="app.launch"||op==="app.close")return {app_name:val};if(op==="url.open")return {url:val};if(op==="android.screenshot.capture"||op==="screenshot.capture"||op==="android.screenshot.request_permission"||op==="android.ui_snapshot")return {purpose:val||"ios_requested_screenshot"};if(op==="pdd.share_image")return {artifact_id:val};if(op==="pdd.create_listing")return {artifact_id:val,draft_only:true};if(op==="device.status"||op==="list_installed_apps")return {query:val||op};if(op==="accessibility.tap")return {target:val};return {text:val}}
async function filePayload(file){const bytes=await file.arrayBuffer();let bin="",view=new Uint8Array(bytes);for(let i=0;i<view.length;i+=8192)bin+=String.fromCharCode(...view.subarray(i,i+8192));return {path:file.name,mime_type:file.type||"application/octet-stream",content_base64:btoa(bin)}}
async function uploadFiles(){try{const files=Array.from($("mobileFiles").files||[]);if(!files.length)throw new Error("请选择图片");$("uploadStatus").textContent="上传中...";const payload={source:"ios_terminal",purpose:$("uploadPurpose").value||"ios_work_image",files:await Promise.all(files.map(filePayload)),tags:["ios","work_image"]};const r=await fetch("/mobile/artifacts",{method:"POST",headers:headers({"Content-Type":"application/json"}),body:JSON.stringify(payload)});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||d.message||`HTTP ${r.status}`);$("uploadStatus").textContent=`已上传 ${d.artifacts?.length||0} 个 artifact`;await load()}catch(e){$("uploadStatus").textContent=`上传失败：${e.message||e}`}}
window.serviceAction=(action,service_id)=>act({action,service_id});$("refresh").onclick=load;$("startAndroid").onclick=()=>act({action:"start_android_endpoint"});$("restartAndroid").onclick=()=>act({action:"restart_android_endpoint"});$("approveApk").onclick=()=>act({action:"approve_android_apk_release",reviewer:"ios_terminal",reason:"iOS terminal approval for controlled Android update."});$("startLifecycle").onclick=startAndroidLifecycle;$("runBenchmark").onclick=()=>act({action:"evaluate_scheduler_benchmark",outputs_by_case_id:schedulerBenchmarkOutputs()});$("refreshMobile").onclick=()=>act({action:"refresh"});$("softStop").onclick=()=>act({action:"panic_stop",mode:"soft_stop",reason:"iOS terminal soft stop"});$("refreshWorkflows").onclick=()=>act({action:"workflow_snapshot",workflow_name:activeWorkflowName});$("startWorkflow").onclick=()=>act({action:"start_run",workflow_name:$("workflowSelect").value||activeWorkflowName,inputs:parseJsonBox("workflowInputs")});$("composeWorkflow").onclick=()=>act({action:"compose_definition",workflow_name:$("comboName").value||"custom.workflow.ios_combo.v1",display_name:$("comboDisplay").value||"iOS 组合工作流",mode:$("comboMode").value,components:comboComponents()});$("composeAndStart").onclick=async()=>{await act({action:"compose_definition",workflow_name:$("comboName").value||"custom.workflow.ios_combo.v1",display_name:$("comboDisplay").value||"iOS 组合工作流",mode:$("comboMode").value,components:comboComponents()});activeWorkflowName=$("comboName").value||"custom.workflow.ios_combo.v1";await act({action:"start_run",workflow_name:activeWorkflowName,inputs:parseJsonBox("workflowInputs")})};$("queueCommand").onclick=()=>act({action:"enqueue_android_command",device_id:$("deviceId").value||"android_device",operation:$("operation").value,params:commandParams()});$("clearCommands").onclick=()=>act({action:"clear_android_commands",device_id:$("deviceId").value});$("uploadMobileFiles").onclick=uploadFiles;$("cleanupArtifacts").onclick=()=>act({action:"cleanup_mobile_artifacts",expired_only:true});$("refreshMaintenance").onclick=()=>act({action:"state_maintenance_snapshot"});$("cleanupState").onclick=()=>act({action:"cleanup_all",keep_recent:30,keep_android_commands:300,keep_android_history:120,keep_kb_jobs:80});load();
if("serviceWorker" in navigator){navigator.serviceWorker.register("/ios/service-worker.js").catch(()=>{})}
</script>
</body>
</html>""".replace("__SPIRITKIN_PROJECT_ROOT__", PROJECT_ROOT_POSIX)


def _ios_terminal_manifest() -> dict[str, Any]:
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


def _ios_service_worker_js() -> str:
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


def _ios_icon_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#0a1220"/>
  <path d="M126 154h260v204H126z" fill="#f8fbff"/>
  <path d="M156 190h200v28H156zm0 58h138v28H156zm0 58h170v28H156z" fill="#0250cc"/>
  <circle cx="376" cy="316" r="38" fill="#16a34a"/>
  <path d="M366 315l8 9 19-23" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def _ios_app_icon_path() -> Path:
    return (Path.cwd() / "ios" / "SpiritKinTerminal" / "Sources" / "Assets.xcassets" / "AppIcon.appiconset" / "AppIcon-1024.png").resolve()


if __name__ == "__main__":
    main()
