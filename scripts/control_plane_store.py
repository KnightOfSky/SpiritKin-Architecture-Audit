"""Lightweight SpiritKin control plane state store.

The local receiver, iOS terminal, Android bridge endpoint, remote worker
heartbeat, and future desktop management API should all use this module instead
of maintaining separate in-memory registries. The default backend is a single
JSON state file so it can run on a cheap VPS or a local desktop without a DB.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.security.sensitive_payload import DEFAULT_ALLOWED_ROOT_SECRET_KEYS, assert_no_sensitive_payload
from backend.state_store import locked_state_path


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = Path(
    os.environ.get("SPIRITKIN_CONTROL_STATE_DIR")
    or os.environ.get("SPIRITKIN_STATE_DIR", str(ROOT / "state" / "control_plane"))
)

STATE_FILE_NAME = "control_state.json"
ARTIFACT_DIR_NAME = "artifacts"
STATE_VERSION = 3
MIGRATIONS = [
    {
        "name": "bootstrap_v1_defaults",
        "from_version": 0,
        "to_version": 1,
        "description": "Create the default control-plane collections and workspace/template defaults.",
    },
    {
        "name": "v2_action_log_from_events",
        "from_version": 1,
        "to_version": 2,
        "description": "Backfill the unified action_log from legacy events.",
    },
    {
        "name": "v3_accounts_and_quotas",
        "from_version": 2,
        "to_version": 3,
        "description": "Add account ownership, quota plans, and workspace account references.",
    },
]
DEFAULT_WORKSPACE_ID = "local-ecommerce"
DEFAULT_WORKSPACE_NAME = "Local Ecommerce Workspace"
DEFAULT_ACCOUNT_ID = "owner"
DEFAULT_ACCOUNT_NAME = "Owner Account"
DEFAULT_WORKFLOW_TEMPLATE_ID = "ecommerce.auto_listing.v1"
CLI_WORKFLOW_TEMPLATE_ID = "local.cli.run.v1"
LANGGRAPH_WORKFLOW_TEMPLATE_ID = "langgraph.run.v1"
CREWAI_WORKFLOW_TEMPLATE_ID = "crewai.run.v1"
DEFAULT_ARTIFACT_TTL_HOURS = 168
MAX_EVENTS = 300
MAX_ACTION_LOGS = 500
MAX_RECENT = 30
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
_AUTH_BASELINE_KEY = "__spiritkin_auth_baseline"
DEFAULT_ARTIFACT_QUOTA_BYTES = 1024 * 1024 * 1024
DEFAULT_ARTIFACT_QUOTA_COUNT = 1000
DEFAULT_PAIRING_TTL_MINUTES = 30
TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "cancelled"}
DEFAULT_WORKER_TASK_BUDGET = {
    "max_runtime_seconds": 1800,
    "max_artifacts": 20,
    "max_android_commands": 10,
    "max_retries": 1,
}
DEFAULT_WORKER_TASK_LEASE_GRACE_SECONDS = 300
ANDROID_COMMAND_CATALOG: dict[str, dict[str, Any]] = {
    "device.status": {
        "label": "Read device status",
        "risk": "low",
        "required_capabilities": ["device.status"],
    },
    "list_installed_apps": {
        "label": "List installed apps",
        "risk": "low",
        "required_capabilities": ["list_installed_apps"],
    },
    "app.launch": {
        "label": "Launch app",
        "risk": "medium",
        "required_capabilities": ["app.launch"],
        "required_packages_param": "app_name",
        "notes": ["Starts a visible Android activity."],
    },
    "app.close": {
        "label": "Close app",
        "risk": "medium",
        "required_capabilities": ["app.close"],
        "notes": ["Best-effort close. Some Android builds require ADB/device-owner privileges."],
    },
    "url.open": {
        "label": "Open URL",
        "risk": "medium",
        "required_capabilities": ["url.open"],
        "notes": ["Opens a URL in the default Android handler."],
    },
    "clipboard.write": {
        "label": "Write clipboard",
        "risk": "medium",
        "required_capabilities": ["clipboard.write"],
        "notes": ["Writes text to the Android clipboard."],
    },
    "artifact.download": {
        "label": "Download artifact",
        "risk": "low",
        "required_capabilities": ["artifact.download"],
        "requires_artifact": True,
    },
    "image.share_to_app": {
        "label": "Share image artifact",
        "risk": "medium",
        "required_capabilities": ["image.share_to_app"],
        "requires_artifact": True,
        "notes": ["Can grant a temporary content URI read permission to the target app."],
    },
    "artifact.cache.cleanup": {
        "label": "Clean Android artifact cache",
        "risk": "low",
        "required_capabilities": ["artifact.cache.cleanup"],
    },
    "artifact.cache.status": {
        "label": "Read Android artifact cache status",
        "risk": "low",
        "required_capabilities": ["artifact.cache.status"],
    },
    "android.ui_snapshot": {
        "label": "Upload UI snapshot",
        "risk": "high",
        "required_capabilities": ["android.ui_snapshot"],
        "requires_accessibility": True,
        "notes": ["Uploads the current Accessibility node tree as an artifact."],
    },
    "android.screenshot.request_permission": {
        "label": "Request screenshot permission",
        "risk": "medium",
        "required_capabilities": ["android.screenshot.request_permission"],
        "notes": ["Opens Android MediaProjection consent on the target device."],
    },
    "android.screenshot.capture": {
        "label": "Capture screenshot",
        "risk": "high",
        "required_capabilities": ["android.screenshot.capture"],
        "requires_screen_capture_authorization": True,
        "notes": ["Uploads the current screen as a PNG artifact after MediaProjection consent."],
    },
    "screenshot.capture": {
        "label": "Capture screenshot",
        "risk": "high",
        "required_capabilities": ["android.screenshot.capture"],
        "requires_screen_capture_authorization": True,
        "notes": ["Compatibility alias for android.screenshot.capture."],
    },
    "android.open_accessibility_settings": {
        "label": "Open Accessibility settings",
        "risk": "low",
        "required_capabilities": ["android.open_accessibility_settings"],
    },
    "android.open_bridge": {
        "label": "Open Android Bridge",
        "risk": "low",
        "required_capabilities": ["android.open_bridge"],
    },
    "accessibility.tap": {
        "label": "Tap by Accessibility",
        "risk": "high",
        "required_capabilities": ["accessibility.tap"],
        "requires_accessibility": True,
        "notes": ["Generic tap hook for future Android workflow steps."],
    },
    "pdd.launch": {
        "label": "Launch PDD",
        "risk": "medium",
        "required_capabilities": ["pdd.launch"],
        "required_packages": ["com.xunmeng.pinduoduo"],
    },
    "pdd.share_image": {
        "label": "Share image to PDD",
        "risk": "high",
        "required_capabilities": ["pdd.share_image"],
        "required_packages": ["com.xunmeng.pinduoduo"],
        "requires_artifact": True,
    },
    "pdd.create_listing": {
        "label": "Create PDD listing",
        "risk": "critical",
        "required_capabilities": ["pdd.create_listing"],
        "required_packages": ["com.xunmeng.pinduoduo"],
        "requires_accessibility": True,
        "requires_foreground_packages": ["com.xunmeng.pinduoduo", "com.spiritkin.mobilelinkbridge"],
        "requires_artifact": False,
        "notes": ["Uses Accessibility to drive the listing flow. Submission remains controlled by allow_submit."],
    },
}

ANDROID_BASE_OPERATIONS = {
    "device.status",
    "list_installed_apps",
    "artifact.cache.status",
    "artifact.cache.cleanup",
    "android.open_accessibility_settings",
    "android.open_bridge",
    "android.screenshot.request_permission",
    "android.screenshot.capture",
    "screenshot.capture",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sha12(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:12]


def safe_name(value: object, fallback: str = "item") -> str:
    raw = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    cleaned = cleaned.strip("._")
    return cleaned[:100] or fallback


def _is_pdd_web_link(value: str) -> bool:
    link = str(value or "").strip().lower()
    return link.startswith(("http://", "https://")) and ("yangkeduo.com/" in link or "pinduoduo.com/" in link)


def normalize_workspace_id(value: object) -> str:
    return safe_name(value or DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_ID)


def default_workflow_template() -> dict[str, Any]:
    return {
        "template_id": DEFAULT_WORKFLOW_TEMPLATE_ID,
        "name": "Ecommerce Auto Listing",
        "category": "ecommerce",
        "version": "0.1.0",
        "status": "active",
        "execution_model": "control_plane_owned_remote_worker_steps",
        "required_capabilities": [
            "ecommerce.auto_listing",
            "artifact.store",
            "android.bridge",
        ],
        "worker_required_capabilities": ["ecommerce.auto_listing"],
        "artifact_flow": [
            "human_or_selection_agent_uploads_product_images",
            "workflow_passes_artifact_ids_to_listing_agent",
            "android_bridge_downloads_or_receives_images_by_artifact_id",
            "temporary_screenshots_are_cleaned_by_ttl",
        ],
        "metered": "scrape",
        "input_schema": {
            "artifact_ids": "list",
            "device_id": "string",
            "metered_amount": "int",
        },
        "worker_task_operation": "workflow.execute.auto_listing",
        "updated_at": utc_now(),
    }


def built_in_workflow_templates() -> dict[str, dict[str, Any]]:
    return {
        DEFAULT_WORKFLOW_TEMPLATE_ID: default_workflow_template(),
        CLI_WORKFLOW_TEMPLATE_ID: {
            "template_id": CLI_WORKFLOW_TEMPLATE_ID,
            "name": "Governed Local CLI",
            "category": "runtime",
            "version": "0.1.0",
            "status": "active",
            "execution_model": "worker_local_subprocess",
            "required_capabilities": ["local.cli"],
            "worker_required_capabilities": ["local.cli"],
            "worker_task_operation": "local.cli.run",
            "metered": "",
            "input_schema": {"command": "string|list", "cwd": "string", "budget": "object"},
            "updated_at": utc_now(),
        },
        LANGGRAPH_WORKFLOW_TEMPLATE_ID: {
            "template_id": LANGGRAPH_WORKFLOW_TEMPLATE_ID,
            "name": "LangGraph Adapter",
            "category": "agent",
            "version": "0.1.0",
            "status": "active",
            "execution_model": "worker_python_module_adapter",
            "required_capabilities": ["langgraph.run", "local.cli"],
            "worker_required_capabilities": ["langgraph.run", "local.cli"],
            "worker_task_operation": "langgraph.run",
            "metered": "",
            "input_schema": {"module": "string", "args": "list", "command": "optional string|list", "cwd": "string"},
            "updated_at": utc_now(),
        },
        CREWAI_WORKFLOW_TEMPLATE_ID: {
            "template_id": CREWAI_WORKFLOW_TEMPLATE_ID,
            "name": "CrewAI Adapter",
            "category": "agent",
            "version": "0.1.0",
            "status": "active",
            "execution_model": "worker_python_module_adapter",
            "required_capabilities": ["crewai.run", "local.cli"],
            "worker_required_capabilities": ["crewai.run", "local.cli"],
            "worker_task_operation": "crewai.run",
            "metered": "",
            "input_schema": {"module": "string", "args": "list", "command": "optional string|list", "cwd": "string"},
            "updated_at": utc_now(),
        },
    }


def default_execution_policy() -> dict[str, Any]:
    return {
        "control_allowed_actions": [
            "action_log",
            "add_device_workflow",
            "approve_pairing_request",
            "approve_workflow_promotion",
            "assign_workspace_to_account",
            "cancel_workflow_run",
            "clear_android_commands",
            "cleanup_artifacts",
            "cleanup_state",
            "clear_workflow_runs",
            "clear_pairing_history",
            "clear_binding_history",
            "clear_ios_terminal_history",
            "delete_artifact_file",
            "delete_device_workflow",
            "delete_workflow_run",
            "delete_pairing_token",
            "repair_device_workflow",
            "cancel_pairing_token",
            "create_account",
            "get_account_usage",
            "list_accounts",
            "queue_android_command",
            "register_workspace",
            "reject_pairing_request",
            "revoke_device_binding",
            "retry_workflow_run",
            "snapshot",
            "start_workflow_run",
            "set_device_workflow_state",
            "set_account_status",
            "update_account_plan",
            "update_runtime_profile",
            "update_workspace_policy",
            "validate_state",
        ],
        "control_denied_actions": [],
        "android_allowed_operations": sorted(ANDROID_COMMAND_CATALOG.keys()),
        "android_denied_operations": [],
        "workflow_allowed_templates": sorted(built_in_workflow_templates().keys()),
        "worker_allowed_capabilities": [
            "ecommerce.auto_listing",
            "android.bridge",
            "artifact.store",
            "local.cli",
            "langgraph.run",
            "crewai.run",
        ],
        "require_promote_gate": False,
        "approved_promotions": [],
        "default_task_budget": dict(DEFAULT_WORKER_TASK_BUDGET),
        "updated_at": utc_now(),
    }


def default_runtime_profile(workspace_id: str = DEFAULT_WORKSPACE_ID) -> dict[str, Any]:
    safe_workspace = normalize_workspace_id(workspace_id)
    return {
        "profile_id": f"runtime.{safe_workspace}.v1",
        "workspace_root": str(Path("state") / "workspaces" / safe_workspace),
        "venv_path": str(Path("state") / "workspaces" / safe_workspace / ".venv"),
        "dependency_files": ["requirements.txt", "pyproject.toml"],
        "container_image": "",
        "dependency_policy": "project_local_only",
        "allowed_local_commands": ["python"],
        "forbidden_paths": ["E:/AutoProcessAP", "AutoProcess runtime services"],
        "updated_at": utc_now(),
    }


def default_artifact_policy() -> dict[str, Any]:
    return {
        "backend": os.environ.get("SPIRITKIN_ARTIFACT_BACKEND", "local_disk"),
        "backend_root": os.environ.get("SPIRITKIN_ARTIFACT_BACKEND_ROOT", ""),
        "s3_endpoint_url": os.environ.get("SPIRITKIN_ARTIFACT_S3_ENDPOINT_URL", ""),
        "s3_bucket": os.environ.get("SPIRITKIN_ARTIFACT_S3_BUCKET", ""),
        "s3_region": os.environ.get("SPIRITKIN_ARTIFACT_S3_REGION", "us-east-1"),
        "s3_prefix": os.environ.get("SPIRITKIN_ARTIFACT_S3_PREFIX", ""),
        "s3_public_base_url": os.environ.get("SPIRITKIN_ARTIFACT_S3_PUBLIC_BASE_URL", ""),
        "s3_path_style": os.environ.get("SPIRITKIN_ARTIFACT_S3_PATH_STYLE", "1").strip().lower() not in {"0", "false", "no", "off"},
        "s3_access_key_env": os.environ.get("SPIRITKIN_ARTIFACT_S3_ACCESS_KEY_ENV", "AWS_ACCESS_KEY_ID"),
        "s3_secret_key_env": os.environ.get("SPIRITKIN_ARTIFACT_S3_SECRET_KEY_ENV", "AWS_SECRET_ACCESS_KEY"),
        "s3_session_token_env": os.environ.get("SPIRITKIN_ARTIFACT_S3_SESSION_TOKEN_ENV", "AWS_SESSION_TOKEN"),
        "max_workspace_bytes": DEFAULT_ARTIFACT_QUOTA_BYTES,
        "max_workspace_artifacts": DEFAULT_ARTIFACT_QUOTA_COUNT,
        "max_file_bytes": MAX_ARTIFACT_BYTES,
        "default_ttl_hours": DEFAULT_ARTIFACT_TTL_HOURS,
        "cleanup_on_quota": False,
        "updated_at": utc_now(),
    }


def default_account_quotas() -> dict[str, int]:
    return {
        "max_workspaces": 0,
        "max_workers": 0,
        "max_scrapes_per_period": 0,
        "scrape_period_days": 30,
    }


def normalize_account_quotas(value: object) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    for key, default_value in default_account_quotas().items():
        raw = source.get(key, default_value)
        try:
            result[key] = max(0, int(raw))
        except (TypeError, ValueError):
            result[key] = int(default_value)
    for key, raw in source.items():
        key_text = str(key or "").strip()
        if not key_text or key_text in result:
            continue
        try:
            result[key_text] = max(0, int(raw))
        except (TypeError, ValueError):
            result[key_text] = raw
    return result


def default_account_plan(*, now: str | None = None, quotas: dict[str, Any] | None = None, tier: str = "owner") -> dict[str, Any]:
    timestamp = now or utc_now()
    normalized_quotas = normalize_account_quotas(quotas or default_account_quotas())
    days = max(1, int(normalized_quotas.get("scrape_period_days") or 30))
    period_start = parse_time(timestamp).isoformat()
    period_end = (parse_time(period_start) + timedelta(days=days)).isoformat()
    return {
        "tier": str(tier or "owner"),
        "quotas": normalized_quotas,
        "usage": {"scrapes_this_period": 0},
        "period_start": period_start,
        "period_end": period_end,
        "updated_at": timestamp,
    }


def normalize_artifact_policy(value: object) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    default = default_artifact_policy()
    result = dict(default)
    backend = str(source.get("backend") or default["backend"]).strip() or default["backend"]
    if backend not in {"local_disk", "filesystem_object_store", "s3"}:
        backend = default["backend"]
    result["backend"] = backend
    for key in (
        "backend_root",
        "s3_endpoint_url",
        "s3_bucket",
        "s3_region",
        "s3_prefix",
        "s3_public_base_url",
        "s3_access_key_env",
        "s3_secret_key_env",
        "s3_session_token_env",
    ):
        result[key] = str(source.get(key) or default.get(key) or "").strip()
    if "endpoint_url" in source:
        result["s3_endpoint_url"] = str(source.get("endpoint_url") or "").strip()
    if "bucket" in source:
        result["s3_bucket"] = str(source.get("bucket") or "").strip()
    if "region" in source:
        result["s3_region"] = str(source.get("region") or "").strip()
    if "prefix" in source:
        result["s3_prefix"] = str(source.get("prefix") or "").strip()
    if "public_base_url" in source:
        result["s3_public_base_url"] = str(source.get("public_base_url") or "").strip()
    result["s3_path_style"] = bool(source.get("s3_path_style", source.get("path_style", default.get("s3_path_style"))))
    for key in ("max_workspace_bytes", "max_workspace_artifacts", "max_file_bytes", "default_ttl_hours"):
        raw = source.get(key, default[key])
        try:
            result[key] = max(0, int(raw))
        except (TypeError, ValueError):
            result[key] = int(default[key])
    result["cleanup_on_quota"] = bool(source.get("cleanup_on_quota"))
    result["updated_at"] = str(source.get("updated_at") or default["updated_at"])
    return result


class ControlPlaneStore:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self.state_dir = Path(state_dir or DEFAULT_STATE_DIR).resolve()
        self.state_file = self.state_dir / STATE_FILE_NAME
        self.artifact_root = self.state_dir / ARTIFACT_DIR_NAME
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            with locked_state_path(self.state_file):
                state, needs_save = self._load_locked()
                if needs_save:
                    self._save_locked(state)
                self._attach_auth_baseline(state)
                return state

    def save(self, state: dict[str, Any]) -> None:
        with self._lock:
            with locked_state_path(self.state_file):
                self._save_locked(state)

    def _load_locked(self) -> tuple[dict[str, Any], bool]:
        if not self.state_file.exists():
            return self._new_state(), True
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._new_state(), True
        original_version = self._state_version(state)
        needs_save = (
            original_version < STATE_VERSION
            or not isinstance(state.get("schema"), dict)
            or not isinstance(state.get("action_log"), list)
        )
        return self._normalize_state(state), needs_save

    def _save_locked(self, state: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        baseline = payload.pop(_AUTH_BASELINE_KEY, None)
        if isinstance(baseline, dict) and self.state_file.exists():
            current, _ = self._load_locked()
            for collection in ("pairing_tokens", "device_bindings"):
                original = baseline.get(collection) if isinstance(baseline.get(collection), dict) else {}
                desired = payload.get(collection) if isinstance(payload.get(collection), dict) else {}
                merged = dict(current.get(collection) or {})
                for key, value in desired.items():
                    if key not in original or value != original.get(key):
                        merged[key] = value
                for key in original:
                    if key not in desired:
                        merged.pop(key, None)
                payload[collection] = merged
        payload["updated_at"] = utc_now()
        tmp = self.state_file.with_name(
            f".{self.state_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        tmp.write_text(json.dumps(self._normalize_state(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    @staticmethod
    def _attach_auth_baseline(state: dict[str, Any]) -> None:
        state[_AUTH_BASELINE_KEY] = {
            "pairing_tokens": json.loads(json.dumps(state.get("pairing_tokens") or {})),
            "device_bindings": json.loads(json.dumps(state.get("device_bindings") or {})),
        }

    def snapshot(self, workspace_id: str | None = None, *, account_id: str | None = None) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else ""
        account_filter = self._normalize_account_id(account_id) if account_id else ""
        account_workspace_ids: set[str] = set()
        if account_filter:
            for key, workspace in state["workspaces"].items():
                if not isinstance(workspace, dict):
                    continue
                if self._normalize_account_id(workspace.get("account_id") or DEFAULT_ACCOUNT_ID) == account_filter:
                    account_workspace_ids.add(normalize_workspace_id(key))

        def matches_workspace(item: object) -> bool:
            if not workspace_filter and not account_workspace_ids:
                return True
            if not isinstance(item, dict) or not item.get("workspace_id"):
                return False
            item_workspace = normalize_workspace_id(item.get("workspace_id"))
            if workspace_filter:
                return item_workspace == workspace_filter and (not account_workspace_ids or item_workspace in account_workspace_ids)
            return item_workspace in account_workspace_ids

        artifacts = {key: item for key, item in state["artifacts"].items() if matches_workspace(item)}
        commands = {key: item for key, item in state["android_commands"].items() if matches_workspace(item)}
        workers = {key: item for key, item in state["remote_workers"].items() if matches_workspace(item)}
        devices = {key: item for key, item in state["android_devices"].items() if matches_workspace(item)}
        runs = {key: item for key, item in state["workflow_runs"].items() if matches_workspace(item)}
        worker_tasks = {key: item for key, item in state["worker_tasks"].items() if matches_workspace(item)}
        device_workflow_controls = {
            key: item for key, item in state["device_workflow_controls"].items() if matches_workspace(item)
        }
        workspaces = {
            key: item
            for key, item in state["workspaces"].items()
            if (
                (
                    workspace_filter
                    and normalize_workspace_id(key) == workspace_filter
                    and (not account_workspace_ids or normalize_workspace_id(key) in account_workspace_ids)
                )
                or (
                    not workspace_filter
                    and (
                        (account_workspace_ids and normalize_workspace_id(key) in account_workspace_ids)
                        or (not account_workspace_ids and (not workspace_filter or normalize_workspace_id(key) == workspace_filter or matches_workspace(item)))
                    )
                )
            )
        }
        ios_terminals = {key: item for key, item in state["ios_terminals"].items() if matches_workspace(item)}
        pairing_tokens = {key: item for key, item in state["pairing_tokens"].items() if matches_workspace(item)}
        device_bindings = {key: item for key, item in state["device_bindings"].items() if matches_workspace(item)}
        action_logs = [item for item in state["action_log"] if matches_workspace(item)]
        events = [
            item
            for item in state["events"]
            if (not workspace_filter and not account_workspace_ids) or (isinstance(item, dict) and matches_workspace(item.get("payload")))
        ]
        account_summary = self._account_snapshot(state, workspace_id=workspace_filter, account_id=account_filter)
        android_diagnostics = self._android_diagnostics(devices, commands)
        workspace_devices = self._workspace_device_overview(
            workspaces=workspaces,
            devices=devices,
            ios_terminals=ios_terminals,
            remote_workers=workers,
            pairing_tokens=pairing_tokens,
            device_bindings=device_bindings,
            device_workflow_controls=device_workflow_controls,
        )
        artifact_status = Counter(str(item.get("status") or "unknown") for item in artifacts.values())
        command_status = Counter(str(item.get("status") or "unknown") for item in commands.values())
        worker_status = Counter(str(item.get("status") or "unknown") for item in workers.values())
        device_status = Counter(str(item.get("status") or "unknown") for item in devices.values())
        run_status = Counter(str(item.get("status") or "unknown") for item in runs.values())
        total_artifact_bytes = sum(int(item.get("size_bytes") or 0) for item in artifacts.values())
        artifact_quota = self._artifact_quota_summary(state, workspace_filter, workspace_ids=account_workspace_ids)
        action_status = Counter(str(item.get("status") or "unknown") for item in action_logs if isinstance(item, dict))
        action_types = Counter(str(item.get("action") or "unknown") for item in action_logs if isinstance(item, dict))
        return {
            "ok": True,
            "schema": state["schema"],
            "version": state["version"],
            "migrations": MIGRATIONS,
            "state_file": str(self.state_file),
            "artifact_root": str(self.artifact_root),
            "deployment": state["deployment"],
            "workspace_filter": workspace_filter,
            "account_filter": account_filter,
            "workspace_count": len(workspaces),
            "workspaces": sorted(workspaces.values(), key=lambda item: str(item.get("workspace_id"))),
            "accounts": account_summary,
            "workspace_devices": workspace_devices,
            "workflow_templates": sorted(state["workflow_templates"].values(), key=lambda item: str(item.get("template_id"))),
            "android_command_catalog": self._android_command_catalog(),
            "workflow_runs": {
                "count": len(runs),
                "status_counts": dict(sorted(run_status.items())),
                "recent": self._recent(runs.values()),
                "active": self._workflow_run_items(runs.values(), terminal=False),
                "history": self._workflow_run_items(runs.values(), terminal=True),
            },
            "device_workflow_controls": {
                "count": len(device_workflow_controls),
                "items": self._recent(device_workflow_controls.values(), key="updated_at"),
            },
            "worker_tasks": {
                "count": len(worker_tasks),
                "status_counts": dict(sorted(Counter(str(item.get("status") or "unknown") for item in worker_tasks.values()).items())),
                "recent": self._recent(worker_tasks.values()),
            },
            "remote_workers": {
                "count": len(workers),
                "status_counts": dict(sorted(worker_status.items())),
                "items": self._recent(workers.values(), key="last_seen_at"),
            },
            "android": {
                "device_count": len(devices),
                "status_counts": dict(sorted(device_status.items())),
                "devices": self._recent(devices.values(), key="last_seen_at"),
                "diagnostics": android_diagnostics,
                "commands": {
                    "count": len(commands),
                    "status_counts": dict(sorted(command_status.items())),
                    "recent": self._recent(commands.values()),
                },
            },
            "artifacts": {
                "count": len(artifacts),
                "status_counts": dict(sorted(artifact_status.items())),
                "total_size_bytes": total_artifact_bytes,
                "quota": artifact_quota,
                "recent": self._recent(artifacts.values()),
            },
            "ios_terminals": self._recent(ios_terminals.values(), key="last_seen_at"),
            "pairings": {
                "pending_count": len([item for item in pairing_tokens.values() if item.get("status") == "pending"]),
                "request_count": len([item for item in pairing_tokens.values() if item.get("status") == "requested"]),
                "bound_count": len([item for item in device_bindings.values() if item.get("status") == "active"]),
                "android_bound_count": len(
                    [
                        item
                        for item in device_bindings.values()
                        if item.get("status") == "active" and item.get("device_role") == "android_bridge"
                    ]
                ),
                "worker_bound_count": len(
                    [
                        item
                        for item in device_bindings.values()
                        if item.get("status") == "active" and item.get("device_role") == "remote_worker"
                    ]
                ),
                "ios_terminal_bound_count": len(
                    [
                        item
                        for item in device_bindings.values()
                        if item.get("status") == "active" and item.get("device_role") == "ios_terminal"
                    ]
                ),
                "recent_pending": self._recent(
                    [
                        self._pairing_record(item)
                        for item in pairing_tokens.values()
                        if isinstance(item, dict) and item.get("status") == "pending"
                    ],
                    key="created_at",
                ),
                "recent_requests": self._recent(
                    [
                        self._pairing_record(item)
                        for item in pairing_tokens.values()
                        if isinstance(item, dict) and item.get("status") == "requested"
                    ],
                    key="created_at",
                ),
                "recent_history": self._recent(
                    [
                        self._pairing_record(item)
                        for item in pairing_tokens.values()
                        if isinstance(item, dict) and item.get("status") not in {"pending", "requested"}
                    ],
                    key="created_at",
                ),
                "bindings": self._recent(
                    [item for item in device_bindings.values() if isinstance(item, dict) and item.get("status") == "active"],
                    key="last_seen_at",
                ),
                "binding_history": self._recent(
                    [item for item in device_bindings.values() if isinstance(item, dict) and item.get("status") != "active"],
                    key="last_seen_at",
                ),
            },
            "action_log": {
                "count": len(action_logs),
                "status_counts": dict(sorted(action_status.items())),
                "top_actions": dict(action_types.most_common(10)),
                "recent": list(reversed(action_logs[-20:])),
            },
            "events": list(events[-50:]),
            "updated_at": state.get("updated_at"),
        }

    def ensure_workspace(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        name: str = DEFAULT_WORKSPACE_NAME,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
            self._assert_account_active(state, account_id)
            self._assert_can_create_workspace(state, account_id)
            workspace = self._new_workspace(workspace_id, name or workspace_id, account_id=account_id)
            state["workspaces"][workspace_id] = workspace
            self._link_workspace_to_account(state, workspace_id, account_id)
            self._append_event(state, "workspace_registered", {"workspace_id": workspace_id, "account_id": account_id})
            self.save(state)
        return workspace

    def create_account(
        self,
        *,
        account_id: str = "",
        name: str = "",
        plan: dict[str, Any] | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        account_id = self._normalize_account_id(account_id or f"acct-{secrets.token_hex(4)}")
        if account_id in state["accounts"]:
            raise ValueError(f"account already exists: {account_id}")
        account = self._new_account(account_id=account_id, name=name or account_id, plan=plan, tier=str((plan or {}).get("tier") or "default"))
        state["accounts"][account_id] = account
        self._append_event(state, "account_created", {"account_id": account_id, "requested_by": requested_by})
        self.save(state)
        return {"account": account}

    def get_account(self, account_id: str = DEFAULT_ACCOUNT_ID) -> dict[str, Any]:
        state = self.load()
        account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
        account = state["accounts"].get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        return {"account": self._account_record(state, account)}

    def list_accounts(self) -> dict[str, Any]:
        state = self.load()
        return self._account_snapshot(state)

    def assign_workspace_to_account(self, workspace_id: str, account_id: str, *, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
        workspace = state["workspaces"].get(workspace_id)
        if not isinstance(workspace, dict):
            raise KeyError(f"unknown workspace: {workspace_id}")
        self._assert_account_active(state, account_id)
        previous_account_id = self._workspace_account_id(state, workspace_id)
        if previous_account_id != account_id:
            self._assert_can_create_workspace(state, account_id, excluding_workspace_id=workspace_id)
        workspace["account_id"] = account_id
        workspace["updated_at"] = utc_now()
        self._link_workspace_to_account(state, workspace_id, account_id)
        self._append_event(
            state,
            "workspace_account_assigned",
            {
                "workspace_id": workspace_id,
                "account_id": account_id,
                "previous_account_id": previous_account_id,
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"workspace": workspace, "account": self._account_record(state, state["accounts"][account_id])}

    def update_account_plan(
        self,
        account_id: str,
        plan: dict[str, Any] | None = None,
        *,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
        account = state["accounts"].get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        update = plan if isinstance(plan, dict) else {}
        current = self._account_plan(account)
        next_plan = dict(current)
        if "tier" in update:
            next_plan["tier"] = str(update.get("tier") or current.get("tier") or "default")
        quota_update = update.get("quotas") if isinstance(update.get("quotas"), dict) else update
        next_plan["quotas"] = normalize_account_quotas({**dict(current.get("quotas") or {}), **dict(quota_update or {})})
        if "usage" in update and isinstance(update.get("usage"), dict):
            next_plan["usage"] = self._account_usage(update.get("usage"))
        else:
            next_plan["usage"] = self._account_usage(current.get("usage"))
        for key in ("period_start", "period_end"):
            if str(update.get(key) or "").strip():
                next_plan[key] = parse_time(str(update.get(key))).isoformat()
        days = max(1, int(next_plan["quotas"].get("scrape_period_days") or 30))
        if not next_plan.get("period_start"):
            next_plan["period_start"] = utc_now()
        if not next_plan.get("period_end"):
            next_plan["period_end"] = (parse_time(str(next_plan["period_start"])) + timedelta(days=days)).isoformat()
        next_plan["updated_at"] = utc_now()
        account["plan"] = next_plan
        account["updated_at"] = utc_now()
        self._append_event(state, "account_plan_updated", {"account_id": account_id, "requested_by": requested_by, "quotas": next_plan["quotas"]})
        self.save(state)
        return {"account": self._account_record(state, account)}

    def set_account_status(self, account_id: str, status: str, *, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
        account = state["accounts"].get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        next_status = str(status or "active").strip().lower()
        if next_status not in {"active", "disabled"}:
            raise ValueError("account status must be active or disabled")
        account["status"] = next_status
        account["updated_at"] = utc_now()
        self._append_event(state, "account_status_updated", {"account_id": account_id, "status": next_status, "requested_by": requested_by})
        self.save(state)
        return {"account": self._account_record(state, account)}

    def consume_metered_quota(
        self,
        account_id: str,
        *,
        kind: str = "scrape",
        amount: int = 1,
        requested_by: str = "management",
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        owns_state = state is None
        if state is None:
            state = self.load()
        account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
        self._assert_account_active(state, account_id)
        account = state["accounts"].get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        if kind != "scrape":
            raise ValueError(f"unsupported metered quota kind: {kind}")
        amount = max(0, int(amount or 0))
        self._reset_account_period_if_needed(account)
        plan = self._account_plan(account)
        quotas = normalize_account_quotas(plan.get("quotas"))
        usage = self._account_usage(plan.get("usage"))
        limit = int(quotas.get("max_scrapes_per_period") or 0)
        before = int(usage.get("scrapes_this_period") or 0)
        after = before + amount
        if limit and after > limit:
            raise PermissionError(f"account scrape quota exceeded: {after}>{limit}")
        usage["scrapes_this_period"] = after
        plan["usage"] = usage
        plan["updated_at"] = utc_now()
        account["plan"] = plan
        account["updated_at"] = utc_now()
        result = {
            "account_id": account_id,
            "kind": kind,
            "amount": amount,
            "used_before": before,
            "used_after": after,
            "limit": limit,
            "period_start": plan.get("period_start") or "",
            "period_end": plan.get("period_end") or "",
        }
        self._append_event(state, "account_quota_consumed", {**result, "requested_by": requested_by})
        if owns_state:
            self.save(state)
        return result

    def update_workspace_policy(self, workspace_id: str, policy: dict[str, Any], *, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            account_id = self._normalize_account_id(policy.get("account_id") or DEFAULT_ACCOUNT_ID)
            self._assert_account_active(state, account_id)
            self._assert_can_create_workspace(state, account_id)
            workspace = self._new_workspace(workspace_id, workspace_id, account_id=account_id)
            state["workspaces"][workspace_id] = workspace
            self._link_workspace_to_account(state, workspace_id, account_id)
        current = self._workspace_policy(workspace)
        next_policy = dict(current)
        for key in (
            "control_allowed_actions",
            "control_denied_actions",
            "android_allowed_operations",
            "android_denied_operations",
            "workflow_allowed_templates",
            "worker_allowed_capabilities",
            "approved_promotions",
        ):
            if key in policy:
                values = policy.get(key)
                if not isinstance(values, list):
                    raise ValueError(f"{key} must be a list")
                next_policy[key] = sorted({str(item).strip() for item in values if str(item).strip()})
        if "require_promote_gate" in policy:
            next_policy["require_promote_gate"] = bool(policy.get("require_promote_gate"))
        if "default_task_budget" in policy:
            next_policy["default_task_budget"] = self._task_budget(policy.get("default_task_budget"))
        if "artifact_policy" in policy:
            next_artifact_policy = self._artifact_policy_from_update(self._artifact_policy(workspace), policy.get("artifact_policy"))
            next_policy["artifact_policy"] = next_artifact_policy
            workspace["artifact_policy"] = next_artifact_policy
            state["deployment"]["artifact_strategy"] = str(next_artifact_policy.get("backend") or "local_disk")
        next_policy["updated_at"] = utc_now()
        workspace["execution_policy"] = next_policy
        workspace["updated_at"] = utc_now()
        self._append_event(
            state,
            "workspace_policy_updated",
            {"workspace_id": workspace_id, "requested_by": requested_by, "policy": next_policy},
        )
        self.save(state)
        return {"workspace": workspace, "policy": next_policy}

    def update_runtime_profile(self, workspace_id: str, profile: dict[str, Any], *, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            account_id = DEFAULT_ACCOUNT_ID
            self._assert_account_active(state, account_id)
            self._assert_can_create_workspace(state, account_id)
            workspace = self._new_workspace(workspace_id, workspace_id, account_id=account_id)
            state["workspaces"][workspace_id] = workspace
            self._link_workspace_to_account(state, workspace_id, account_id)
        current = self._runtime_profile(workspace)
        next_profile = dict(current)
        for key in ("workspace_root", "venv_path", "container_image", "dependency_policy"):
            if key in profile:
                next_profile[key] = str(profile.get(key) or "").strip()
        for key in ("dependency_files", "allowed_local_commands", "forbidden_paths"):
            if key in profile:
                values = profile.get(key)
                if not isinstance(values, list):
                    raise ValueError(f"{key} must be a list")
                next_profile[key] = sorted({str(item).strip() for item in values if str(item).strip()})
        if next_profile["dependency_policy"] not in {"project_local_only", "locked", "container_only"}:
            raise ValueError("dependency_policy must be project_local_only, locked, or container_only")
        next_profile["updated_at"] = utc_now()
        workspace["runtime_profile"] = next_profile
        workspace["updated_at"] = utc_now()
        self._append_event(
            state,
            "workspace_runtime_profile_updated",
            {"workspace_id": workspace_id, "requested_by": requested_by, "profile": next_profile},
        )
        self.save(state)
        return {"workspace": workspace, "runtime_profile": next_profile}

    def approve_promotion(self, workspace_id: str, template_id: str, *, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            account_id = DEFAULT_ACCOUNT_ID
            self._assert_account_active(state, account_id)
            self._assert_can_create_workspace(state, account_id)
            workspace = self._new_workspace(workspace_id, workspace_id, account_id=account_id)
            state["workspaces"][workspace_id] = workspace
            self._link_workspace_to_account(state, workspace_id, account_id)
        policy = self._workspace_policy(workspace)
        approved = sorted({*policy.get("approved_promotions", []), str(template_id)})
        policy["approved_promotions"] = approved
        policy["updated_at"] = utc_now()
        workspace["execution_policy"] = policy
        workspace["updated_at"] = utc_now()
        self._append_event(
            state,
            "workflow_promotion_approved",
            {"workspace_id": workspace_id, "template_id": str(template_id), "requested_by": requested_by},
        )
        self.save(state)
        return {"workspace": workspace, "policy": policy}

    def register_ios_terminal(self, terminal_id: str, client: str = "", workspace_id: str = DEFAULT_WORKSPACE_ID) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        terminal_id = safe_name(terminal_id or f"ios_{sha12(client or utc_now())}", "ios_terminal")
        terminal = state["ios_terminals"].get(terminal_id, {})
        terminal.update(
            {
                "terminal_id": terminal_id,
                "workspace_id": workspace_id,
                "client": client,
                "status": "online",
                "last_seen_at": utc_now(),
            }
        )
        state["ios_terminals"][terminal_id] = terminal
        self._append_event(state, "ios_terminal_seen", {"terminal_id": terminal_id, "workspace_id": workspace_id})
        self.save(state)
        return terminal

    def clear_ios_terminal_history(
        self,
        *,
        workspace_id: str | None = None,
        keep_latest: int = 1,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else ""
        items = [
            (terminal_id, item)
            for terminal_id, item in state["ios_terminals"].items()
            if isinstance(item, dict)
            and (not workspace_filter or normalize_workspace_id(item.get("workspace_id")) == workspace_filter)
        ]
        items.sort(key=lambda pair: str(pair[1].get("last_seen_at") or ""), reverse=True)
        keep = {terminal_id for terminal_id, _ in items[: max(0, int(keep_latest or 1))]}
        deleted: list[str] = []
        for terminal_id, _ in items:
            if terminal_id in keep:
                continue
            deleted.append(str(terminal_id))
            del state["ios_terminals"][terminal_id]
        if deleted:
            self._append_event(
                state,
                "ios_terminal_history_cleared",
                {"workspace_id": workspace_filter, "deleted": deleted, "requested_by": requested_by},
            )
            self.save(state)
        return {"ok": True, "deleted": deleted, "deleted_count": len(deleted)}

    def record_mobile_link(
        self,
        link: str,
        *,
        source: str,
        client: str = "",
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        device_id: str = "",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        event = {
            "link_id": f"mlink_{sha12(link)}",
            "workspace_id": workspace_id,
            "link": link,
            "source": source or "android-bridge",
            "client": client,
            "device_id": safe_name(device_id, "android_device") if device_id else "",
            "status": "available",
            "received_at": utc_now(),
        }
        state.setdefault("mobile_links", {})[event["link_id"]] = event
        self._append_event(state, "mobile_link_received", {"workspace_id": workspace_id, "link_id": event["link_id"]})
        self.save(state)
        return event

    def list_mobile_links(
        self,
        *,
        workspace_id: str | None = None,
        device_id: str = "",
        source: str = "",
        status: str = "available",
        limit: int = 80,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else ""
        device_filter = safe_name(device_id, "android_device") if device_id else ""
        source_filter = str(source or "").strip()
        status_filter = str(status or "").strip()
        items: list[dict[str, Any]] = []
        for item in state.setdefault("mobile_links", {}).values():
            if not isinstance(item, dict):
                continue
            if workspace_filter and normalize_workspace_id(item.get("workspace_id") or "") != workspace_filter:
                continue
            if device_filter and str(item.get("device_id") or "") not in {"", device_filter}:
                continue
            if source_filter and str(item.get("source") or "") != source_filter:
                continue
            if status_filter and str(item.get("status") or "available") != status_filter:
                continue
            items.append(dict(item))
        items.sort(key=lambda item: str(item.get("received_at") or ""), reverse=True)
        return {"ok": True, "count": len(items), "links": items[: max(0, int(limit or 80))]}

    def mobile_link(self, link_id: str, *, workspace_id: str) -> dict[str, Any]:
        state = self.load()
        link_id = str(link_id or "").strip()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        link = state.setdefault("mobile_links", {}).get(link_id)
        if not isinstance(link, dict) or normalize_workspace_id(link.get("workspace_id") or "") != workspace_id:
            raise KeyError(f"unknown mobile link for workspace: {link_id}")
        return dict(link)

    def claim_mobile_links_for_extension(
        self,
        *,
        workspace_id: str,
        extension_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        extension_id = safe_name(extension_id or "browser_extension", "browser_extension")
        candidates = [
            item
            for item in state.setdefault("mobile_links", {}).values()
            if isinstance(item, dict)
            and normalize_workspace_id(item.get("workspace_id") or DEFAULT_WORKSPACE_ID) == workspace_id
            and str(item.get("status") or "available") == "available"
            and _is_pdd_web_link(str(item.get("link") or ""))
        ]
        candidates.sort(key=lambda item: str(item.get("received_at") or ""))
        claimed: list[dict[str, Any]] = []
        now = utc_now()
        for item in candidates[: max(0, min(50, int(limit or 10)))]:
            item["status"] = "processing"
            item["claimed_at"] = now
            item["claimed_by"] = extension_id
            item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
            claimed.append(dict(item))
        if claimed:
            self._append_event(
                state,
                "browser_extension_links_claimed",
                {
                    "workspace_id": workspace_id,
                    "extension_id": extension_id,
                    "link_ids": [str(item.get("link_id") or "") for item in claimed],
                },
            )
            self.save(state)
        return {"ok": True, "count": len(claimed), "links": claimed}

    def record_mobile_link_extraction_result(
        self,
        link_id: str,
        *,
        workspace_id: str,
        extension_id: str,
        success: bool,
        artifact_id: str = "",
        summary: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        state = self.load()
        link_id = str(link_id or "").strip()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        extension_id = safe_name(extension_id or "browser_extension", "browser_extension")
        link = state.setdefault("mobile_links", {}).get(link_id)
        if not isinstance(link, dict) or normalize_workspace_id(link.get("workspace_id") or "") != workspace_id:
            raise KeyError(f"unknown mobile link for workspace: {link_id}")
        claimed_by = str(link.get("claimed_by") or "")
        if claimed_by and claimed_by != extension_id:
            raise PermissionError("mobile link is claimed by another browser extension")
        if success and not str(artifact_id or "").strip():
            raise ValueError("artifact_id is required for a successful extraction")
        now = utc_now()
        link["status"] = "completed" if success else "failed"
        link["processed_at"] = now
        link["processed_by"] = extension_id
        link["artifact_id"] = str(artifact_id or "").strip()
        link["extraction_summary"] = dict(summary or {})
        link["extraction_error"] = str(error or "")[:1000]
        self._append_event(
            state,
            "browser_extension_extraction_completed" if success else "browser_extension_extraction_failed",
            {
                "workspace_id": workspace_id,
                "link_id": link_id,
                "extension_id": extension_id,
                "artifact_id": link["artifact_id"],
                "error": link["extraction_error"],
            },
        )
        self.save(state)
        return {"ok": True, "link": dict(link)}

    def requeue_mobile_link_for_extension(
        self,
        link_id: str,
        *,
        workspace_id: str,
        requested_by: str,
    ) -> dict[str, Any]:
        state = self.load()
        link_id = str(link_id or "").strip()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        link = state.setdefault("mobile_links", {}).get(link_id)
        if not isinstance(link, dict) or normalize_workspace_id(link.get("workspace_id") or "") != workspace_id:
            raise KeyError(f"unknown mobile link for workspace: {link_id}")
        if str(link.get("status") or "") in {"completed", "deleted"}:
            raise ValueError("completed or deleted mobile links cannot be requeued")
        link["status"] = "available"
        link["requeued_at"] = utc_now()
        link["requeued_by"] = safe_name(requested_by or "browser_extension", "browser_extension")
        for key in ("claimed_at", "claimed_by", "processed_at", "processed_by", "extraction_error"):
            link.pop(key, None)
        self._append_event(
            state,
            "browser_extension_link_requeued",
            {"workspace_id": workspace_id, "link_id": link_id, "requested_by": link["requeued_by"]},
        )
        self.save(state)
        return {"ok": True, "link": dict(link)}

    def delete_mobile_link(
        self,
        link_id: str,
        *,
        workspace_id: str | None = None,
        device_id: str = "",
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        link_id = str(link_id or "").strip()
        link = state.setdefault("mobile_links", {}).get(link_id)
        if not isinstance(link, dict):
            raise KeyError(f"unknown mobile link: {link_id}")
        if workspace_id and normalize_workspace_id(link.get("workspace_id") or "") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown mobile link for workspace: {link_id}")
        if device_id:
            requested_device = safe_name(device_id, "android_device")
            owner_device = str(link.get("device_id") or "")
            if owner_device and owner_device != requested_device:
                raise KeyError(f"unknown mobile link for device: {link_id}")
        link["status"] = "deleted"
        link["deleted_at"] = utc_now()
        link["deleted_by"] = requested_by
        self._append_event(
            state,
            "mobile_link_deleted",
            {"workspace_id": link.get("workspace_id") or "", "link_id": link_id, "requested_by": requested_by},
        )
        self.save(state)
        return {"link": link}

    def create_pairing_token(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        account_id: str = DEFAULT_ACCOUNT_ID,
        device_role: str = "android_bridge",
        requested_by: str = "management",
        server_url: str = "",
        ttl_minutes: int = DEFAULT_PAIRING_TTL_MINUTES,
    ) -> dict[str, Any]:
        state = self.load()
        role = safe_name(device_role or "android_bridge", "android_bridge")
        if role == "account_console":
            workspace_id = ""
            account_id = self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID)
            self._assert_account_active(state, account_id)
        else:
            workspace_id = normalize_workspace_id(workspace_id)
            account_id = self._workspace_account_id(state, workspace_id)
            self._assert_account_active(state, account_id)
            if role == "remote_worker":
                self._assert_can_bind_worker(state, account_id)
        token_id = f"pair_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
        token = secrets.token_urlsafe(24)
        expires_at = (datetime.now(UTC) + timedelta(minutes=ttl_minutes)).isoformat()
        payload = {
            "token_id": token_id,
            "token": token,
            "workspace_id": workspace_id,
            "account_id": account_id,
            "device_role": role,
            "requested_by": requested_by,
            "server_url": str(server_url or ""),
            "status": "pending",
            "created_at": utc_now(),
            "expires_at": expires_at,
        }
        state["pairing_tokens"][token_id] = payload
        self._append_event(
            state,
            "pairing_token_created",
            {"token_id": token_id, "workspace_id": workspace_id, "account_id": account_id, "device_role": payload["device_role"]},
        )
        self.save(state)
        return payload

    def create_pairing_request(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        device_id: str = "",
        device_role: str = "android_bridge",
        requested_by: str = "android_bridge",
        server_url: str = "",
        device_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        requested_device_id = safe_name(device_id or "", "android_device")
        requested_role = safe_name(device_role or "android_bridge", "android_bridge")
        recoverable_tokens: list[str] = []
        if requested_role == "android_bridge" and requested_device_id:
            for existing in state["device_bindings"].values():
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("status") or "") != "active":
                    continue
                if normalize_workspace_id(existing.get("workspace_id")) != workspace_id:
                    continue
                if str(existing.get("device_role") or "") != "android_bridge":
                    continue
                if safe_name(existing.get("device_id") or "", "android_device") != requested_device_id:
                    continue
                recoverable_tokens.append(str(existing.get("token_id") or ""))
        request_id = f"preq_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
        request_secret = secrets.token_urlsafe(18)
        payload = {
            "token_id": request_id,
            "request_id": request_id,
            "token": "",
            "workspace_id": workspace_id,
            "device_id": requested_device_id,
            "device_role": requested_role,
            "requested_by": requested_by,
            "server_url": str(server_url or ""),
            "status": "requested",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "expires_at": "",
            "device_state": device_state if isinstance(device_state, dict) else {},
            "recoverable_binding_tokens": recoverable_tokens,
            "invalidated_binding_tokens": recoverable_tokens,
            "request_secret_hash": hashlib.sha256(request_secret.encode("utf-8")).hexdigest(),
        }
        for token_id in recoverable_tokens:
            pairing = state["pairing_tokens"].get(token_id)
            if isinstance(pairing, dict):
                pairing["status"] = "needs_rebind"
                pairing["rebind_request_id"] = request_id
                pairing["updated_at"] = utc_now()
            for binding in state["device_bindings"].values():
                if not isinstance(binding, dict) or str(binding.get("token_id") or "") != token_id:
                    continue
                binding["status"] = "needs_rebind"
                binding["rebind_request_id"] = request_id
                binding["updated_at"] = utc_now()
        state["pairing_tokens"][request_id] = payload
        self._append_event(
            state,
            "pairing_request_created",
            {
                "request_id": request_id,
                "workspace_id": workspace_id,
                "device_id": payload["device_id"],
                "device_role": payload["device_role"],
                "recoverable_binding_tokens": recoverable_tokens,
            },
        )
        self.save(state)
        client_request = dict(payload)
        client_request["request_secret"] = request_secret
        client_request.pop("request_secret_hash", None)
        return client_request

    def approve_pairing_request(
        self,
        request_id: str,
        *,
        workspace_id: str | None = None,
        ttl_minutes: int = DEFAULT_PAIRING_TTL_MINUTES,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        request_id = str(request_id or "").strip()
        request = state["pairing_tokens"].get(request_id)
        if not isinstance(request, dict):
            raise KeyError(f"unknown pairing request: {request_id}")
        if workspace_id and request.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown pairing request for workspace: {request_id}")
        if str(request.get("status") or "") != "requested":
            raise ValueError(f"pairing request is not pending approval: {request_id}")
        ttl_minutes = max(5, min(int(ttl_minutes or DEFAULT_PAIRING_TTL_MINUTES), 43200))
        recovered_binding: dict[str, Any] | None = None
        for token_id in request.get("recoverable_binding_tokens") or request.get("invalidated_binding_tokens") or []:
            candidate_token_id = str(token_id or "").strip()
            if not candidate_token_id:
                continue
            candidate_pairing = state["pairing_tokens"].get(candidate_token_id)
            if not isinstance(candidate_pairing, dict):
                continue
            if str(candidate_pairing.get("status") or "") not in {"needs_rebind", "bound"}:
                continue
            if normalize_workspace_id(candidate_pairing.get("workspace_id")) != normalize_workspace_id(request.get("workspace_id")):
                continue
            if str(candidate_pairing.get("device_role") or "") != str(request.get("device_role") or ""):
                continue
            if parse_time(str(candidate_pairing.get("expires_at") or "")) <= datetime.now(UTC):
                candidate_pairing["status"] = "expired"
                candidate_pairing["expired_at"] = utc_now()
                continue
            for binding in state["device_bindings"].values():
                if not isinstance(binding, dict):
                    continue
                if str(binding.get("token_id") or "") != candidate_token_id:
                    continue
                if str(binding.get("status") or "") not in {"needs_rebind", "active"}:
                    continue
                if safe_name(binding.get("device_id") or "", "android_device") != safe_name(request.get("device_id") or "", "android_device"):
                    continue
                recovered_binding = binding
                break
            if recovered_binding is not None:
                request["token"] = str(candidate_pairing.get("token") or "")
                request["expires_at"] = str(candidate_pairing.get("expires_at") or "")
                request["recovered_token_id"] = candidate_token_id
                request["recovered_from_request_id"] = request_id
                candidate_pairing["status"] = "pending"
                candidate_pairing["recovery_request_id"] = request_id
                candidate_pairing["recovery_approved_at"] = utc_now()
                break
        request["status"] = "pending"
        request["approved_at"] = utc_now()
        request["approved_by"] = requested_by
        request["updated_at"] = utc_now()
        if not request.get("token"):
            request["token"] = secrets.token_urlsafe(24)
            request["expires_at"] = (datetime.now(UTC) + timedelta(minutes=ttl_minutes)).isoformat()
        request["ttl_minutes"] = ttl_minutes
        self._append_event(
            state,
            "pairing_request_approved",
            {
                "request_id": request_id,
                "workspace_id": request.get("workspace_id") or "",
                "device_id": request.get("device_id") or "",
                "ttl_minutes": ttl_minutes,
                "recovered_token_id": request.get("recovered_token_id") or "",
                "requested_by": requested_by,
            },
        )
        self.save(state)
        client_request = dict(request)
        client_request.pop("request_secret_hash", None)
        return client_request

    def reject_pairing_request(
        self,
        request_id: str,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        request_id = str(request_id or "").strip()
        request = state["pairing_tokens"].get(request_id)
        if not isinstance(request, dict):
            raise KeyError(f"unknown pairing request: {request_id}")
        if workspace_id and request.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown pairing request for workspace: {request_id}")
        if str(request.get("status") or "") not in {"requested", "pending", "expired"}:
            raise ValueError(f"pairing request is not rejectable: {request_id}")
        request["status"] = "rejected"
        request["rejected_at"] = utc_now()
        request["rejected_by"] = requested_by
        request["updated_at"] = utc_now()
        self._append_event(
            state,
            "pairing_request_rejected",
            {
                "request_id": request_id,
                "workspace_id": request.get("workspace_id") or "",
                "device_id": request.get("device_id") or "",
                "requested_by": requested_by,
            },
        )
        self.save(state)
        client_request = dict(request)
        client_request.pop("request_secret_hash", None)
        return {"pairing": client_request}

    def pairing_request_status(self, request_id: str, *, workspace_id: str | None = None, request_secret: str = "") -> dict[str, Any]:
        state = self.load()
        request_id = str(request_id or "").strip()
        request = state["pairing_tokens"].get(request_id)
        if not isinstance(request, dict):
            raise KeyError(f"unknown pairing request: {request_id}")
        if workspace_id and request.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown pairing request for workspace: {request_id}")
        expected_secret_hash = str(request.get("request_secret_hash") or "").strip()
        if expected_secret_hash and request_secret and not secrets.compare_digest(
            expected_secret_hash,
            hashlib.sha256(str(request_secret or "").encode("utf-8")).hexdigest(),
        ):
            raise PermissionError("pairing request secret required")
        if str(request.get("status") or "") == "pending" and parse_time(str(request.get("expires_at") or "")) <= datetime.now(UTC):
            request["status"] = "expired"
            request["expired_at"] = utc_now()
            self.save(state)
        client_request = dict(request)
        client_request.pop("request_secret_hash", None)
        return client_request

    def cancel_pairing_token(
        self,
        token_id: str,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        token_id = str(token_id or "").strip()
        pairing = state["pairing_tokens"].get(token_id)
        if not isinstance(pairing, dict):
            raise KeyError(f"unknown pairing token: {token_id}")
        if workspace_id and pairing.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown pairing token for workspace: {token_id}")
        if pairing.get("status") not in {"pending", "expired"}:
            raise ValueError(f"pairing token is not cancellable: {token_id}")
        pairing["status"] = "cancelled"
        pairing["cancelled_at"] = utc_now()
        pairing["cancelled_by"] = requested_by
        self._append_event(
            state,
            "pairing_token_cancelled",
            {"workspace_id": pairing.get("workspace_id") or "", "token_id": token_id, "requested_by": requested_by},
        )
        self.save(state)
        return {"pairing": pairing}

    def delete_pairing_token(
        self,
        token_id: str,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        token_id = str(token_id or "").strip()
        pairing = state["pairing_tokens"].get(token_id)
        if not isinstance(pairing, dict):
            raise KeyError(f"unknown pairing token: {token_id}")
        if workspace_id and pairing.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown pairing token for workspace: {token_id}")
        del state["pairing_tokens"][token_id]
        self._append_event(
            state,
            "pairing_token_deleted",
            {"workspace_id": pairing.get("workspace_id") or "", "token_id": token_id, "requested_by": requested_by},
        )
        self.save(state)
        return {"ok": True, "deleted": True, "token_id": token_id}

    def clear_pairing_history(
        self,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else None
        deleted: list[str] = []
        for token_id, pairing in list(state["pairing_tokens"].items()):
            if not isinstance(pairing, dict):
                continue
            if workspace_filter and pairing.get("workspace_id") != workspace_filter:
                continue
            if str(pairing.get("status") or "") == "pending":
                continue
            deleted.append(str(token_id))
            del state["pairing_tokens"][token_id]
        if deleted:
            self._append_event(
                state,
                "pairing_history_cleared",
                {"workspace_id": workspace_filter or "", "deleted_count": len(deleted), "requested_by": requested_by},
            )
            self.save(state)
        return {"ok": True, "deleted": deleted, "deleted_count": len(deleted)}

    def bind_device(
        self,
        payload: dict[str, Any],
        *,
        client: str = "",
        required_role: str = "",
    ) -> dict[str, Any]:
        state = self.load()
        token = str(payload.get("pairing_token") or payload.get("token") or "").strip()
        if not token:
            raise ValueError("pairing_token is required")
        pairing = self._pairing_by_token(state, token)
        if pairing is None:
            raise ValueError("invalid pairing token")
        pairing_status = str(pairing.get("status") or "")
        if pairing_status not in {"pending", "needs_rebind"}:
            raise ValueError("pairing token is not pending")
        if parse_time(str(pairing.get("expires_at") or "")) <= datetime.now(UTC):
            pairing["status"] = "expired"
            self.save(state)
            raise ValueError("pairing token expired")
        device_role = str(pairing.get("device_role") or "android_bridge")
        if required_role and device_role != required_role:
            raise PermissionError(f"pairing token role mismatch: expected {required_role}, got {device_role}")
        raw_device_id = payload.get("device_id")
        if device_role == "remote_worker" and not raw_device_id:
            raw_device_id = payload.get("worker_id")
        if device_role == "ios_terminal" and not raw_device_id:
            raw_device_id = payload.get("terminal_id")
        if device_role == "account_console" and not raw_device_id:
            raw_device_id = payload.get("console_id")
        device_id = safe_name(
            raw_device_id or f"device_{secrets.token_hex(6)}",
            "remote_worker" if device_role == "remote_worker" else "device",
        )
        workspace_id = normalize_workspace_id(pairing.get("workspace_id") or DEFAULT_WORKSPACE_ID) if device_role != "account_console" else ""
        account_id = self._normalize_account_id(pairing.get("account_id") or DEFAULT_ACCOUNT_ID) if device_role == "account_console" else self._workspace_account_id(state, workspace_id)
        self._assert_account_active(state, account_id)
        if device_role == "remote_worker":
            self._assert_can_bind_worker(state, account_id, candidate_token=token)
        existing_binding = state["device_bindings"].get(token)
        if isinstance(existing_binding, dict) and str(existing_binding.get("status") or "") == "needs_rebind":
            binding = existing_binding
            binding.update(
                {
                    "device_id": device_id,
                    "workspace_id": workspace_id,
                    "device_role": device_role,
                    "token": token,
                    "token_id": pairing["token_id"],
                    "expires_at": pairing.get("expires_at") or "",
                    "status": "active",
                    "client": client,
                    "last_seen_at": utc_now(),
                    "rebound_at": utc_now(),
                    "device_state": payload.get("device_state") if isinstance(payload.get("device_state"), dict) else {},
                }
            )
            binding.pop("needs_rebind_at", None)
            binding.pop("needs_rebind_reason", None)
        else:
            binding = {
                "device_id": device_id,
                "workspace_id": workspace_id,
                "device_role": device_role,
                "token": token,
                "token_id": pairing["token_id"],
                "expires_at": pairing.get("expires_at") or "",
                "status": "active",
                "client": client,
                "created_at": utc_now(),
                "last_seen_at": utc_now(),
                "device_state": payload.get("device_state") if isinstance(payload.get("device_state"), dict) else {},
            }
        if device_role == "remote_worker":
            binding["worker_id"] = device_id
        if device_role == "ios_terminal":
            binding["terminal_id"] = device_id
        if device_role == "account_console":
            binding["console_id"] = device_id
            binding["account_id"] = account_id
        replaced_at = utc_now()
        replaced_tokens: list[str] = []
        for existing_token, existing in state["device_bindings"].items():
            if existing_token == token or not isinstance(existing, dict):
                continue
            if existing.get("status") != "active":
                continue
            if normalize_workspace_id(existing.get("workspace_id")) != workspace_id:
                continue
            if str(existing.get("device_role") or "") != device_role:
                continue
            existing_id = str(existing.get("device_id") or existing.get("worker_id") or existing.get("terminal_id") or "")
            if device_role == "account_console":
                existing_id = str(existing.get("console_id") or existing_id)
            if existing_id != device_id:
                continue
            existing["status"] = "replaced"
            existing["replaced_at"] = replaced_at
            existing["replaced_by_token_id"] = pairing["token_id"]
            replaced_tokens.append(str(existing.get("token_id") or ""))
            old_pairing = state["pairing_tokens"].get(str(existing.get("token_id") or ""))
            if isinstance(old_pairing, dict):
                old_pairing["status"] = "replaced"
                old_pairing["replaced_at"] = replaced_at
                old_pairing["replaced_by_token_id"] = pairing["token_id"]
        state["device_bindings"][token] = binding
        pairing["status"] = "bound"
        pairing["bound_device_id"] = device_id
        pairing["bound_at"] = utc_now()
        self._append_event(
            state,
            "device_bound",
            {
                "device_id": device_id,
                "workspace_id": workspace_id,
                "account_id": account_id,
                "role": binding["device_role"],
                "replaced_tokens": replaced_tokens,
            },
        )
        self.save(state)
        return binding

    def revoke_device_binding(
        self,
        token_id: str,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        token_id = str(token_id or "").strip()
        binding_token = ""
        binding: dict[str, Any] | None = None
        for token, item in state["device_bindings"].items():
            if not isinstance(item, dict):
                continue
            if str(item.get("token_id") or "") == token_id:
                binding_token = str(token)
                binding = item
                break
        if not binding:
            raise KeyError(f"unknown device binding: {token_id}")
        if workspace_id and binding.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown device binding for workspace: {token_id}")
        binding["status"] = "revoked"
        binding["revoked_at"] = utc_now()
        binding["revoked_by"] = requested_by
        pairing = state["pairing_tokens"].get(token_id)
        if isinstance(pairing, dict):
            pairing["status"] = "revoked"
            pairing["revoked_at"] = binding["revoked_at"]
        self._append_event(
            state,
            "device_binding_revoked",
            {
                "workspace_id": binding.get("workspace_id") or "",
                "token_id": token_id,
                "device_id": binding.get("device_id") or "",
                "device_role": binding.get("device_role") or "",
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"binding": binding, "token": binding_token}

    def clear_binding_history(
        self,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else None
        deleted_tokens: list[str] = []
        deleted_pairings: list[str] = []
        for token, binding in list(state["device_bindings"].items()):
            if not isinstance(binding, dict):
                continue
            if workspace_filter and binding.get("workspace_id") != workspace_filter:
                continue
            if str(binding.get("status") or "") == "active":
                continue
            deleted_tokens.append(str(binding.get("token_id") or token))
            del state["device_bindings"][token]
        for token_id, pairing in list(state["pairing_tokens"].items()):
            if not isinstance(pairing, dict):
                continue
            if workspace_filter and pairing.get("workspace_id") != workspace_filter:
                continue
            if str(pairing.get("status") or "") in {"pending", "bound"}:
                continue
            deleted_pairings.append(str(token_id))
            del state["pairing_tokens"][token_id]
        if deleted_tokens or deleted_pairings:
            self._append_event(
                state,
                "binding_history_cleared",
                {
                    "workspace_id": workspace_filter or "",
                    "deleted_bindings": len(deleted_tokens),
                    "deleted_pairings": len(deleted_pairings),
                    "requested_by": requested_by,
                },
            )
            self.save(state)
        return {
            "ok": True,
            "deleted_bindings": deleted_tokens,
            "deleted_pairings": deleted_pairings,
            "deleted_binding_count": len(deleted_tokens),
            "deleted_pairing_count": len(deleted_pairings),
        }

    def authenticate_token(self, token: str | None, *, required_role: str = "") -> dict[str, Any] | None:
        token = str(token or "").strip()
        if not token:
            return None
        state = self.load()
        binding = state["device_bindings"].get(token)
        if not isinstance(binding, dict):
            return None
        status = str(binding.get("status") or "")
        expires_at = str(binding.get("expires_at") or "").strip()
        if expires_at and parse_time(expires_at) <= datetime.now(UTC):
            return None
        if status == "needs_rebind" and str(binding.get("device_role") or "") == "android_bridge":
            pairing = state["pairing_tokens"].get(str(binding.get("token_id") or ""))
            if not isinstance(pairing, dict) or parse_time(str(pairing.get("expires_at") or "")) <= datetime.now(UTC):
                return None
            binding["status"] = "active"
            binding["recovered_by_heartbeat_at"] = utc_now()
            binding.pop("needs_rebind_at", None)
            binding.pop("needs_rebind_reason", None)
            pairing["status"] = "bound"
            pairing["recovered_by_heartbeat_at"] = binding["recovered_by_heartbeat_at"]
        elif status != "active":
            return None
        if required_role and binding.get("device_role") != required_role:
            return None
        if str(binding.get("device_role") or "") == "account_console":
            account_id = self._normalize_account_id(binding.get("account_id") or DEFAULT_ACCOUNT_ID)
            self._assert_account_active(state, account_id)
        now = datetime.now(UTC)
        last_seen = parse_time(str(binding.get("last_seen_at") or binding.get("created_at") or ""))
        if (now - last_seen).total_seconds() >= 30:
            with self._lock:
                with locked_state_path(self.state_file):
                    current, _ = self._load_locked()
                    current_binding = current["device_bindings"].get(token)
                    if not isinstance(current_binding, dict) or current_binding.get("status") != "active":
                        return None
                    current_binding["last_seen_at"] = now.isoformat()
                    self._save_locked(current)
                    binding = current_binding
        return dict(binding)

    def _remote_worker_binding_from_payload(self, state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        token = str(payload.get("token") or payload.get("pairing_token") or "").strip()
        if not token:
            return None
        binding = state["device_bindings"].get(token)
        if not isinstance(binding, dict) or binding.get("status") != "active" or binding.get("device_role") != "remote_worker":
            raise PermissionError("invalid remote worker pairing token")
        expires_at = str(binding.get("expires_at") or "").strip()
        if expires_at and parse_time(expires_at) <= datetime.now(UTC):
            raise PermissionError("remote worker pairing token expired")
        binding["last_seen_at"] = utc_now()
        return binding

    def record_artifact(self, payload: dict[str, Any], *, client: str = "", default_source: str = "unknown") -> dict[str, Any]:
        assert_no_sensitive_payload(payload, allowed_root_keys=DEFAULT_ALLOWED_ROOT_SECRET_KEYS)
        state = self.load()
        workspace_id = normalize_workspace_id(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        source = str(payload.get("source") or default_source or "unknown")
        device_id = str(payload.get("device_id") or "")
        owner_agent_id = str(payload.get("owner_agent_id") or "")
        task_id = str(payload.get("task_id") or "")
        purpose = str(payload.get("purpose") or "mobile_work_image")
        tags = [str(item) for item in payload.get("tags") or [] if str(item)]
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            single = {
                "name": payload.get("name") or payload.get("filename") or "artifact.txt",
                "mime_type": payload.get("mime_type") or payload.get("type") or "text/plain",
            }
            for key in ("text", "base64", "data_url", "path", "uri"):
                if key in payload:
                    single[key] = payload[key]
            files = [single]

        artifact_id = f"art_{int(time.time() * 1000)}_{sha12(json.dumps(payload, ensure_ascii=False, sort_keys=True)[:4000])}"
        created_at = utc_now()
        decoded_files: list[dict[str, Any]] = []
        total_size = 0
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            name = safe_name(item.get("name") or item.get("path") or f"artifact-{index + 1}.bin", f"artifact-{index + 1}.bin")
            content, mime_type = self._decode_file_payload(item)
            artifact_policy = self._workspace_artifact_policy(state, workspace_id)
            max_file_bytes = int(artifact_policy.get("max_file_bytes") or 0)
            if max_file_bytes and len(content) > max_file_bytes:
                raise ValueError(f"artifact file is too large: {name}")
            if len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError(f"artifact file exceeds hard limit: {name}")
            total_size += len(content)
            decoded_files.append(
                {
                    "name": name,
                    "mime_type": str(item.get("mime_type") or item.get("type") or mime_type),
                    "content": content,
                }
            )
        if not decoded_files:
            raise ValueError("artifact payload did not include any files")

        artifact_policy = self._workspace_artifact_policy(state, workspace_id)
        quota = self._assert_artifact_quota_available(state, workspace_id, total_size, artifact_policy)
        backend = str(artifact_policy.get("backend") or "local_disk")
        storage_root = "" if backend == "s3" else str(self._artifact_storage_root(artifact_policy))
        stored_files: list[dict[str, Any]] = []
        for item in decoded_files:
            content = item["content"]
            stored_files.append(self._store_artifact_file(artifact_policy, workspace_id, artifact_id, str(item["name"]), str(item["mime_type"]), content))

        expires_at = payload.get("expires_at")
        if not expires_at:
            expires_at = (datetime.now(UTC) + timedelta(hours=int(artifact_policy.get("default_ttl_hours") or DEFAULT_ARTIFACT_TTL_HOURS))).isoformat()
        artifact = {
            "artifact_id": artifact_id,
            "workspace_id": workspace_id,
            "backend": backend,
            "storage_root": storage_root,
            "bucket": str(artifact_policy.get("s3_bucket") or "") if backend == "s3" else "",
            "s3": self._artifact_s3_metadata(artifact_policy) if backend == "s3" else {},
            "source": source,
            "client": client,
            "device_id": device_id,
            "owner_agent_id": owner_agent_id,
            "task_id": task_id,
            "purpose": purpose,
            "tags": tags,
            "status": "available",
            "created_at": created_at,
            "expires_at": expires_at,
            "size_bytes": total_size,
            "quota": quota,
            "files": stored_files,
        }
        state["artifacts"][artifact_id] = artifact
        self._append_event(
            state,
            "artifact_ingested",
            {"workspace_id": workspace_id, "artifact_id": artifact_id, "source": source, "file_count": len(stored_files)},
        )
        self.save(state)
        return artifact

    def artifact_file(
        self,
        artifact_id: str,
        *,
        file_index: int = 0,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        artifact_id = str(artifact_id or "").strip()
        if artifact_id not in state["artifacts"]:
            raise KeyError(f"unknown artifact: {artifact_id}")
        artifact = state["artifacts"][artifact_id]
        if workspace_id and artifact.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown artifact for workspace: {artifact_id}")
        if artifact.get("status") != "available":
            raise ValueError(f"artifact is not available: {artifact_id}")
        files = artifact.get("files") if isinstance(artifact.get("files"), list) else []
        if file_index < 0 or file_index >= len(files):
            raise KeyError(f"unknown artifact file index: {file_index}")
        file_meta = files[file_index]
        if not isinstance(file_meta, dict):
            raise ValueError(f"invalid artifact file metadata: {artifact_id}")
        path = self._materialize_artifact_file(artifact, file_meta)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        return {
            "artifact": artifact,
            "file": file_meta,
            "path": path,
            "filename": str(file_meta.get("name") or path.name),
            "mime_type": str(file_meta.get("mime_type") or "application/octet-stream"),
            "size_bytes": int(file_meta.get("size_bytes") or path.stat().st_size),
        }

    def list_artifact_files(
        self,
        *,
        workspace_id: str | None = None,
        device_id: str | None = None,
        source: str | None = None,
        status: str = "available",
        limit: int = 100,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else ""
        device_filter = str(device_id or "").strip()
        source_filter = str(source or "").strip()
        status_filter = str(status or "").strip()
        limit = max(1, min(int(limit or 100), 500))
        artifacts: list[dict[str, Any]] = []
        file_count = 0
        total_size = 0
        for artifact in self._recent(state["artifacts"].values(), limit=limit):
            if workspace_filter and artifact.get("workspace_id") != workspace_filter:
                continue
            if device_filter and str(artifact.get("device_id") or "") != device_filter:
                continue
            if source_filter and str(artifact.get("source") or "") != source_filter:
                continue
            if status_filter and str(artifact.get("status") or "") != status_filter:
                continue
            files = artifact.get("files") if isinstance(artifact.get("files"), list) else []
            artifact_files: list[dict[str, Any]] = []
            for index, item in enumerate(files):
                if not isinstance(item, dict):
                    continue
                artifact_files.append(
                    {
                        "file_index": index,
                        "name": str(item.get("name") or ""),
                        "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                        "size_bytes": int(item.get("size_bytes") or 0),
                    }
                )
            if not artifact_files and status_filter == "available":
                continue
            file_count += len(artifact_files)
            total_size += sum(int(item.get("size_bytes") or 0) for item in artifact_files)
            artifacts.append(
                {
                    "artifact_id": artifact.get("artifact_id") or "",
                    "workspace_id": artifact.get("workspace_id") or "",
                    "source": artifact.get("source") or "",
                    "device_id": artifact.get("device_id") or "",
                    "purpose": artifact.get("purpose") or "",
                    "status": artifact.get("status") or "",
                    "created_at": artifact.get("created_at") or "",
                    "updated_at": artifact.get("updated_at") or "",
                    "expires_at": artifact.get("expires_at") or "",
                    "size_bytes": int(artifact.get("size_bytes") or 0),
                    "file_count": len(artifact_files),
                    "files": artifact_files,
                }
            )
            if len(artifacts) >= limit:
                break
        return {
            "ok": True,
            "workspace_id": workspace_filter,
            "device_id": device_filter,
            "source": source_filter,
            "status": status_filter,
            "count": len(artifacts),
            "file_count": file_count,
            "total_size_bytes": total_size,
            "artifacts": artifacts,
        }

    def delete_artifact_file(
        self,
        artifact_id: str,
        *,
        file_index: int,
        workspace_id: str | None = None,
        device_id: str | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        artifact_id = str(artifact_id or "").strip()
        if artifact_id not in state["artifacts"]:
            raise KeyError(f"unknown artifact: {artifact_id}")
        artifact = state["artifacts"][artifact_id]
        if workspace_id and artifact.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown artifact for workspace: {artifact_id}")
        if device_id and str(artifact.get("device_id") or "") != str(device_id).strip():
            raise KeyError(f"unknown artifact for device: {artifact_id}")
        if artifact.get("status") != "available":
            raise ValueError(f"artifact is not available: {artifact_id}")
        files = artifact.get("files") if isinstance(artifact.get("files"), list) else []
        if file_index < 0 or file_index >= len(files):
            raise KeyError(f"unknown artifact file index: {file_index}")
        file_meta = files[file_index]
        if not isinstance(file_meta, dict):
            raise ValueError(f"invalid artifact file metadata: {artifact_id}")
        self._delete_artifact_file(artifact, file_meta)
        remaining = [item for index, item in enumerate(files) if index != file_index]
        artifact["files"] = remaining
        artifact["size_bytes"] = sum(int(item.get("size_bytes") or 0) for item in remaining if isinstance(item, dict))
        artifact["updated_at"] = utc_now()
        deleted_name = str(file_meta.get("name") or "")
        if not remaining:
            artifact["status"] = "deleted"
            artifact["deleted_at"] = utc_now()
        self._append_event(
            state,
            "artifact_file_deleted",
            {
                "workspace_id": artifact.get("workspace_id") or "",
                "artifact_id": artifact_id,
                "file_index": file_index,
                "file_name": deleted_name,
                "remaining_files": len(remaining),
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"artifact": artifact, "deleted_file": {"file_index": file_index, "name": deleted_name}, "remaining_files": len(remaining)}

    def cleanup_artifacts(
        self,
        *,
        older_than_hours: int = DEFAULT_ARTIFACT_TTL_HOURS,
        workspace_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        state = self.load()
        cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
        deleted: list[str] = []
        skipped: list[str] = []
        for artifact_id, artifact in list(state["artifacts"].items()):
            if workspace_id and artifact.get("workspace_id") != normalize_workspace_id(workspace_id):
                continue
            if artifact.get("status") == "deleted":
                continue
            created_at = parse_time(str(artifact.get("created_at") or ""))
            expires_at = parse_time(str(artifact.get("expires_at") or "")) if artifact.get("expires_at") else None
            expired = created_at <= cutoff or (expires_at is not None and expires_at <= datetime.now(UTC))
            if not expired:
                skipped.append(artifact_id)
                continue
            if not dry_run:
                for item in artifact.get("files") or []:
                    if not isinstance(item, dict):
                        continue
                    self._delete_artifact_file(artifact, item)
                artifact["status"] = "deleted"
                artifact["deleted_at"] = utc_now()
            deleted.append(artifact_id)
        if not dry_run:
            self._append_event(
                state,
                "artifact_cleanup",
                {
                    "workspace_id": normalize_workspace_id(workspace_id) if workspace_id else "",
                    "deleted": deleted,
                    "skipped_count": len(skipped),
                },
            )
            self.save(state)
        return {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}

    def android_heartbeat(self, payload: dict[str, Any], *, client: str = "") -> dict[str, Any]:
        assert_no_sensitive_payload(payload, allowed_root_keys=DEFAULT_ALLOWED_ROOT_SECRET_KEYS)
        wait_seconds = 0.0
        try:
            wait_seconds = float(payload.get("wait_seconds") or payload.get("long_poll_seconds") or 0)
        except (TypeError, ValueError):
            wait_seconds = 0.0
        wait_seconds = max(0.0, min(wait_seconds, 25.0))
        return self._android_heartbeat_once(payload, client=client, wait_seconds=wait_seconds)

    def _android_heartbeat_once(self, payload: dict[str, Any], *, client: str = "", wait_seconds: float = 0.0) -> dict[str, Any]:
        state = self.load()
        device_state = payload.get("device_state") if isinstance(payload.get("device_state"), dict) else {}
        device_id = safe_name(payload.get("device_id") or device_state.get("device_id") or f"android_{sha12(client or utc_now())}", "android_device")
        workspace_id = normalize_workspace_id(payload.get("workspace_id") or device_state.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        token = str(payload.get("token") or payload.get("pairing_token") or "").strip()
        if token and token in state["device_bindings"]:
            binding = state["device_bindings"][token]
            if str(binding.get("status") or "") == "needs_rebind" and str(binding.get("device_role") or "") == "android_bridge":
                pairing = state["pairing_tokens"].get(str(binding.get("token_id") or ""))
                if isinstance(pairing, dict) and parse_time(str(pairing.get("expires_at") or "")) > datetime.now(UTC):
                    binding["status"] = "active"
                    binding["recovered_by_heartbeat_at"] = utc_now()
                    binding.pop("needs_rebind_at", None)
                    binding.pop("needs_rebind_reason", None)
                    pairing["status"] = "bound"
                    pairing["recovered_by_heartbeat_at"] = binding["recovered_by_heartbeat_at"]
            workspace_id = normalize_workspace_id(binding.get("workspace_id") or workspace_id)
            device_id = safe_name(binding.get("device_id") or device_id, "android_device")
            binding["last_seen_at"] = utc_now()
            binding["client"] = client
            binding["device_state"] = device_state
        installed_apps = payload.get("installed_apps") if isinstance(payload.get("installed_apps"), list) else []
        command_results = payload.get("command_results") if isinstance(payload.get("command_results"), list) else []
        reported_capabilities = [str(item) for item in payload.get("capabilities") or [] if str(item)]
        reported_command_catalog = self._reported_android_command_catalog(payload.get("command_catalog"))
        default_capabilities = [
            "heartbeat",
            "link.share",
            "artifact.upload",
            "artifact.download",
            "image.share_to_app",
            "artifact.cache.cleanup",
            "artifact.cache.status",
            "device.status",
            "list_installed_apps",
            "android.ui_snapshot",
            "android.open_accessibility_settings",
            "android.open_bridge",
            "pdd.launch",
            "pdd.share_image",
            "pdd.create_listing",
            "app.launch",
            "app.close",
            "url.open",
            "clipboard.write",
        ]

        device = state["android_devices"].get(device_id, {})
        device.update(
            {
                "device_id": device_id,
                "workspace_id": workspace_id,
                "client": client,
                "status": "online",
                "last_seen_at": utc_now(),
                "state": device_state,
                "installed_apps": installed_apps[:100],
                "capabilities": reported_capabilities or default_capabilities,
                "command_catalog": reported_command_catalog or device.get("command_catalog") or [],
            }
        )
        device["diagnostic"] = self._android_device_diagnostic(device, [])
        state["android_devices"][device_id] = device

        recent_failures: list[dict[str, Any]] = []
        for result in command_results:
            if not isinstance(result, dict):
                continue
            command_id = str(result.get("command_id") or "")
            if command_id not in state["android_commands"]:
                continue
            command = state["android_commands"][command_id]
            command["status"] = str(result.get("status") or ("completed" if result.get("success") else "failed"))
            command["success"] = bool(result.get("success") or command["status"] == "completed")
            command["message"] = str(result.get("message") or "")
            command["result"] = result.get("result") if isinstance(result.get("result"), dict) else {}
            command["completed_at"] = utc_now()
            if command["status"] == "failed" or not command["success"]:
                recent_failures.append(command)
            self._append_event(
                state,
                "android_command_result",
                {
                    "command_id": command_id,
                    "workspace_id": command.get("workspace_id") or workspace_id,
                    "device_id": device_id,
                    "operation": command.get("operation") or "",
                    "status": command["status"],
                    "message": command["message"],
                    "artifact_id": command["result"].get("artifact_id") if isinstance(command.get("result"), dict) else "",
                },
            )
        if recent_failures:
            device["diagnostic"] = self._android_device_diagnostic(device, recent_failures)

        pending = self._deliver_pending_android_commands(state, device_id=device_id, workspace_id=workspace_id)
        saved_before_wait = False
        deadline = time.monotonic() + max(0.0, float(wait_seconds or 0.0))
        while not pending and time.monotonic() < deadline:
            if not saved_before_wait:
                self.save(state)
                saved_before_wait = True
            time.sleep(1.0)
            state = self.load()
            pending = self._deliver_pending_android_commands(state, device_id=device_id, workspace_id=workspace_id)

        self._append_event(
            state,
            "android_heartbeat",
            {"device_id": device_id, "workspace_id": workspace_id, "commands": len(pending), "results": len(command_results), "client": client},
        )
        self.save(state)
        return {"ok": True, "device_id": device_id, "commands": pending, "server_time": utc_now()}

    def _deliver_pending_android_commands(self, state: dict[str, Any], *, device_id: str, workspace_id: str) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for command in state["android_commands"].values():
            if command.get("status") != "queued":
                continue
            target = str(command.get("device_id") or "*")
            if target not in {"*", device_id}:
                continue
            if command.get("workspace_id") != workspace_id:
                continue
            command["status"] = "delivered"
            command["delivered_at"] = utc_now()
            command["delivered_to"] = device_id
            pending.append(
                {
                    "command_id": command["command_id"],
                    "operation": command["operation"],
                    "params": command.get("params") or {},
                }
            )
            if len(pending) >= 10:
                break
        return pending

    def queue_android_command(
        self,
        *,
        operation: str,
        params: dict[str, Any] | None = None,
        device_id: str = "*",
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        operation = str(operation or "").strip()
        if not operation:
            raise ValueError("operation is required")
        workspace_id = normalize_workspace_id(workspace_id)
        self._assert_android_operation_allowed(state, workspace_id, operation)
        command_device_id = safe_name(device_id or "*", "*") if device_id != "*" else "*"
        preflight = self._android_command_preflight(
            state,
            operation,
            params or {},
            device_id=command_device_id,
            workspace_id=workspace_id,
        )
        command_id = f"cmd_{int(time.time() * 1000)}_{sha12(operation + json.dumps(params or {}, sort_keys=True))}"
        command = {
            "command_id": command_id,
            "workspace_id": workspace_id,
            "device_id": command_device_id,
            "operation": operation,
            "params": params or {},
            "preflight": preflight,
            "status": "queued",
            "requested_by": requested_by,
            "created_at": utc_now(),
        }
        state["android_commands"][command_id] = command
        self._append_event(
            state,
            "android_command_queued",
            {
                "command_id": command_id,
                "workspace_id": workspace_id,
                "operation": operation,
                "device_id": command["device_id"],
                "requested_by": requested_by,
                "risk": preflight.get("risk") or "",
                "preflight_status": preflight.get("status") or "",
            },
        )
        self.save(state)
        return command

    def clear_android_commands(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        device_id: str = "",
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        device_filter = safe_name(device_id or "", "") if device_id else ""
        removed: list[str] = []
        for command_id, command in list(state["android_commands"].items()):
            if not isinstance(command, dict):
                continue
            if normalize_workspace_id(command.get("workspace_id") or DEFAULT_WORKSPACE_ID) != workspace_id:
                continue
            if device_filter and str(command.get("device_id") or "") not in {device_filter, "*"}:
                continue
            if str(command.get("status") or "") not in {"queued", "delivered"}:
                continue
            removed.append(command_id)
            del state["android_commands"][command_id]
        self._append_event(
            state,
            "android_commands_cleared",
            {
                "workspace_id": workspace_id,
                "device_id": device_filter,
                "removed": len(removed),
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"ok": True, "removed": len(removed), "command_ids": removed, "workspace_id": workspace_id, "device_id": device_filter}

    def set_device_workflow_state(
        self,
        *,
        workspace_id: str,
        device_id: str,
        workflow_id: str = DEFAULT_WORKFLOW_TEMPLATE_ID,
        enabled: bool = True,
        reason: str = "",
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        raw_device_id = str(device_id or "").strip()
        if not raw_device_id:
            raise ValueError("device_id is required")
        device_id = safe_name(raw_device_id, "android_device")
        workflow_id = str(workflow_id or DEFAULT_WORKFLOW_TEMPLATE_ID).strip() or DEFAULT_WORKFLOW_TEMPLATE_ID
        self._assert_workflow_template_allowed(state, workspace_id, workflow_id)
        control_id = self._device_workflow_control_id(workspace_id, device_id, workflow_id)
        now = utc_now()
        control = state["device_workflow_controls"].get(control_id, {})
        control.update(
            {
                "control_id": control_id,
                "workspace_id": workspace_id,
                "device_id": device_id,
                "workflow_id": workflow_id,
                "enabled": bool(enabled),
                "status": "enabled" if enabled else "paused",
                "reason": str(reason or ""),
                "updated_at": now,
                "updated_by": requested_by,
            }
        )
        control.setdefault("created_at", now)
        state["device_workflow_controls"][control_id] = control
        self._append_event(
            state,
            "device_workflow_state_updated",
            {
                "workspace_id": workspace_id,
                "device_id": device_id,
                "workflow_id": workflow_id,
                "enabled": bool(enabled),
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return control

    def add_device_workflow(
        self,
        *,
        workspace_id: str,
        device_id: str,
        workflow_id: str = DEFAULT_WORKFLOW_TEMPLATE_ID,
        enabled: bool = True,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        return self.set_device_workflow_state(
            workspace_id=workspace_id,
            device_id=device_id,
            workflow_id=workflow_id,
            enabled=enabled,
            reason="",
            requested_by=requested_by,
        )

    def delete_device_workflow(
        self,
        *,
        workspace_id: str,
        device_id: str,
        workflow_id: str = DEFAULT_WORKFLOW_TEMPLATE_ID,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        raw_device_id = str(device_id or "").strip()
        if not raw_device_id:
            raise ValueError("device_id is required")
        device_id = safe_name(raw_device_id, "android_device")
        workflow_id = str(workflow_id or DEFAULT_WORKFLOW_TEMPLATE_ID).strip() or DEFAULT_WORKFLOW_TEMPLATE_ID
        control_id = self._device_workflow_control_id(workspace_id, device_id, workflow_id)
        removed = state["device_workflow_controls"].pop(control_id, None)
        self._append_event(
            state,
            "device_workflow_deleted",
            {
                "workspace_id": workspace_id,
                "device_id": device_id,
                "workflow_id": workflow_id,
                "removed": bool(removed),
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"ok": True, "removed": bool(removed), "control_id": control_id, "workflow_id": workflow_id}

    def repair_device_workflow(
        self,
        *,
        workspace_id: str,
        device_id: str,
        workflow_id: str = DEFAULT_WORKFLOW_TEMPLATE_ID,
        repair_type: str = "status",
        requested_by: str = "management",
    ) -> dict[str, Any]:
        raw_device_id = str(device_id or "").strip()
        if not raw_device_id:
            raise ValueError("device_id is required")
        repair_type = str(repair_type or "status").strip()
        operation_by_repair = {
            "status": "device.status",
            "open_app": "app.launch",
            "open_pdd": "app.launch",
            "accessibility_settings": "android.open_accessibility_settings",
            "screenshot_permission": "android.screenshot.request_permission",
            "open_bridge": "android.open_bridge",
        }
        if repair_type not in operation_by_repair:
            raise ValueError(f"unsupported repair_type: {repair_type}")
        params: dict[str, Any] = {}
        if repair_type == "open_pdd":
            params["app_name"] = "com.xunmeng.pinduoduo"
        elif repair_type == "open_app":
            params["app_name"] = "com.spiritkin.mobilelinkbridge"
        command = self.queue_android_command(
            operation=operation_by_repair[repair_type],
            params=params,
            device_id=raw_device_id,
            workspace_id=workspace_id,
            requested_by=requested_by or "management",
        )
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        device_id = safe_name(raw_device_id, "android_device")
        workflow_id = str(workflow_id or DEFAULT_WORKFLOW_TEMPLATE_ID).strip() or DEFAULT_WORKFLOW_TEMPLATE_ID
        control_id = self._device_workflow_control_id(workspace_id, device_id, workflow_id)
        now = utc_now()
        control = state["device_workflow_controls"].get(control_id, {})
        control.update(
            {
                "control_id": control_id,
                "workspace_id": workspace_id,
                "device_id": device_id,
                "workflow_id": workflow_id,
                "enabled": bool(control.get("enabled", True)),
                "status": "repair_queued",
                "last_repair_type": repair_type,
                "last_repair_command_id": command["command_id"],
                "last_repair_at": now,
                "updated_at": now,
                "updated_by": requested_by,
            }
        )
        control.setdefault("created_at", now)
        state["device_workflow_controls"][control_id] = control
        self._append_event(
            state,
            "device_workflow_repair_queued",
            {
                "workspace_id": workspace_id,
                "device_id": device_id,
                "workflow_id": workflow_id,
                "repair_type": repair_type,
                "command_id": command["command_id"],
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"control": control, "command": command}

    def start_workflow_run(
        self,
        *,
        template_id: str = DEFAULT_WORKFLOW_TEMPLATE_ID,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        inputs: dict[str, Any] | None = None,
        requested_by: str = "management",
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id)
        if template_id not in state["workflow_templates"]:
            raise KeyError(f"unknown workflow template: {template_id}")
        template = state["workflow_templates"][template_id]
        workflow_inputs = inputs if isinstance(inputs, dict) else {}
        account_id = self._workspace_account_id(state, workspace_id)
        self._assert_account_active(state, account_id)
        self._assert_workflow_template_allowed(state, workspace_id, template_id)
        self._assert_device_workflow_enabled(state, workspace_id, template_id, workflow_inputs)
        governance = self._workflow_governance(state, workspace_id, template_id, workflow_inputs, requested_by)
        metered = str(template.get("metered") or "").strip()
        quota_consumption: dict[str, Any] | None = None
        if metered:
            try:
                metered_amount = int(workflow_inputs.get("metered_amount") or workflow_inputs.get(f"{metered}_count") or 1)
            except (TypeError, ValueError):
                metered_amount = 1
            quota_consumption = self.consume_metered_quota(
                account_id,
                kind=metered,
                amount=metered_amount,
                requested_by=requested_by,
                state=state,
            )
        workspace = state["workspaces"].get(workspace_id) if isinstance(state.get("workspaces"), dict) else None
        runtime_profile = self._runtime_profile(workspace if isinstance(workspace, dict) else {"workspace_id": workspace_id})
        run_id = f"run_{int(time.time() * 1000)}_{sha12(template_id + json.dumps(inputs or {}, sort_keys=True))}"
        task_id = f"wtask_{int(time.time() * 1000)}_{sha12(run_id)}"
        run = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "account_id": account_id,
            "template_id": template_id,
            "status": "queued",
            "requested_by": requested_by,
            "inputs": workflow_inputs,
            "governance": governance,
            "runtime_profile": runtime_profile,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "worker_task_id": task_id,
        }
        required_capabilities = [
            str(item)
            for item in (template.get("worker_required_capabilities") if isinstance(template.get("worker_required_capabilities"), list) else [])
            if str(item)
        ]
        task = {
            "task_id": task_id,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "account_id": account_id,
            "operation": template["worker_task_operation"],
            "status": "queued",
            "attempt": 0,
            "max_attempts": int(governance["budget"].get("max_retries") or 0) + 1,
            "required_capability": required_capabilities[0] if required_capabilities else "",
            "required_capabilities": required_capabilities,
            "inputs": workflow_inputs,
            "governance": governance,
            "budget": governance["budget"],
            "runtime_profile": runtime_profile,
            "created_at": utc_now(),
        }
        if quota_consumption:
            run["quota_consumption"] = quota_consumption
            task["quota_consumption"] = quota_consumption
        state["workflow_runs"][run_id] = run
        state["worker_tasks"][task_id] = task
        self._append_event(
            state,
            "workflow_run_started",
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "account_id": account_id,
                "template_id": template_id,
                "worker_task_id": task_id,
                "requested_by": requested_by,
                "promote_mode": governance["promote_mode"],
                "dry_run": governance["dry_run"],
                "debug": governance["debug"],
            },
        )
        self.save(state)
        return {"run": run, "worker_task": task}

    def cancel_workflow_run(self, run_id: str, *, workspace_id: str | None = None, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        run_id = str(run_id or "").strip()
        if run_id not in state["workflow_runs"]:
            raise KeyError(f"unknown workflow run: {run_id}")
        run = state["workflow_runs"][run_id]
        if workspace_id and run.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown workflow run for workspace: {run_id}")
        status = str(run.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            raise ValueError(f"workflow run is already terminal: {run_id}")
        run["status"] = "cancelled"
        run["updated_at"] = utc_now()
        run["cancelled_at"] = utc_now()
        run["cancelled_by"] = requested_by
        task_id = str(run.get("worker_task_id") or "")
        task = state["worker_tasks"].get(task_id) if task_id else None
        if isinstance(task, dict) and str(task.get("status") or "") not in {"completed", "failed", "cancelled"}:
            task["status"] = "cancelled"
            task["completed_at"] = utc_now()
            task["cancelled_by"] = requested_by
        self._append_event(
            state,
            "workflow_run_cancelled",
            {
                "run_id": run_id,
                "workspace_id": run.get("workspace_id") or "",
                "worker_task_id": task_id,
                "requested_by": requested_by,
                "status": "cancelled",
            },
        )
        self.save(state)
        return {"run": run, "worker_task": task}

    def retry_workflow_run(self, run_id: str, *, workspace_id: str | None = None, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        run_id = str(run_id or "").strip()
        if run_id not in state["workflow_runs"]:
            raise KeyError(f"unknown workflow run: {run_id}")
        original = state["workflow_runs"][run_id]
        if workspace_id and original.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown workflow run for workspace: {run_id}")
        template_id = str(original.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID)
        inputs = original.get("inputs") if isinstance(original.get("inputs"), dict) else {}
        workspace = str(original.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        result = self.start_workflow_run(
            template_id=template_id,
            workspace_id=workspace,
            inputs=inputs,
            requested_by=requested_by,
        )
        retry_state = self.load()
        next_run = retry_state["workflow_runs"][result["run"]["run_id"]]
        next_run["retry_of"] = run_id
        retry_state["worker_tasks"][result["worker_task"]["task_id"]]["retry_of"] = str(original.get("worker_task_id") or "")
        original = retry_state["workflow_runs"][run_id]
        original["retried_by_run_id"] = next_run["run_id"]
        original["updated_at"] = utc_now()
        self._append_event(
            retry_state,
            "workflow_run_retried",
            {
                "run_id": run_id,
                "new_run_id": next_run["run_id"],
                "workspace_id": workspace,
                "template_id": template_id,
                "requested_by": requested_by,
            },
        )
        self.save(retry_state)
        return {"run": next_run, "worker_task": retry_state["worker_tasks"][result["worker_task"]["task_id"]], "retry_of": run_id}

    def delete_workflow_run(self, run_id: str, *, workspace_id: str | None = None, requested_by: str = "management") -> dict[str, Any]:
        state = self.load()
        run_id = str(run_id or "").strip()
        if run_id not in state["workflow_runs"]:
            raise KeyError(f"unknown workflow run: {run_id}")
        run = state["workflow_runs"][run_id]
        if workspace_id and run.get("workspace_id") != normalize_workspace_id(workspace_id):
            raise KeyError(f"unknown workflow run for workspace: {run_id}")
        removed_tasks: list[str] = []
        task_id = str(run.get("worker_task_id") or "")
        if task_id and task_id in state["worker_tasks"]:
            removed_tasks.append(task_id)
            del state["worker_tasks"][task_id]
        for other_task_id, task in list(state["worker_tasks"].items()):
            if isinstance(task, dict) and str(task.get("run_id") or "") == run_id:
                removed_tasks.append(other_task_id)
                del state["worker_tasks"][other_task_id]
        del state["workflow_runs"][run_id]
        self._append_event(
            state,
            "workflow_run_deleted",
            {
                "run_id": run_id,
                "workspace_id": run.get("workspace_id") or "",
                "template_id": run.get("template_id") or "",
                "removed_worker_tasks": removed_tasks,
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {"ok": True, "run_id": run_id, "deleted_worker_tasks": removed_tasks}

    def clear_workflow_runs(
        self,
        *,
        workspace_id: str | None = None,
        requested_by: str = "management",
        include_active: bool = False,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else ""
        deleted_runs: list[str] = []
        deleted_tasks: list[str] = []
        for run_id, run in list(state["workflow_runs"].items()):
            if not isinstance(run, dict):
                continue
            if workspace_filter and normalize_workspace_id(run.get("workspace_id") or "") != workspace_filter:
                continue
            status = str(run.get("status") or "")
            if not include_active and status not in TERMINAL_WORKFLOW_STATUSES:
                continue
            deleted_runs.append(run_id)
            task_id = str(run.get("worker_task_id") or "")
            if task_id and task_id in state["worker_tasks"]:
                deleted_tasks.append(task_id)
                del state["worker_tasks"][task_id]
            del state["workflow_runs"][run_id]
        for task_id, task in list(state["worker_tasks"].items()):
            if not isinstance(task, dict):
                continue
            run_id = str(task.get("run_id") or "")
            if run_id and run_id in deleted_runs:
                deleted_tasks.append(task_id)
                del state["worker_tasks"][task_id]
        self._append_event(
            state,
            "workflow_runs_cleared",
            {
                "workspace_id": workspace_filter,
                "deleted_run_count": len(deleted_runs),
                "deleted_worker_task_count": len(deleted_tasks),
                "include_active": include_active,
                "requested_by": requested_by,
            },
        )
        self.save(state)
        return {
            "ok": True,
            "deleted_run_count": len(deleted_runs),
            "deleted_worker_task_count": len(deleted_tasks),
            "deleted_run_ids": deleted_runs,
        }

    def worker_heartbeat(self, payload: dict[str, Any], *, client: str = "") -> dict[str, Any]:
        state = self.load()
        reclaimed = self._reclaim_expired_worker_tasks(state)
        binding = self._remote_worker_binding_from_payload(state, payload)
        if binding:
            worker_id = safe_name(binding.get("worker_id") or binding.get("device_id"), "remote_worker")
            workspace_id = normalize_workspace_id(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        else:
            worker_id = safe_name(payload.get("worker_id") or f"worker_{sha12(client or utc_now())}", "remote_worker")
            workspace_id = normalize_workspace_id(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        capabilities = [str(item) for item in payload.get("capabilities") or [] if str(item)]
        authorized_capabilities = self._authorized_worker_capabilities(state, workspace_id, capabilities)
        worker = state["remote_workers"].get(worker_id, {})
        worker.update(
            {
                "worker_id": worker_id,
                "workspace_id": workspace_id,
                "client": client,
                "status": "online",
                "last_seen_at": utc_now(),
                "capabilities": capabilities,
                "authorized_capabilities": authorized_capabilities,
                "version": str(payload.get("version") or ""),
            }
        )
        if binding:
            worker["binding_token_id"] = str(binding.get("token_id") or "")
            worker["paired"] = True
        state["remote_workers"][worker_id] = worker
        assignments: list[dict[str, Any]] = []
        for task in state["worker_tasks"].values():
            if task.get("status") != "queued":
                continue
            if task.get("workspace_id") != workspace_id:
                continue
            allowed, reason = self._worker_can_claim_task(task, capabilities=capabilities, authorized_capabilities=authorized_capabilities)
            if not allowed:
                self._append_event(
                    state,
                    "worker_task_claim_skipped",
                    {
                        "worker_id": worker_id,
                        "workspace_id": workspace_id,
                        "task_id": task.get("task_id") or "",
                        "reason": reason,
                    },
                )
                continue
            task["status"] = "assigned"
            task["worker_id"] = worker_id
            task["assigned_at"] = utc_now()
            task["attempt"] = int(task.get("attempt") or 0) + 1
            task["lease_expires_at"] = self._worker_task_lease_expires_at(task)
            assignments.append(task)
            if len(assignments) >= 3:
                break
        self._append_event(
            state,
            "worker_heartbeat",
            {
                "worker_id": worker_id,
                "workspace_id": workspace_id,
                "assignments": len(assignments),
                "reclaimed": len(reclaimed),
                "client": client,
            },
        )
        self.save(state)
        return {"ok": True, "worker_id": worker_id, "tasks": assignments, "reclaimed_tasks": reclaimed, "server_time": utc_now()}

    def worker_result(self, payload: dict[str, Any], *, client: str = "") -> dict[str, Any]:
        state = self.load()
        binding = self._remote_worker_binding_from_payload(state, payload)
        task_id = str(payload.get("task_id") or "")
        if task_id not in state["worker_tasks"]:
            raise KeyError(f"unknown worker task: {task_id}")
        task = state["worker_tasks"][task_id]
        if str(task.get("status") or "") != "assigned":
            raise PermissionError(f"worker task is not assigned: {task_id}")
        if task.get("lease_expires_at") and parse_time(str(task.get("lease_expires_at") or "")) <= datetime.now(UTC):
            raise PermissionError(f"worker task lease expired: {task_id}")
        if binding:
            worker_id = safe_name(binding.get("worker_id") or binding.get("device_id"), "remote_worker")
            workspace_id = normalize_workspace_id(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
            if normalize_workspace_id(task.get("workspace_id")) != workspace_id:
                raise PermissionError(f"worker token cannot report a task outside workspace: {task_id}")
            if str(task.get("worker_id") or "") != worker_id:
                raise PermissionError(f"worker is not assigned to task: {task_id}")
        elif payload.get("worker_id") and str(payload.get("worker_id")) != str(task.get("worker_id") or ""):
            raise PermissionError(f"worker is not assigned to task: {task_id}")
        status = str(payload.get("status") or ("completed" if payload.get("success") else "failed"))
        result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        assert_no_sensitive_payload(result_payload)
        budget_result = self._worker_budget_result(task, result_payload)
        if budget_result["blocked"]:
            raise PermissionError(f"worker task budget exceeded: {'; '.join(budget_result['violations'])}")
        governance = task.get("governance") if isinstance(task.get("governance"), dict) else {}
        if governance.get("dry_run") and self._result_attempts_publish(result_payload):
            raise PermissionError("dry-run worker task cannot report publish/submit side effects")
        task["status"] = status
        task["completed_at"] = utc_now()
        task["lease_expires_at"] = ""
        task["result"] = result_payload
        task["budget_result"] = budget_result
        run_id = str(task.get("run_id") or "")
        if run_id in state["workflow_runs"]:
            run = state["workflow_runs"][run_id]
            run["status"] = status
            run["updated_at"] = utc_now()
            run["result"] = task["result"]
            run["budget_result"] = budget_result
        self._append_event(
            state,
            "worker_task_result",
            {
                "task_id": task_id,
                "run_id": run_id,
                "workspace_id": task.get("workspace_id") or "",
                "worker_id": task.get("worker_id") or "",
                "status": status,
                "client": client,
                "budget_status": budget_result["status"],
            },
        )
        self.save(state)
        return {"ok": True, "task": task}

    def management_action(self, payload: dict[str, Any], *, client: str = "") -> dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        workspace_id = normalize_workspace_id(payload.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        state = self.load()
        self._assert_account_control_allowed(state, action, payload, workspace_id)
        self._assert_control_action_allowed(state, workspace_id, action, payload)
        if action == "create_account":
            plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            return self.create_account(
                account_id=str(payload.get("account_id") or ""),
                name=str(payload.get("name") or ""),
                plan=plan,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "get_account_usage":
            account_id = str(payload.get("account_id") or self._workspace_account_id(state, workspace_id) or DEFAULT_ACCOUNT_ID)
            return self.get_account(account_id)
        if action == "list_accounts":
            return self.list_accounts()
        if action == "assign_workspace_to_account":
            return self.assign_workspace_to_account(
                workspace_id,
                str(payload.get("account_id") or DEFAULT_ACCOUNT_ID),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "update_account_plan":
            plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            if not plan and isinstance(payload.get("quotas"), dict):
                plan = {"quotas": payload.get("quotas")}
            return self.update_account_plan(
                str(payload.get("account_id") or DEFAULT_ACCOUNT_ID),
                plan,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "set_account_status":
            return self.set_account_status(
                str(payload.get("account_id") or DEFAULT_ACCOUNT_ID),
                str(payload.get("status") or "active"),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "register_workspace":
            return {"workspace": self.ensure_workspace(workspace_id, str(payload.get("name") or workspace_id), account_id=str(payload.get("account_id") or DEFAULT_ACCOUNT_ID))}
        if action == "update_workspace_policy":
            policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
            return self.update_workspace_policy(
                workspace_id,
                policy,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "update_runtime_profile":
            profile = payload.get("runtime_profile") if isinstance(payload.get("runtime_profile"), dict) else {}
            return self.update_runtime_profile(
                workspace_id,
                profile,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "approve_workflow_promotion":
            return self.approve_promotion(
                workspace_id,
                str(payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "queue_android_command":
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            return {
                "command": self.queue_android_command(
                    operation=str(payload.get("operation") or ""),
                    params=params,
                    device_id=str(payload.get("device_id") or "*"),
                    workspace_id=workspace_id,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "clear_android_commands":
            return self.clear_android_commands(
                workspace_id=workspace_id,
                device_id=str(payload.get("device_id") or ""),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "add_device_workflow":
            return {
                "control": self.add_device_workflow(
                    workspace_id=workspace_id,
                    device_id=str(payload.get("device_id") or ""),
                    workflow_id=str(payload.get("workflow_id") or payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                    enabled=bool(payload.get("enabled", True)),
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "set_device_workflow_state":
            return {
                "control": self.set_device_workflow_state(
                    workspace_id=workspace_id,
                    device_id=str(payload.get("device_id") or ""),
                    workflow_id=str(payload.get("workflow_id") or payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                    enabled=bool(payload.get("enabled", True)),
                    reason=str(payload.get("reason") or ""),
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "delete_device_workflow":
            return self.delete_device_workflow(
                workspace_id=workspace_id,
                device_id=str(payload.get("device_id") or ""),
                workflow_id=str(payload.get("workflow_id") or payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "repair_device_workflow":
            return self.repair_device_workflow(
                workspace_id=workspace_id,
                device_id=str(payload.get("device_id") or ""),
                workflow_id=str(payload.get("workflow_id") or payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                repair_type=str(payload.get("repair_type") or "status"),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "start_workflow_run":
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            return {
                "workflow": self.start_workflow_run(
                    template_id=str(payload.get("template_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                    workspace_id=workspace_id,
                    inputs=inputs,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "cancel_workflow_run":
            return {
                "workflow": self.cancel_workflow_run(
                    str(payload.get("run_id") or ""),
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "retry_workflow_run":
            return {
                "workflow": self.retry_workflow_run(
                    str(payload.get("run_id") or ""),
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "delete_workflow_run":
            return {
                "workflow": self.delete_workflow_run(
                    str(payload.get("run_id") or ""),
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "clear_workflow_runs":
            return {
                "workflow": self.clear_workflow_runs(
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                    include_active=bool(payload.get("include_active")),
                )
            }
        if action == "approve_pairing_request":
            return {
                "pairing": self.approve_pairing_request(
                    str(payload.get("request_id") or payload.get("token_id") or ""),
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    ttl_minutes=int(payload.get("ttl_minutes") or DEFAULT_PAIRING_TTL_MINUTES),
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "reject_pairing_request":
            return self.reject_pairing_request(
                str(payload.get("request_id") or payload.get("token_id") or ""),
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "cancel_pairing_token":
            return self.cancel_pairing_token(
                str(payload.get("token_id") or ""),
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "delete_pairing_token":
            return self.delete_pairing_token(
                str(payload.get("token_id") or ""),
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "clear_pairing_history":
            return self.clear_pairing_history(
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "clear_binding_history":
            return self.clear_binding_history(
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "clear_ios_terminal_history":
            return self.clear_ios_terminal_history(
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                keep_latest=int(payload.get("keep_latest") or 1),
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "revoke_device_binding":
            return self.revoke_device_binding(
                str(payload.get("token_id") or ""),
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                requested_by=str(payload.get("requested_by") or client or "management"),
            )
        if action == "cleanup_artifacts":
            return {
                "cleanup": self.cleanup_artifacts(
                    older_than_hours=int(payload.get("older_than_hours") or DEFAULT_ARTIFACT_TTL_HOURS),
                    workspace_id=workspace_id,
                    dry_run=bool(payload.get("dry_run")),
                )
            }
        if action == "delete_artifact_file":
            return {
                "artifact": self.delete_artifact_file(
                    str(payload.get("artifact_id") or ""),
                    file_index=int(payload.get("file_index") or 0),
                    workspace_id=workspace_id if payload.get("workspace_id") else None,
                    requested_by=str(payload.get("requested_by") or client or "management"),
                )
            }
        if action == "cleanup_state":
            return {
                "cleanup": self.cleanup_state(
                    older_than_hours=int(payload.get("older_than_hours") or DEFAULT_ARTIFACT_TTL_HOURS),
                    workspace_id=workspace_id,
                    dry_run=bool(payload.get("dry_run")),
                )
            }
        if action == "validate_state":
            return {"validation": self.validate_state(workspace_id=workspace_id if payload.get("workspace_id") else None)}
        if action == "action_log":
            return self.action_log(
                workspace_id=workspace_id if payload.get("workspace_id") else None,
                action=str(payload.get("filter_action") or ""),
                status=str(payload.get("status") or ""),
                limit=int(payload.get("limit") or 50),
            )
        if action == "snapshot":
            return self.snapshot(workspace_id=workspace_id if payload.get("workspace_id") else None)
        raise ValueError(f"unknown management action: {action}")

    def action_log(
        self,
        *,
        workspace_id: str | None = None,
        action: str = "",
        status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        state = self.load()
        workspace_id = normalize_workspace_id(workspace_id) if workspace_id else ""
        action = str(action or "").strip()
        status = str(status or "").strip()
        limit = max(1, min(int(limit or 50), 200))
        items = []
        for item in reversed(state["action_log"]):
            if not isinstance(item, dict):
                continue
            if workspace_id and item.get("workspace_id") != workspace_id:
                continue
            if action and item.get("action") != action:
                continue
            if status and item.get("status") != status:
                continue
            items.append(dict(item))
            if len(items) >= limit:
                break
        return {
            "ok": True,
            "count": len(items),
            "filters": {
                "workspace_id": workspace_id,
                "action": action,
                "status": status,
                "limit": limit,
            },
            "items": items,
        }

    def cleanup_state(
        self,
        *,
        older_than_hours: int = DEFAULT_ARTIFACT_TTL_HOURS,
        workspace_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else None
        artifact_cleanup = self.cleanup_artifacts(
            older_than_hours=older_than_hours,
            workspace_id=workspace_filter,
            dry_run=dry_run,
        )
        state = self.load()
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=older_than_hours)
        reclaimed_worker_tasks = [] if dry_run else self._reclaim_expired_worker_tasks(state)
        expired_pairing_tokens: list[str] = []
        pruned_android_commands: list[str] = []
        offline_workers: list[str] = []
        offline_android_devices: list[str] = []
        offline_ios_terminals: list[str] = []

        for token_id, token in list(state["pairing_tokens"].items()):
            if not isinstance(token, dict):
                continue
            if workspace_filter and token.get("workspace_id") != workspace_filter:
                continue
            if token.get("status") != "pending":
                continue
            if parse_time(str(token.get("expires_at") or "")) > now:
                continue
            expired_pairing_tokens.append(token_id)
            if not dry_run:
                token["status"] = "expired"
                token["expired_at"] = utc_now()

        terminal_collections = (
            ("remote_workers", "worker_id", "last_seen_at", offline_workers),
            ("android_devices", "device_id", "last_seen_at", offline_android_devices),
            ("ios_terminals", "terminal_id", "last_seen_at", offline_ios_terminals),
        )
        for collection_name, id_key, time_key, changed in terminal_collections:
            for item_id, item in state[collection_name].items():
                if not isinstance(item, dict):
                    continue
                if workspace_filter and item.get("workspace_id") != workspace_filter:
                    continue
                if item.get("status") != "online":
                    continue
                if parse_time(str(item.get(time_key) or "")) > cutoff:
                    continue
                changed.append(str(item.get(id_key) or item_id))
                if not dry_run:
                    item["status"] = "offline"
                    item["offline_at"] = utc_now()

        for command_id, command in list(state["android_commands"].items()):
            if not isinstance(command, dict):
                continue
            if workspace_filter and command.get("workspace_id") != workspace_filter:
                continue
            if command.get("status") not in {"completed", "failed", "cancelled", "expired"}:
                continue
            if parse_time(str(command.get("completed_at") or command.get("created_at") or "")) > cutoff:
                continue
            pruned_android_commands.append(command_id)
            if not dry_run:
                del state["android_commands"][command_id]

        result = {
            "artifact_cleanup": artifact_cleanup,
            "expired_pairing_tokens": expired_pairing_tokens,
            "reclaimed_worker_tasks": reclaimed_worker_tasks,
            "offline_workers": offline_workers,
            "offline_android_devices": offline_android_devices,
            "offline_ios_terminals": offline_ios_terminals,
            "pruned_android_commands": pruned_android_commands,
            "workspace_id": workspace_filter,
            "older_than_hours": older_than_hours,
            "dry_run": dry_run,
        }
        if not dry_run:
            self._append_event(
                state,
                "state_cleanup",
                {
                    "workspace_id": workspace_filter or "",
                    "expired_pairing_tokens": len(expired_pairing_tokens),
                    "reclaimed_worker_tasks": len(reclaimed_worker_tasks),
                    "offline_workers": len(offline_workers),
                    "offline_android_devices": len(offline_android_devices),
                    "offline_ios_terminals": len(offline_ios_terminals),
                    "pruned_android_commands": len(pruned_android_commands),
                    "deleted_artifacts": len(artifact_cleanup.get("deleted") or []),
                },
            )
            self.save(state)
        return result

    def validate_state(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        state = self.load()
        workspace_filter = normalize_workspace_id(workspace_id) if workspace_id else None
        required_collections = [
            "accounts",
            "workspaces",
            "workflow_templates",
            "workflow_runs",
            "worker_tasks",
            "remote_workers",
            "android_devices",
            "android_commands",
            "ios_terminals",
            "pairing_tokens",
            "device_bindings",
            "artifacts",
            "mobile_links",
        ]
        errors: list[str] = []
        warnings: list[str] = []
        missing_collections = [key for key in required_collections if not isinstance(state.get(key), dict)]
        missing_lists = [key for key in ("events", "action_log") if not isinstance(state.get(key), list)]
        if self._state_version(state) != STATE_VERSION:
            errors.append(f"state version mismatch: {state.get('version')} != {STATE_VERSION}")
        if not isinstance(state.get("schema"), dict):
            errors.append("schema metadata is missing")
        if missing_collections:
            errors.append("missing collections: " + ", ".join(missing_collections))
        if missing_lists:
            errors.append("missing lists: " + ", ".join(missing_lists))

        orphan_worker_tasks: list[str] = []
        for task_id, task in state.get("worker_tasks", {}).items():
            if not isinstance(task, dict):
                warnings.append(f"invalid worker_task: {task_id}")
                continue
            if workspace_filter and task.get("workspace_id") != workspace_filter:
                continue
            run_id = str(task.get("run_id") or "")
            if run_id and run_id not in state.get("workflow_runs", {}):
                orphan_worker_tasks.append(str(task_id))

        orphan_workflow_runs: list[str] = []
        for run_id, run in state.get("workflow_runs", {}).items():
            if not isinstance(run, dict):
                warnings.append(f"invalid workflow_run: {run_id}")
                continue
            if workspace_filter and run.get("workspace_id") != workspace_filter:
                continue
            task_id = str(run.get("worker_task_id") or "")
            if task_id and task_id not in state.get("worker_tasks", {}):
                orphan_workflow_runs.append(str(run_id))

        missing_artifact_files: list[dict[str, Any]] = []
        for artifact_id, artifact in state.get("artifacts", {}).items():
            if not isinstance(artifact, dict):
                warnings.append(f"invalid artifact: {artifact_id}")
                continue
            if workspace_filter and artifact.get("workspace_id") != workspace_filter:
                continue
            if artifact.get("status") != "available":
                continue
            for index, item in enumerate(artifact.get("files") if isinstance(artifact.get("files"), list) else []):
                if not isinstance(item, dict):
                    warnings.append(f"invalid artifact file metadata: {artifact_id}[{index}]")
                    continue
                policy = self._artifact_policy_for_artifact(artifact)
                if policy.get("backend") == "s3":
                    try:
                        self._s3_request(policy, "HEAD", str(item.get("storage_key") or ""))
                    except Exception:
                        missing_artifact_files.append({"artifact_id": str(artifact_id), "file_index": index, "storage_key": str(item.get("storage_key") or "")})
                    continue
                path = self._artifact_file_path(artifact, item)
                try:
                    self._assert_under(path, self._artifact_storage_root(policy))
                except ValueError:
                    errors.append(f"artifact file outside artifact root: {artifact_id}[{index}]")
                    continue
                if not path.exists() or not path.is_file():
                    missing_artifact_files.append({"artifact_id": str(artifact_id), "file_index": index, "relative_path": str(item.get("relative_path") or "")})

        stale_action_log_refs = 0
        for item in state.get("action_log", []):
            if not isinstance(item, dict):
                warnings.append("invalid action_log item")
                continue
            if workspace_filter and item.get("workspace_id") != workspace_filter:
                continue
            target_type = str(item.get("target_type") or "")
            target_id = str(item.get("target_id") or "")
            collection = {
                "workflow_run": "workflow_runs",
                "worker_task": "worker_tasks",
                "android_command": "android_commands",
                "android_device": "android_devices",
                "artifact": "artifacts",
            }.get(target_type)
            if collection and target_id and target_id not in state.get(collection, {}):
                stale_action_log_refs += 1

        warnings.extend([f"orphan worker task: {task_id}" for task_id in orphan_worker_tasks[:10]])
        warnings.extend([f"workflow run missing worker task: {run_id}" for run_id in orphan_workflow_runs[:10]])
        warnings.extend([f"missing artifact files: {len(missing_artifact_files)}"] if missing_artifact_files else [])
        warnings.extend([f"stale action log references: {stale_action_log_refs}"] if stale_action_log_refs else [])
        return {
            "ok": not errors,
            "schema_version": self._state_version(state),
            "expected_schema_version": STATE_VERSION,
            "migrations": MIGRATIONS,
            "workspace_id": workspace_filter,
            "errors": errors,
            "warnings": warnings,
            "counts": {
                "missing_collections": len(missing_collections),
                "missing_lists": len(missing_lists),
                "orphan_worker_tasks": len(orphan_worker_tasks),
                "orphan_workflow_runs": len(orphan_workflow_runs),
                "missing_artifact_files": len(missing_artifact_files),
                "stale_action_log_refs": stale_action_log_refs,
            },
            "details": {
                "missing_collections": missing_collections,
                "missing_lists": missing_lists,
                "orphan_worker_tasks": orphan_worker_tasks[:20],
                "orphan_workflow_runs": orphan_workflow_runs[:20],
                "missing_artifact_files": missing_artifact_files[:20],
            },
        }

    def _new_state(self) -> dict[str, Any]:
        now = utc_now()
        return self._normalize_state(
            {
                "version": STATE_VERSION,
                "schema": {
                    "version": STATE_VERSION,
                    "migrated_from": STATE_VERSION,
                    "migrated_at": now,
                    "migrations": [],
                },
                "created_at": now,
                "updated_at": now,
                "deployment": {
                    "mode": "light_cloud_control_plane",
                    "execution_strategy": "local_remote_worker",
                    "artifact_strategy": "local_first_with_optional_object_store",
                    "owner_terminal_roles": ["desktop", "ios"],
                    "auth_mode": os.environ.get("SPIRITKIN_REQUIRE_PAIRING_TOKEN", "optional"),
                },
                "accounts": {
                    DEFAULT_ACCOUNT_ID: self._new_account(
                        account_id=DEFAULT_ACCOUNT_ID,
                        name=DEFAULT_ACCOUNT_NAME,
                        now=now,
                        plan=default_account_plan(now=now, tier="owner"),
                    )
                },
                "workspaces": {
                    DEFAULT_WORKSPACE_ID: {
                        "workspace_id": DEFAULT_WORKSPACE_ID,
                        "account_id": DEFAULT_ACCOUNT_ID,
                        "name": DEFAULT_WORKSPACE_NAME,
                        "status": "active",
                        "created_at": now,
                    "updated_at": now,
                    "allowed_domain": "ecommerce",
                    "runtime_profile": default_runtime_profile(DEFAULT_WORKSPACE_ID),
                }
            },
                "workflow_templates": built_in_workflow_templates(),
                "workflow_runs": {},
                "worker_tasks": {},
                "remote_workers": {},
                "android_devices": {},
                "android_commands": {},
                "device_workflow_controls": {},
                "ios_terminals": {},
                "pairing_tokens": {},
                "device_bindings": {},
                "artifacts": {},
                "mobile_links": {},
                "events": [],
                "action_log": [],
            }
        )

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(state, dict):
            state = {}
        previous_version = self._state_version(state)
        if previous_version < STATE_VERSION or not isinstance(state.get("schema"), dict):
            state = self._migrate_state(state, previous_version)
        state["version"] = STATE_VERSION
        state.setdefault("created_at", utc_now())
        state.setdefault("updated_at", utc_now())
        state.setdefault("deployment", {})
        for key in (
            "accounts",
            "workspaces",
            "workflow_templates",
            "workflow_runs",
            "worker_tasks",
            "remote_workers",
            "android_devices",
            "android_commands",
            "device_workflow_controls",
            "ios_terminals",
            "pairing_tokens",
            "device_bindings",
            "artifacts",
            "mobile_links",
        ):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        if not isinstance(state.get("events"), list):
            state["events"] = []
        if not isinstance(state.get("action_log"), list):
            state["action_log"] = []
        state["action_log"] = state["action_log"][-MAX_ACTION_LOGS:]
        if not isinstance(state.get("schema"), dict):
            state["schema"] = {"version": STATE_VERSION, "migrated_from": previous_version, "migrated_at": utc_now(), "migrations": []}
        state["schema"].setdefault("version", STATE_VERSION)
        state["schema"]["version"] = STATE_VERSION
        state["schema"].setdefault("migrated_from", previous_version)
        state["schema"].setdefault("migrated_at", utc_now())
        if not isinstance(state["schema"].get("migrations"), list):
            state["schema"]["migrations"] = []
        if DEFAULT_WORKSPACE_ID not in state["workspaces"]:
            state["workspaces"][DEFAULT_WORKSPACE_ID] = {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "account_id": DEFAULT_ACCOUNT_ID,
                "name": DEFAULT_WORKSPACE_NAME,
                "status": "active",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "allowed_domain": "ecommerce",
                "execution_policy": default_execution_policy(),
                "runtime_profile": default_runtime_profile(DEFAULT_WORKSPACE_ID),
                "artifact_policy": default_artifact_policy(),
            }
        if DEFAULT_ACCOUNT_ID not in state["accounts"]:
            state["accounts"][DEFAULT_ACCOUNT_ID] = self._new_account(account_id=DEFAULT_ACCOUNT_ID, name=DEFAULT_ACCOUNT_NAME)
        for account_id, account in list(state["accounts"].items()):
            if not isinstance(account, dict):
                state["accounts"][account_id] = self._new_account(account_id=str(account_id or DEFAULT_ACCOUNT_ID), name=str(account_id or DEFAULT_ACCOUNT_NAME))
            else:
                normalized_account_id = self._normalize_account_id(account.get("account_id") or account_id or DEFAULT_ACCOUNT_ID)
                account["account_id"] = normalized_account_id
                account.setdefault("name", DEFAULT_ACCOUNT_NAME if normalized_account_id == DEFAULT_ACCOUNT_ID else normalized_account_id)
                account["status"] = str(account.get("status") or "active")
                account.setdefault("created_at", utc_now())
                account.setdefault("updated_at", utc_now())
                account["plan"] = self._account_plan(account)
                if not isinstance(account.get("workspace_ids"), list):
                    account["workspace_ids"] = []
        for workspace_id, workspace in state["workspaces"].items():
            if isinstance(workspace, dict):
                workspace["account_id"] = self._normalize_account_id(workspace.get("account_id") or DEFAULT_ACCOUNT_ID)
                workspace["execution_policy"] = self._workspace_policy(workspace)
                workspace["runtime_profile"] = self._runtime_profile(workspace)
                workspace["artifact_policy"] = self._artifact_policy(workspace)
                self._link_workspace_to_account(state, str(workspace_id), workspace["account_id"])
        for template_id, template in built_in_workflow_templates().items():
            if template_id not in state["workflow_templates"]:
                state["workflow_templates"][template_id] = template
        return state

    def _workspace_policy(self, workspace: dict[str, Any] | None) -> dict[str, Any]:
        default = default_execution_policy()
        policy = workspace.get("execution_policy") if isinstance(workspace, dict) and isinstance(workspace.get("execution_policy"), dict) else {}
        result = dict(default)
        for key in (
            "control_allowed_actions",
            "control_denied_actions",
            "android_allowed_operations",
            "android_denied_operations",
            "workflow_allowed_templates",
            "worker_allowed_capabilities",
            "approved_promotions",
        ):
            values = policy.get(key)
            if isinstance(values, list):
                merged = {str(item).strip() for item in values if str(item).strip()}
                if key == "control_allowed_actions":
                    added_defaults = {
                        "add_device_workflow",
                        "approve_pairing_request",
                        "clear_binding_history",
                        "clear_ios_terminal_history",
                        "clear_pairing_history",
                        "clear_workflow_runs",
                        "delete_artifact_file",
                        "delete_device_workflow",
                        "delete_workflow_run",
                        "delete_pairing_token",
                        "cancel_pairing_token",
                        "reject_pairing_request",
                        "revoke_device_binding",
                    }
                    legacy_default = set(default["control_allowed_actions"]) - added_defaults
                    if legacy_default.issubset(merged):
                        merged.update(added_defaults)
                if key == "android_allowed_operations":
                    # Older workspace policies may have been persisted before
                    # the Android device-management commands existed. Keep the
                    # shared diagnostics/sync operations available unless they
                    # are explicitly denied so controller-side device actions do
                    # not fail with stale workspace policies.
                    merged.update(ANDROID_BASE_OPERATIONS)
                result[key] = sorted(merged)
        if "require_promote_gate" in policy:
            result["require_promote_gate"] = bool(policy.get("require_promote_gate"))
        result["default_task_budget"] = self._task_budget(policy.get("default_task_budget") if isinstance(policy, dict) else None)
        result["artifact_policy"] = self._artifact_policy(workspace)
        result["updated_at"] = str(policy.get("updated_at") or default["updated_at"]) if isinstance(policy, dict) else default["updated_at"]
        return result

    def _assert_control_action_allowed(
        self,
        state: dict[str, Any],
        workspace_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        if not action:
            raise ValueError("action is required")
        if str(payload.get("actor_role") or "").strip() == "management":
            return
        workspace = state["workspaces"].get(workspace_id) if isinstance(state.get("workspaces"), dict) else None
        policy = self._workspace_policy(workspace if isinstance(workspace, dict) else {"workspace_id": workspace_id})
        denied = {str(item) for item in policy.get("control_denied_actions", [])}
        allowed = {str(item) for item in policy.get("control_allowed_actions", [])}
        if action in denied:
            raise PermissionError(f"control action denied by workspace policy: {action}")
        if allowed and action not in allowed:
            raise PermissionError(f"control action is not allowed by workspace policy: {action}")

    def _assert_account_control_allowed(
        self,
        state: dict[str, Any],
        action: str,
        payload: dict[str, Any],
        workspace_id: str,
    ) -> None:
        management_only = {
            "create_account",
            "assign_workspace_to_account",
            "update_account_plan",
            "set_account_status",
            "list_accounts",
            "update_workspace_policy",
            "update_runtime_profile",
        }
        actor_role = str(payload.get("actor_role") or "").strip()
        # Empty actor_role is reserved for direct trusted in-process callers.
        # Every HTTP control route stamps an explicit role.
        if action in management_only and actor_role and actor_role != "management":
            raise PermissionError(f"management token required for control action: {action}")
        if actor_role != "account_console":
            return
        account_id = self._normalize_account_id(payload.get("account_id") or DEFAULT_ACCOUNT_ID)
        if action == "get_account_usage":
            requested_account = self._normalize_account_id(payload.get("account_id") or account_id)
            if requested_account != account_id:
                raise PermissionError("account console token cannot read another account")
            return
        if action == "register_workspace":
            self._assert_account_active(state, account_id)
            return
        if workspace_id:
            actual_account = self._workspace_account_id(state, workspace_id)
            if actual_account != account_id:
                raise PermissionError("account console token cannot act on another account workspace")

    def _runtime_profile(self, workspace: dict[str, Any] | None) -> dict[str, Any]:
        workspace_id = normalize_workspace_id(workspace.get("workspace_id") if isinstance(workspace, dict) else DEFAULT_WORKSPACE_ID)
        default = default_runtime_profile(workspace_id)
        profile = workspace.get("runtime_profile") if isinstance(workspace, dict) and isinstance(workspace.get("runtime_profile"), dict) else {}
        result = dict(default)
        for key in ("profile_id", "workspace_root", "venv_path", "container_image", "dependency_policy"):
            if str(profile.get(key) or "").strip():
                result[key] = str(profile.get(key)).strip()
        for key in ("dependency_files", "allowed_local_commands", "forbidden_paths"):
            values = profile.get(key)
            if isinstance(values, list):
                result[key] = sorted({str(item).strip() for item in values if str(item).strip()})
        if result["dependency_policy"] not in {"project_local_only", "locked", "container_only"}:
            result["dependency_policy"] = default["dependency_policy"]
        result["updated_at"] = str(profile.get("updated_at") or default["updated_at"]) if isinstance(profile, dict) else default["updated_at"]
        return result

    def _artifact_policy(self, workspace: dict[str, Any] | None) -> dict[str, Any]:
        policy = workspace.get("artifact_policy") if isinstance(workspace, dict) else {}
        if not isinstance(policy, dict) and isinstance(workspace, dict):
            execution_policy = workspace.get("execution_policy") if isinstance(workspace.get("execution_policy"), dict) else {}
            policy = execution_policy.get("artifact_policy") if isinstance(execution_policy.get("artifact_policy"), dict) else {}
        return normalize_artifact_policy(policy)

    def _artifact_policy_from_update(self, current: dict[str, Any], update: object) -> dict[str, Any]:
        if not isinstance(update, dict):
            raise ValueError("artifact_policy must be an object")
        allowed_keys = {
            "backend",
            "backend_root",
            "s3_endpoint_url",
            "s3_bucket",
            "s3_region",
            "s3_prefix",
            "s3_public_base_url",
            "s3_path_style",
            "s3_access_key_env",
            "s3_secret_key_env",
            "s3_session_token_env",
            "endpoint_url",
            "bucket",
            "region",
            "prefix",
            "public_base_url",
            "path_style",
            "max_workspace_bytes",
            "max_workspace_artifacts",
            "max_file_bytes",
            "default_ttl_hours",
            "cleanup_on_quota",
        }
        next_policy = dict(current)
        credential_keys = {"s3_access_key_env", "s3_secret_key_env", "s3_session_token_env"}
        allowed_credential_envs = {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            *{item.strip() for item in os.getenv("SPIRITKIN_ARTIFACT_S3_ALLOWED_CREDENTIAL_ENVS", "").split(",") if item.strip()},
        }
        for key in allowed_keys:
            if key in update:
                if key in credential_keys and str(update[key] or "").strip() not in allowed_credential_envs:
                    raise ValueError(f"artifact credential env is not allowlisted: {update[key]}")
                next_policy[key] = update[key]
        if "s3_endpoint_url" in next_policy and str(next_policy.get("s3_endpoint_url") or "").strip():
            self._validate_s3_endpoint(str(next_policy.get("s3_endpoint_url") or ""), resolve_host=False)
        next_policy["updated_at"] = utc_now()
        return normalize_artifact_policy(next_policy)

    def _validate_s3_endpoint(self, endpoint: str, *, resolve_host: bool = True) -> None:
        parsed = urllib.parse.urlsplit(str(endpoint or "").strip())
        host = str(parsed.hostname or "").lower()
        if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("s3 endpoint must be a credential-free origin")
        allowed_hosts = {item.strip().lower() for item in os.getenv("SPIRITKIN_ARTIFACT_S3_ALLOWED_HOSTS", "").split(",") if item.strip()}
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("s3 endpoint must use HTTPS; HTTP is allowed only for loopback tests")
        if host in allowed_hosts or loopback:
            return
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError("s3 endpoint resolves to a private or special-use address")
        if resolve_host and literal is None:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
            except OSError as exc:
                raise ValueError("s3 endpoint host could not be resolved") from exc
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise ValueError("s3 endpoint resolves to a private or special-use address")

    def _workspace_artifact_policy(self, state: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            workspace = self._new_workspace(workspace_id, workspace_id)
            state["workspaces"][workspace_id] = workspace
        policy = self._artifact_policy(workspace if isinstance(workspace, dict) else {})
        if isinstance(workspace, dict):
            workspace["artifact_policy"] = policy
            workspace["execution_policy"] = self._workspace_policy(workspace)
        return policy

    def _artifact_storage_root(self, policy: dict[str, Any]) -> Path:
        backend_root = str(policy.get("backend_root") or "").strip()
        if policy.get("backend") == "filesystem_object_store" and backend_root:
            return Path(backend_root).expanduser().resolve()
        return self.artifact_root

    def _store_artifact_file(
        self,
        policy: dict[str, Any],
        workspace_id: str,
        artifact_id: str,
        name: str,
        mime_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        backend = str(policy.get("backend") or "local_disk")
        safe_filename = safe_name(name, "artifact.bin")
        storage_key = self._artifact_storage_key(policy, workspace_id, artifact_id, safe_filename)
        if backend == "s3":
            self._s3_request(policy, "PUT", storage_key, body=content, content_type=mime_type)
            return {
                "name": safe_filename,
                "mime_type": mime_type,
                "relative_path": "",
                "storage_key": storage_key,
                "backend": backend,
                "bucket": str(policy.get("s3_bucket") or ""),
                "public_url": self._s3_public_url(policy, storage_key),
                "size_bytes": len(content),
            }
        storage_root = self._artifact_storage_root(policy)
        artifact_dir = storage_root / workspace_id / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = self._dedup_target(artifact_dir / safe_filename)
        target.write_bytes(content)
        rel_path = target.relative_to(self.state_dir).as_posix() if self._is_relative_to(target, self.state_dir) else target.as_posix()
        return {
            "name": target.name,
            "mime_type": mime_type,
            "relative_path": rel_path,
            "storage_key": f"{workspace_id}/{artifact_id}/{target.name}",
            "backend": backend,
            "size_bytes": len(content),
        }

    def _artifact_storage_key(self, policy: dict[str, Any], workspace_id: str, artifact_id: str, filename: str) -> str:
        prefix = str(policy.get("s3_prefix") or "").strip().strip("/")
        parts = [workspace_id, artifact_id, filename]
        key = "/".join(part.strip("/") for part in parts if part)
        return f"{prefix}/{key}" if prefix else key

    def _s3_public_url(self, policy: dict[str, Any], storage_key: str) -> str:
        public_base = str(policy.get("s3_public_base_url") or "").strip().rstrip("/")
        if public_base:
            return f"{public_base}/{urllib.parse.quote(storage_key, safe='/')}"
        endpoint = str(policy.get("s3_endpoint_url") or "").strip().rstrip("/")
        bucket = str(policy.get("s3_bucket") or "").strip()
        if not endpoint or not bucket:
            return ""
        if policy.get("s3_path_style"):
            return f"{endpoint}/{urllib.parse.quote(bucket, safe='')}/{urllib.parse.quote(storage_key, safe='/')}"
        parsed = urllib.parse.urlsplit(endpoint)
        return urllib.parse.urlunsplit((parsed.scheme, f"{bucket}.{parsed.netloc}", "/" + urllib.parse.quote(storage_key, safe="/"), "", ""))

    def _artifact_policy_for_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        policy = default_artifact_policy()
        policy["backend"] = str(artifact.get("backend") or policy["backend"])
        storage_root = str(artifact.get("storage_root") or "").strip()
        if storage_root and policy["backend"] == "filesystem_object_store":
            policy["backend_root"] = storage_root
        if policy["backend"] == "s3":
            policy["s3_bucket"] = str(artifact.get("bucket") or policy.get("s3_bucket") or "")
            s3 = artifact.get("s3") if isinstance(artifact.get("s3"), dict) else {}
            for key in (
                "s3_endpoint_url",
                "s3_region",
                "s3_prefix",
                "s3_public_base_url",
                "s3_path_style",
                "s3_access_key_env",
                "s3_secret_key_env",
                "s3_session_token_env",
            ):
                if key in s3:
                    policy[key] = s3[key]
        return normalize_artifact_policy(policy)

    def _artifact_s3_metadata(self, policy: dict[str, Any]) -> dict[str, Any]:
        return {
            "s3_endpoint_url": str(policy.get("s3_endpoint_url") or ""),
            "s3_region": str(policy.get("s3_region") or ""),
            "s3_prefix": str(policy.get("s3_prefix") or ""),
            "s3_public_base_url": str(policy.get("s3_public_base_url") or ""),
            "s3_path_style": bool(policy.get("s3_path_style")),
            "s3_access_key_env": str(policy.get("s3_access_key_env") or "AWS_ACCESS_KEY_ID"),
            "s3_secret_key_env": str(policy.get("s3_secret_key_env") or "AWS_SECRET_ACCESS_KEY"),
            "s3_session_token_env": str(policy.get("s3_session_token_env") or "AWS_SESSION_TOKEN"),
        }

    def _materialize_artifact_file(self, artifact: dict[str, Any], file_meta: dict[str, Any]) -> Path:
        policy = self._artifact_policy_for_artifact(artifact)
        if policy.get("backend") != "s3":
            return self._artifact_file_path(artifact, file_meta)
        storage_key = str(file_meta.get("storage_key") or "").strip()
        if not storage_key:
            raise ValueError("s3 artifact missing storage_key")
        workspace_id = normalize_workspace_id(artifact.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        cache_dir = self.artifact_root / "_s3_cache" / workspace_id / artifact_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / safe_name(file_meta.get("name") or "artifact.bin", "artifact.bin")
        self._assert_under(cache_path, self.artifact_root)
        if not cache_path.exists() or int(file_meta.get("size_bytes") or 0) != cache_path.stat().st_size:
            data = self._s3_request(policy, "GET", storage_key)
            cache_path.write_bytes(data)
        return cache_path

    def _delete_artifact_file(self, artifact: dict[str, Any], file_meta: dict[str, Any]) -> None:
        policy = self._artifact_policy_for_artifact(artifact)
        if policy.get("backend") == "s3":
            storage_key = str(file_meta.get("storage_key") or "").strip()
            if storage_key:
                self._s3_request(policy, "DELETE", storage_key)
            cache_path = self.artifact_root / "_s3_cache" / normalize_workspace_id(artifact.get("workspace_id") or DEFAULT_WORKSPACE_ID) / str(artifact.get("artifact_id") or "") / safe_name(file_meta.get("name") or "artifact.bin", "artifact.bin")
            if cache_path.exists() and cache_path.is_file():
                self._assert_under(cache_path, self.artifact_root)
                cache_path.unlink()
            return
        path = self._artifact_file_path(artifact, file_meta)
        if path.exists() and path.is_file():
            path.unlink()
        parent = path.parent
        try:
            if parent.exists() and parent != self.artifact_root and not any(parent.iterdir()):
                shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass

    def _artifact_file_path(self, artifact: dict[str, Any], file_meta: dict[str, Any]) -> Path:
        policy = self._artifact_policy_for_artifact(artifact)
        root = self._artifact_storage_root(policy)
        raw_path = str(file_meta.get("relative_path") or "").strip()
        if raw_path:
            path = Path(raw_path)
            if not path.is_absolute():
                path = self.state_dir / raw_path
            if self._is_relative_to(path, root):
                self._assert_under(path, root)
                return path
            if self._is_relative_to(path, self.artifact_root):
                self._assert_under(path, self.artifact_root)
                return path
        storage_key = str(file_meta.get("storage_key") or "").strip()
        if storage_key:
            path = root / storage_key
            self._assert_under(path, root)
            return path
        workspace_id = normalize_workspace_id(artifact.get("workspace_id") or DEFAULT_WORKSPACE_ID)
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        name = safe_name(file_meta.get("name") or "artifact.bin", "artifact.bin")
        path = root / workspace_id / artifact_id / name
        self._assert_under(path, root)
        return path

    def _s3_request(
        self,
        policy: dict[str, Any],
        method: str,
        storage_key: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/octet-stream",
    ) -> bytes:
        endpoint = str(policy.get("s3_endpoint_url") or "").strip().rstrip("/")
        bucket = str(policy.get("s3_bucket") or "").strip()
        if not endpoint or not bucket:
            raise ValueError("s3 artifact backend requires s3_endpoint_url and s3_bucket")
        self._validate_s3_endpoint(endpoint)
        region = str(policy.get("s3_region") or "us-east-1").strip() or "us-east-1"
        url = self._s3_object_url(policy, storage_key)
        request = urllib.request.Request(url, data=body, method=method.upper())
        payload_hash = hashlib.sha256(body or b"").hexdigest()
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        request.add_header("x-amz-content-sha256", payload_hash)
        request.add_header("x-amz-date", amz_date)
        request.add_header("content-type", content_type)
        access_key = self._env_value(policy.get("s3_access_key_env"))
        secret_key = self._env_value(policy.get("s3_secret_key_env"))
        session_token = self._env_value(policy.get("s3_session_token_env"))
        if access_key and secret_key:
            authorization = self._s3_signature_header(method.upper(), url, body or b"", region, access_key, secret_key, amz_date, session_token)
            request.add_header("Authorization", authorization)
            if session_token:
                request.add_header("x-amz-security-token", session_token)
        try:
            with urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in {200, 204, 404} and method.upper() in {"DELETE", "HEAD"}:
                return b""
            raise ValueError(f"s3 artifact request failed: {exc.code}") from exc

    def _s3_object_url(self, policy: dict[str, Any], storage_key: str) -> str:
        endpoint = str(policy.get("s3_endpoint_url") or "").strip().rstrip("/")
        bucket = str(policy.get("s3_bucket") or "").strip()
        key = urllib.parse.quote(storage_key.lstrip("/"), safe="/")
        if policy.get("s3_path_style"):
            return f"{endpoint}/{bucket}/{key}"
        parsed = urllib.parse.urlsplit(endpoint)
        return urllib.parse.urlunsplit((parsed.scheme, f"{bucket}.{parsed.netloc}", "/" + key, "", ""))

    def _s3_signature_header(
        self,
        method: str,
        url: str,
        body: bytes,
        region: str,
        access_key: str,
        secret_key: str,
        amz_date: str,
        session_token: str = "",
    ) -> str:
        parsed = urllib.parse.urlsplit(url)
        date_stamp = amz_date[:8]
        canonical_uri = parsed.path or "/"
        canonical_querystring = parsed.query or ""
        payload_hash = hashlib.sha256(body).hexdigest()
        host = parsed.netloc
        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if session_token:
            headers["x-amz-security-token"] = session_token
        canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
        signed_headers = ";".join(sorted(headers))
        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                canonical_querystring,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                algorithm,
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            f"{algorithm} Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return authorization

    def _env_value(self, name: object) -> str:
        env_name = str(name or "").strip()
        return os.environ.get(env_name, "").strip() if env_name else ""

    def _normalize_account_id(self, value: object) -> str:
        return safe_name(value or DEFAULT_ACCOUNT_ID, DEFAULT_ACCOUNT_ID)

    def _new_account(
        self,
        *,
        account_id: str = DEFAULT_ACCOUNT_ID,
        name: str = DEFAULT_ACCOUNT_NAME,
        now: str | None = None,
        plan: dict[str, Any] | None = None,
        tier: str = "owner",
    ) -> dict[str, Any]:
        timestamp = now or utc_now()
        account_id = self._normalize_account_id(account_id)
        return {
            "account_id": account_id,
            "name": str(name or account_id),
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
            "plan": self._account_plan({"plan": plan or default_account_plan(now=timestamp, tier=tier)}),
            "workspace_ids": [],
            "notes": "",
        }

    def _account_plan(self, account: dict[str, Any] | object) -> dict[str, Any]:
        source = account.get("plan") if isinstance(account, dict) and isinstance(account.get("plan"), dict) else account
        source = source if isinstance(source, dict) else {}
        fallback = default_account_plan()
        quotas = normalize_account_quotas(source.get("quotas"))
        usage = self._account_usage(source.get("usage"))
        period_start = parse_time(str(source.get("period_start") or fallback["period_start"])).isoformat()
        period_end_raw = str(source.get("period_end") or "").strip()
        if period_end_raw:
            period_end = parse_time(period_end_raw).isoformat()
        else:
            days = max(1, int(quotas.get("scrape_period_days") or 30))
            period_end = (parse_time(period_start) + timedelta(days=days)).isoformat()
        return {
            "tier": str(source.get("tier") or fallback["tier"]),
            "quotas": quotas,
            "usage": usage,
            "period_start": period_start,
            "period_end": period_end,
            "updated_at": str(source.get("updated_at") or fallback["updated_at"]),
        }

    def _account_usage(self, value: object) -> dict[str, int]:
        source = value if isinstance(value, dict) else {}
        try:
            scrapes = max(0, int(source.get("scrapes_this_period") or 0))
        except (TypeError, ValueError):
            scrapes = 0
        return {"scrapes_this_period": scrapes}

    def _reset_account_period_if_needed(self, account: dict[str, Any]) -> None:
        plan = self._account_plan(account)
        now = datetime.now(UTC)
        if parse_time(str(plan.get("period_end") or "")) > now:
            account["plan"] = plan
            return
        quotas = normalize_account_quotas(plan.get("quotas"))
        days = max(1, int(quotas.get("scrape_period_days") or 30))
        start = now.isoformat()
        plan["usage"] = {"scrapes_this_period": 0}
        plan["period_start"] = start
        plan["period_end"] = (now + timedelta(days=days)).isoformat()
        plan["updated_at"] = utc_now()
        account["plan"] = plan

    def _workspace_account_id(self, state: dict[str, Any], workspace_id: str) -> str:
        workspace_id = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        workspace = state.get("workspaces", {}).get(workspace_id) if isinstance(state.get("workspaces"), dict) else None
        account_id = self._normalize_account_id(workspace.get("account_id") if isinstance(workspace, dict) else DEFAULT_ACCOUNT_ID)
        if not isinstance(state.get("accounts"), dict):
            state["accounts"] = {}
        if account_id not in state["accounts"]:
            state["accounts"][account_id] = self._new_account(
                account_id=account_id,
                name=DEFAULT_ACCOUNT_NAME if account_id == DEFAULT_ACCOUNT_ID else account_id,
            )
        self._link_workspace_to_account(state, workspace_id, account_id)
        return account_id

    def _link_workspace_to_account(self, state: dict[str, Any], workspace_id: str, account_id: str) -> None:
        workspace_id = normalize_workspace_id(workspace_id)
        account_id = self._normalize_account_id(account_id)
        accounts = state.setdefault("accounts", {})
        account = accounts.get(account_id)
        if not isinstance(account, dict):
            account = self._new_account(account_id=account_id, name=DEFAULT_ACCOUNT_NAME if account_id == DEFAULT_ACCOUNT_ID else account_id)
            accounts[account_id] = account
        workspaces = state.setdefault("workspaces", {})
        workspace = workspaces.get(workspace_id)
        if isinstance(workspace, dict):
            workspace["account_id"] = account_id
        for other in accounts.values():
            if not isinstance(other, dict):
                continue
            ids = [normalize_workspace_id(item) for item in other.get("workspace_ids") or [] if str(item).strip()]
            if other is account:
                if workspace_id not in ids:
                    ids.append(workspace_id)
            else:
                ids = [item for item in ids if item != workspace_id]
            other["workspace_ids"] = sorted(set(ids))

    def _account_record(self, state: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        self._reset_account_period_if_needed(account)
        account_id = self._normalize_account_id(account.get("account_id") or DEFAULT_ACCOUNT_ID)
        plan = self._account_plan(account)
        workspace_ids = sorted(
            {
                normalize_workspace_id(workspace_id)
                for workspace_id, workspace in state.get("workspaces", {}).items()
                if isinstance(workspace, dict) and self._normalize_account_id(workspace.get("account_id") or DEFAULT_ACCOUNT_ID) == account_id
            }
        )
        worker_count = self._account_active_worker_count(state, account_id)
        quotas = normalize_account_quotas(plan.get("quotas"))
        usage = self._account_usage(plan.get("usage"))
        quota_summary = {
            "max_workspaces": quotas.get("max_workspaces", 0),
            "workspace_count": len(workspace_ids),
            "max_workers": quotas.get("max_workers", 0),
            "worker_count": worker_count,
            "max_scrapes_per_period": quotas.get("max_scrapes_per_period", 0),
            "scrapes_this_period": usage.get("scrapes_this_period", 0),
            "scrape_period_days": quotas.get("scrape_period_days", 30),
            "period_start": plan.get("period_start") or "",
            "period_end": plan.get("period_end") or "",
        }
        return {
            **dict(account),
            "account_id": account_id,
            "plan": plan,
            "workspace_ids": workspace_ids,
            "usage_summary": quota_summary,
        }

    def _account_snapshot(self, state: dict[str, Any], *, workspace_id: str = "", account_id: str = "") -> dict[str, Any]:
        accounts = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
        records = [self._account_record(state, account) for account in accounts.values() if isinstance(account, dict)]
        if account_id:
            account_id = self._normalize_account_id(account_id)
            records = [record for record in records if self._normalize_account_id(record.get("account_id") or "") == account_id]
        if workspace_id:
            records = [record for record in records if normalize_workspace_id(workspace_id) in set(record.get("workspace_ids") or [])]
        status_counts = Counter(str(item.get("status") or "unknown") for item in records)
        return {
            "schema_version": "spiritkin.control_plane.accounts.v1",
            "total": len(records),
            "status_counts": dict(sorted(status_counts.items())),
            "items": sorted(records, key=lambda item: str(item.get("account_id") or "")),
        }

    def _assert_account_active(self, state: dict[str, Any], account_id: str) -> None:
        account_id = self._normalize_account_id(account_id)
        account = state.get("accounts", {}).get(account_id) if isinstance(state.get("accounts"), dict) else None
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        if str(account.get("status") or "active") != "active":
            raise PermissionError(f"account disabled: {account_id}")

    def _account_active_worker_count(self, state: dict[str, Any], account_id: str, *, excluding_token: str = "") -> int:
        account_id = self._normalize_account_id(account_id)
        count = 0
        for token, binding in state.get("device_bindings", {}).items():
            if excluding_token and token == excluding_token:
                continue
            if not isinstance(binding, dict):
                continue
            if str(binding.get("status") or "") != "active" or str(binding.get("device_role") or "") != "remote_worker":
                continue
            workspace_id = normalize_workspace_id(binding.get("workspace_id") or DEFAULT_WORKSPACE_ID)
            if self._workspace_account_id(state, workspace_id) == account_id:
                count += 1
        return count

    def _assert_can_create_workspace(self, state: dict[str, Any], account_id: str, *, excluding_workspace_id: str = "") -> None:
        account_id = self._normalize_account_id(account_id)
        account = state.get("accounts", {}).get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        quotas = normalize_account_quotas(self._account_plan(account).get("quotas"))
        limit = int(quotas.get("max_workspaces") or 0)
        if not limit:
            return
        excluding_workspace_id = normalize_workspace_id(excluding_workspace_id) if excluding_workspace_id else ""
        count = 0
        for workspace_id, workspace in state.get("workspaces", {}).items():
            if excluding_workspace_id and normalize_workspace_id(workspace_id) == excluding_workspace_id:
                continue
            if isinstance(workspace, dict) and self._normalize_account_id(workspace.get("account_id") or DEFAULT_ACCOUNT_ID) == account_id:
                count += 1
        if count + 1 > limit:
            raise PermissionError(f"account workspace quota exceeded: {count + 1}>{limit}")

    def _assert_can_bind_worker(self, state: dict[str, Any], account_id: str, *, candidate_token: str = "") -> None:
        account_id = self._normalize_account_id(account_id)
        account = state.get("accounts", {}).get(account_id)
        if not isinstance(account, dict):
            raise KeyError(f"unknown account: {account_id}")
        quotas = normalize_account_quotas(self._account_plan(account).get("quotas"))
        limit = int(quotas.get("max_workers") or 0)
        if not limit:
            return
        count = self._account_active_worker_count(state, account_id, excluding_token=candidate_token)
        if count + 1 > limit:
            raise PermissionError(f"account worker quota exceeded: {count + 1}>{limit}")

    def _workspace_artifact_usage(self, state: dict[str, Any], workspace_id: str) -> dict[str, int]:
        artifacts = [
            artifact
            for artifact in state.get("artifacts", {}).values()
            if isinstance(artifact, dict)
            and artifact.get("workspace_id") == workspace_id
            and artifact.get("status") == "available"
        ]
        total_bytes = sum(int(item.get("size_bytes") or 0) for item in artifacts)
        return {"artifact_count": len(artifacts), "total_size_bytes": total_bytes}

    def _assert_artifact_quota_available(
        self,
        state: dict[str, Any],
        workspace_id: str,
        incoming_bytes: int,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        usage = self._workspace_artifact_usage(state, workspace_id)
        max_artifacts = int(policy.get("max_workspace_artifacts") or 0)
        max_bytes = int(policy.get("max_workspace_bytes") or 0)
        next_count = usage["artifact_count"] + 1
        next_bytes = usage["total_size_bytes"] + max(0, int(incoming_bytes or 0))
        if max_artifacts and next_count > max_artifacts:
            raise PermissionError(f"artifact workspace quota exceeded: artifacts {next_count}>{max_artifacts}")
        if max_bytes and next_bytes > max_bytes:
            raise PermissionError(f"artifact workspace quota exceeded: bytes {next_bytes}>{max_bytes}")
        return {
            "backend": str(policy.get("backend") or "local_disk"),
            "used_bytes_before": usage["total_size_bytes"],
            "used_bytes_after": next_bytes,
            "max_workspace_bytes": max_bytes,
            "artifact_count_before": usage["artifact_count"],
            "artifact_count_after": next_count,
            "max_workspace_artifacts": max_artifacts,
        }

    def _artifact_quota_summary(
        self,
        state: dict[str, Any],
        workspace_id: str,
        *,
        workspace_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        workspaces = state.get("workspaces") if isinstance(state.get("workspaces"), dict) else {}
        if workspace_id:
            policy = self._workspace_artifact_policy(state, workspace_id)
            usage = self._workspace_artifact_usage(state, workspace_id)
            return {**usage, **{key: policy[key] for key in ("backend", "max_workspace_bytes", "max_workspace_artifacts", "max_file_bytes", "default_ttl_hours")}}
        items: dict[str, Any] = {}
        scoped_workspace_ids = {normalize_workspace_id(item) for item in (workspace_ids or set()) if str(item or "").strip()}
        candidate_workspace_ids = {
            normalize_workspace_id(workspace_id)
            for workspace_id in workspaces.keys()
            if workspace_id
        }
        candidate_workspace_ids.update(
            normalize_workspace_id(artifact.get("workspace_id"))
            for artifact in state.get("artifacts", {}).values()
            if isinstance(artifact, dict) and artifact.get("workspace_id")
        )
        if scoped_workspace_ids:
            candidate_workspace_ids = {item for item in candidate_workspace_ids if item in scoped_workspace_ids}
        for item_workspace_id in sorted(candidate_workspace_ids):
            policy = self._workspace_artifact_policy(state, item_workspace_id)
            usage = self._workspace_artifact_usage(state, item_workspace_id)
            items[item_workspace_id] = {
                **usage,
                **{key: policy[key] for key in ("backend", "max_workspace_bytes", "max_workspace_artifacts", "max_file_bytes", "default_ttl_hours")},
            }
        return {"workspaces": items}

    def _task_budget(self, value: object) -> dict[str, int]:
        source = value if isinstance(value, dict) else {}
        result = dict(DEFAULT_WORKER_TASK_BUDGET)
        for key, default_value in DEFAULT_WORKER_TASK_BUDGET.items():
            raw = source.get(key, default_value)
            try:
                number = int(raw)
            except (TypeError, ValueError):
                number = int(default_value)
            result[key] = max(0, number)
        return result

    def _workflow_governance(
        self,
        state: dict[str, Any],
        workspace_id: str,
        template_id: str,
        inputs: dict[str, Any],
        requested_by: str,
    ) -> dict[str, Any]:
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            workspace = self._new_workspace(workspace_id, workspace_id)
            state["workspaces"][workspace_id] = workspace
        policy = self._workspace_policy(workspace if isinstance(workspace, dict) else {})
        promote_mode = str(inputs.get("promote_mode") or inputs.get("mode") or "dry_run").strip() or "dry_run"
        if promote_mode not in {"dry_run", "debug", "production"}:
            raise ValueError(f"unknown workflow promote mode: {promote_mode}")
        if policy.get("require_promote_gate") and promote_mode == "production" and template_id not in set(policy.get("approved_promotions") or []):
            raise PermissionError(f"workflow template requires promote approval: {template_id}")
        budget = self._task_budget({**policy.get("default_task_budget", {}), **(inputs.get("budget") if isinstance(inputs.get("budget"), dict) else {})})
        return {
            "promote_mode": promote_mode,
            "dry_run": bool(inputs.get("dry_run")) or promote_mode in {"dry_run", "debug"},
            "debug": bool(inputs.get("debug")) or promote_mode == "debug",
            "requested_by": requested_by,
            "budget": budget,
            "promote_gate_required": bool(policy.get("require_promote_gate")),
            "promotion_approved": template_id in set(policy.get("approved_promotions") or []),
        }

    def _authorized_worker_capabilities(self, state: dict[str, Any], workspace_id: str, capabilities: list[str]) -> list[str]:
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            workspace = self._new_workspace(workspace_id, workspace_id)
            state["workspaces"][workspace_id] = workspace
        policy = self._workspace_policy(workspace if isinstance(workspace, dict) else {})
        allowed = set(policy.get("worker_allowed_capabilities") or [])
        reported = {str(item) for item in capabilities if str(item)}
        return sorted(reported & allowed) if allowed else sorted(reported)

    def _worker_can_claim_task(self, task: dict[str, Any], *, capabilities: list[str], authorized_capabilities: list[str]) -> tuple[bool, str]:
        required = [
            str(item)
            for item in (task.get("required_capabilities") if isinstance(task.get("required_capabilities"), list) else [])
            if str(item)
        ]
        if not required and task.get("required_capability"):
            required = [str(task.get("required_capability"))]
        reported = {str(item) for item in capabilities if str(item)}
        authorized = {str(item) for item in authorized_capabilities if str(item)}
        missing_reported = sorted(set(required) - reported)
        if missing_reported:
            return False, "worker missing capabilities: " + ", ".join(missing_reported)
        missing_authorized = sorted(set(required) - authorized)
        if missing_authorized:
            return False, "workspace policy does not authorize capabilities: " + ", ".join(missing_authorized)
        return True, ""

    def _worker_task_lease_expires_at(self, task: dict[str, Any]) -> str:
        budget = self._task_budget(task.get("budget") if isinstance(task.get("budget"), dict) else {})
        seconds = int(budget.get("max_runtime_seconds") or 0) + DEFAULT_WORKER_TASK_LEASE_GRACE_SECONDS
        return (datetime.now(UTC) + timedelta(seconds=max(60, seconds))).isoformat()

    def _reclaim_expired_worker_tasks(self, state: dict[str, Any]) -> list[str]:
        now = datetime.now(UTC)
        reclaimed: list[str] = []
        for task_id, task in state["worker_tasks"].items():
            if not isinstance(task, dict) or task.get("status") != "assigned":
                continue
            expires_at = parse_time(str(task.get("lease_expires_at") or ""))
            if expires_at > now:
                continue
            task_id = str(task.get("task_id") or task_id)
            attempt = int(task.get("attempt") or 0)
            max_attempts = max(1, int(task.get("max_attempts") or 1))
            previous_worker = str(task.get("worker_id") or "")
            run_id = str(task.get("run_id") or "")
            if attempt < max_attempts:
                task["status"] = "queued"
                task["worker_id"] = ""
                task["lease_expires_at"] = ""
                task["reclaimed_at"] = utc_now()
                task["last_reclaim_reason"] = "lease_expired"
                if previous_worker:
                    history = task.get("attempt_history") if isinstance(task.get("attempt_history"), list) else []
                    history.append(
                        {
                            "attempt": attempt,
                            "worker_id": previous_worker,
                            "assigned_at": task.get("assigned_at") or "",
                            "lease_expires_at": expires_at.isoformat(),
                            "reclaimed_at": task["reclaimed_at"],
                            "reason": "lease_expired",
                        }
                    )
                    task["attempt_history"] = history[-10:]
                if run_id in state["workflow_runs"]:
                    run = state["workflow_runs"][run_id]
                    run["status"] = "queued"
                    run["updated_at"] = utc_now()
                status = "requeued"
            else:
                task["status"] = "failed"
                task["completed_at"] = utc_now()
                task["lease_expires_at"] = ""
                task["failure_reason"] = "worker_task_lease_expired"
                task["result"] = {"ok": False, "error": "worker task lease expired", "attempt": attempt}
                if run_id in state["workflow_runs"]:
                    run = state["workflow_runs"][run_id]
                    run["status"] = "failed"
                    run["updated_at"] = utc_now()
                    run["result"] = task["result"]
                status = "failed"
            self._append_event(
                state,
                "worker_task_reclaimed",
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "workspace_id": task.get("workspace_id") or "",
                    "worker_id": previous_worker,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "status": status,
                    "reason": "lease_expired",
                },
            )
            reclaimed.append(task_id)
        return reclaimed

    def _worker_budget_result(self, task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        budget = self._task_budget(task.get("budget") if isinstance(task.get("budget"), dict) else {})
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        checks = {
            "runtime_seconds": "max_runtime_seconds",
            "artifacts": "max_artifacts",
            "android_commands": "max_android_commands",
            "retries": "max_retries",
        }
        violations: list[str] = []
        normalized_usage: dict[str, int] = {}
        for usage_key, budget_key in checks.items():
            try:
                used = int(usage.get(usage_key) or 0)
            except (TypeError, ValueError):
                used = 0
            normalized_usage[usage_key] = max(0, used)
            limit = int(budget.get(budget_key) or 0)
            if limit and used > limit:
                violations.append(f"{usage_key} {used}>{limit}")
        return {
            "status": "blocked" if violations else "ok",
            "blocked": bool(violations),
            "violations": violations,
            "usage": normalized_usage,
            "budget": budget,
        }

    def _result_attempts_publish(self, result: dict[str, Any]) -> bool:
        if bool(result.get("published") or result.get("submitted") or result.get("production_side_effect")):
            return True
        side_effects = result.get("side_effects") if isinstance(result.get("side_effects"), list) else []
        return any(str(item).lower() in {"publish", "submit", "production_publish", "listing_submit"} for item in side_effects)

    def _assert_android_operation_allowed(self, state: dict[str, Any], workspace_id: str, operation: str) -> None:
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            workspace = self._new_workspace(workspace_id, workspace_id)
            state["workspaces"][workspace_id] = workspace
        policy = self._workspace_policy(workspace if isinstance(workspace, dict) else {})
        allowed = set(policy.get("android_allowed_operations") or [])
        denied = set(policy.get("android_denied_operations") or [])
        if operation in denied:
            raise PermissionError(f"android operation denied by workspace policy: {operation}")
        if allowed and operation not in allowed:
            raise PermissionError(f"android operation not allowed by workspace policy: {operation}")

    def _assert_workflow_template_allowed(self, state: dict[str, Any], workspace_id: str, template_id: str) -> None:
        workspace = state["workspaces"].get(workspace_id)
        if workspace is None:
            workspace = self._new_workspace(workspace_id, workspace_id)
            state["workspaces"][workspace_id] = workspace
        policy = self._workspace_policy(workspace if isinstance(workspace, dict) else {})
        allowed = set(policy.get("workflow_allowed_templates") or [])
        if allowed and template_id not in allowed:
            raise PermissionError(f"workflow template not allowed by workspace policy: {template_id}")

    def _assert_device_workflow_enabled(
        self,
        state: dict[str, Any],
        workspace_id: str,
        template_id: str,
        inputs: dict[str, Any],
    ) -> None:
        raw_device_id = str(inputs.get("device_id") or inputs.get("android_device_id") or inputs.get("target_device_id") or "").strip()
        if not raw_device_id or raw_device_id == "*":
            return
        device_id = safe_name(raw_device_id, "android_device")
        control_id = self._device_workflow_control_id(workspace_id, device_id, template_id)
        control = state.get("device_workflow_controls", {}).get(control_id)
        if isinstance(control, dict) and not bool(control.get("enabled", True)):
            reason = str(control.get("reason") or "device workflow is paused")
            raise PermissionError(f"workflow is paused for device {device_id}: {reason}")

    def _android_command_catalog(self) -> list[dict[str, Any]]:
        return [
            {"operation": operation, **dict(metadata)}
            for operation, metadata in sorted(ANDROID_COMMAND_CATALOG.items())
        ]

    def _android_command_metadata(self, operation: str) -> dict[str, Any]:
        return dict(ANDROID_COMMAND_CATALOG.get(operation) or {"label": operation, "risk": "unknown", "required_capabilities": [operation]})

    def _reported_android_command_catalog(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        items: list[dict[str, Any]] = []
        for item in value[:100]:
            if not isinstance(item, dict):
                continue
            operation = str(item.get("operation") or "").strip()
            if not operation:
                continue
            items.append(
                {
                    "operation": operation,
                    "risk": str(item.get("risk") or "unknown"),
                    "required_capabilities": [
                        str(capability)
                        for capability in (item.get("required_capabilities") if isinstance(item.get("required_capabilities"), list) else [])
                        if str(capability)
                    ],
                    "requires_accessibility": bool(item.get("requires_accessibility")),
                    "requires_artifact": bool(item.get("requires_artifact")),
                    "required_packages": [
                        str(package)
                        for package in (item.get("required_packages") if isinstance(item.get("required_packages"), list) else [])
                        if str(package)
                    ],
                }
            )
        return items

    def _android_command_preflight(
        self,
        state: dict[str, Any],
        operation: str,
        params: dict[str, Any],
        *,
        device_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        metadata = self._android_command_metadata(operation)
        warnings: list[str] = []
        blockers: list[str] = []
        device = state["android_devices"].get(device_id) if device_id != "*" else None
        if isinstance(device, dict) and normalize_workspace_id(device.get("workspace_id")) != workspace_id:
            blockers.append(f"target device belongs to workspace {device.get('workspace_id') or ''}")

        capabilities = {
            str(item)
            for item in (device.get("capabilities") if isinstance(device, dict) and isinstance(device.get("capabilities"), list) else [])
            if str(item)
        }
        for capability in metadata.get("required_capabilities") or []:
            capability = str(capability)
            if capabilities and capability not in capabilities:
                blockers.append(f"device missing capability: {capability}")

        device_state = device.get("state") if isinstance(device, dict) and isinstance(device.get("state"), dict) else {}
        if metadata.get("requires_accessibility") and isinstance(device, dict):
            if device_state.get("pdd_accessibility_granted") is False:
                blockers.append("手机无障碍未开启，请在手机系统无障碍中重新打开 SpiritKin PDD Automation")
            elif device_state.get("pdd_accessibility_connected") is False:
                blockers.append("手机无障碍已授权但服务未连接，请返回手机端或重新开关无障碍服务")
        if metadata.get("requires_screen_capture_authorization") and isinstance(device, dict):
            if device_state.get("screen_capture_authorized") is False:
                blockers.append("Android screen capture permission is not granted")

        installed_packages = {
            str(item.get("package") or "")
            for item in (device.get("installed_apps") if isinstance(device, dict) and isinstance(device.get("installed_apps"), list) else [])
            if isinstance(item, dict)
        }
        for package_name in metadata.get("required_packages") or []:
            package_name = str(package_name)
            if installed_packages and package_name not in installed_packages:
                blockers.append(f"required Android package is not installed: {package_name}")

        package_param = str(metadata.get("required_packages_param") or "")
        if package_param and installed_packages:
            requested = str(params.get(package_param) or "").strip()
            if requested and "." in requested and requested not in installed_packages:
                warnings.append(f"requested package is not installed: {requested}")

        foreground = str(device_state.get("foreground_package") or device_state.get("current_app") or "")
        required_foreground = {str(item) for item in metadata.get("requires_foreground_packages") or [] if str(item)}
        if required_foreground and foreground and foreground not in required_foreground:
            warnings.append(f"foreground app is {foreground}")

        if metadata.get("requires_artifact"):
            artifact_id = str(params.get("artifact_id") or "").strip()
            if not artifact_id:
                blockers.append("artifact_id is required")
            else:
                artifact = state["artifacts"].get(artifact_id)
                if isinstance(artifact, dict):
                    if normalize_workspace_id(artifact.get("workspace_id")) != workspace_id:
                        blockers.append("artifact belongs to another workspace")
                    if artifact.get("status") != "available":
                        blockers.append(f"artifact is not available: {artifact_id}")
                else:
                    warnings.append(f"artifact not found in local state: {artifact_id}")

        status = "blocked" if blockers else ("warning" if warnings else "ready")
        if device_id == "*" and metadata.get("risk") in {"high", "critical"}:
            warnings.append("broadcast command targets every matching Android device in the workspace")
            status = "warning" if status == "ready" else status
        preflight = {
            "status": status,
            "risk": str(metadata.get("risk") or "unknown"),
            "label": str(metadata.get("label") or operation),
            "required_capabilities": [str(item) for item in metadata.get("required_capabilities") or []],
            "requires_accessibility": bool(metadata.get("requires_accessibility")),
            "requires_screen_capture_authorization": bool(metadata.get("requires_screen_capture_authorization")),
            "requires_artifact": bool(metadata.get("requires_artifact")),
            "required_packages": [str(item) for item in metadata.get("required_packages") or []],
            "warnings": warnings,
            "blockers": blockers,
        }
        if blockers:
            raise PermissionError(f"android command preflight blocked: {operation}: {'; '.join(blockers)}")
        return preflight

    def _new_workspace(self, workspace_id: str, name: str | None = None, *, account_id: str = DEFAULT_ACCOUNT_ID) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "account_id": self._normalize_account_id(account_id or DEFAULT_ACCOUNT_ID),
            "name": name or workspace_id,
            "status": "active",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "allowed_domain": "ecommerce",
            "execution_policy": default_execution_policy(),
            "runtime_profile": default_runtime_profile(workspace_id),
            "artifact_policy": default_artifact_policy(),
        }

    def _state_version(self, state: dict[str, Any]) -> int:
        try:
            return int(state.get("version") or 0)
        except (TypeError, ValueError):
            return 0

    def _migrate_state(self, state: dict[str, Any], previous_version: int) -> dict[str, Any]:
        if not isinstance(state, dict):
            state = {}
        migrated = dict(state)
        migrations: list[str] = []
        if previous_version < 1:
            migrations.append(self._migration_name("bootstrap_v1_defaults"))
        if previous_version < 2 or not isinstance(migrated.get("action_log"), list):
            migrated["action_log"] = self._action_log_from_events(migrated.get("events"))
            migrations.append(self._migration_name("v2_action_log_from_events"))
        if previous_version < 3 or not isinstance(migrated.get("accounts"), dict):
            migrated.setdefault("accounts", {})
            migrations.append(self._migration_name("v3_accounts_and_quotas"))
        existing_schema = migrated.get("schema") if isinstance(migrated.get("schema"), dict) else {}
        existing_migrations = existing_schema.get("migrations") if isinstance(existing_schema.get("migrations"), list) else []
        migrated["schema"] = {
            "version": STATE_VERSION,
            "migrated_from": previous_version,
            "migrated_at": utc_now(),
            "migrations": [*existing_migrations, *migrations],
        }
        migrated["version"] = STATE_VERSION
        return migrated

    def _migration_name(self, name: str) -> str:
        for migration in MIGRATIONS:
            if migration["name"] == name:
                return str(migration["name"])
        raise KeyError(f"unknown migration: {name}")

    def _android_diagnostics(self, devices: dict[str, Any], commands: dict[str, Any]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for device in devices.values():
            if not isinstance(device, dict):
                continue
            device_id = str(device.get("device_id") or "")
            recent_failures = [
                command
                for command in commands.values()
                if isinstance(command, dict)
                and command.get("delivered_to") == device_id
                and str(command.get("status") or "") == "failed"
            ]
            recent_failures.sort(key=lambda item: str(item.get("completed_at") or item.get("created_at") or ""), reverse=True)
            recent_failures = recent_failures[:3]
            diagnostic = device.get("diagnostic") if isinstance(device.get("diagnostic"), dict) else None
            items.append(diagnostic or self._android_device_diagnostic(device, recent_failures))
        severity_order = {"blocked": 0, "warning": 1, "ready": 2, "unknown": 3}
        items.sort(key=lambda item: (severity_order.get(str(item.get("status") or "unknown"), 3), str(item.get("device_id") or "")))
        status_counts = Counter(str(item.get("status") or "unknown") for item in items)
        return {"count": len(items), "status_counts": dict(sorted(status_counts.items())), "items": items[:MAX_RECENT]}

    def _workspace_device_overview(
        self,
        *,
        workspaces: dict[str, Any],
        devices: dict[str, Any],
        ios_terminals: dict[str, Any],
        remote_workers: dict[str, Any],
        pairing_tokens: dict[str, Any],
        device_bindings: dict[str, Any],
        device_workflow_controls: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_ids = {
            normalize_workspace_id(key)
            for key in workspaces.keys()
            if str(key or "").strip()
        }
        for collection in (devices, ios_terminals, remote_workers, pairing_tokens, device_bindings):
            for item in collection.values():
                if isinstance(item, dict) and item.get("workspace_id"):
                    workspace_ids.add(normalize_workspace_id(item.get("workspace_id")))
        if not workspace_ids:
            workspace_ids.add(DEFAULT_WORKSPACE_ID)

        summaries: list[dict[str, Any]] = []
        total_counts = Counter()
        for workspace_id in sorted(workspace_ids):
            workspace = workspaces.get(workspace_id) if isinstance(workspaces.get(workspace_id), dict) else {}
            android_items = self._device_items_for_workspace(
                devices.values(),
                workspace_id,
                kind="android",
                workflow_controls=device_workflow_controls.values(),
            )
            ios_items = self._device_items_for_workspace(ios_terminals.values(), workspace_id, kind="ios_terminal")
            worker_items = self._device_items_for_workspace(remote_workers.values(), workspace_id, kind="remote_worker")
            binding_items = self._binding_items_for_workspace(device_bindings.values(), workspace_id)
            pending_items = self._pairing_items_for_workspace(pairing_tokens.values(), workspace_id)
            request_items = self._pairing_items_for_workspace(pairing_tokens.values(), workspace_id, status="requested")
            history_pairings = [
                self._pairing_record(item)
                for item in pairing_tokens.values()
                if isinstance(item, dict)
                and normalize_workspace_id(item.get("workspace_id")) == workspace_id
                and str(item.get("status") or "") not in {"pending", "requested"}
            ]
            binding_history_items = self._binding_items_for_workspace(device_bindings.values(), workspace_id, include_inactive=True)
            counts = {
                "android": len(android_items),
                "ios_controllers": len(ios_items),
                "remote_workers": len(worker_items),
                "active_bindings": len(binding_items),
                "pending_pairings": len(pending_items),
                "pairing_requests": len(request_items),
                "pairing_history": len(history_pairings),
                "binding_history": len(binding_history_items),
            }
            total_counts.update(counts)
            summaries.append(
                {
                    "workspace_id": workspace_id,
                    "name": str(workspace.get("name") or workspace_id),
                    "status": str(workspace.get("status") or "active"),
                    "counts": counts,
                    "android_devices": android_items,
                    "ios_controllers": ios_items,
                    "remote_workers": worker_items,
                    "active_bindings": binding_items,
                    "pending_pairings": pending_items,
                    "pairing_requests": request_items,
                    "pairing_history": self._recent(history_pairings, key="created_at"),
                    "binding_history": binding_history_items,
                    "last_seen_at": self._latest_timestamp(
                        [
                            *android_items,
                            *ios_items,
                            *worker_items,
                            *binding_items,
                            *pending_items,
                            *request_items,
                        ]
                    ),
                }
            )

        return {
            "count": len(summaries),
            "total_counts": dict(sorted(total_counts.items())),
            "items": summaries,
        }

    def _device_items_for_workspace(
        self,
        values: Any,
        workspace_id: str,
        *,
        kind: str,
        workflow_controls: Any | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict) or normalize_workspace_id(raw.get("workspace_id")) != workspace_id:
                continue
            state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
            if kind == "android":
                device_id = str(raw.get("device_id") or "")
                item = {
                    "device_id": device_id,
                    "workspace_id": workspace_id,
                    "role": "android_bridge",
                    "role_label": "Android 手机端",
                    "status": str(raw.get("status") or "unknown"),
                    "last_seen_at": str(raw.get("last_seen_at") or ""),
                    "client": str(raw.get("client") or ""),
                    "foreground_package": str(state.get("foreground_package") or state.get("current_app") or ""),
                    "pdd_accessibility_granted": state.get("pdd_accessibility_granted"),
                    "pdd_accessibility_connected": state.get("pdd_accessibility_connected"),
                    "workflow_controls": self._workflow_controls_for_device(
                        workflow_controls or [],
                        workspace_id,
                        device_id,
                    ),
                }
            elif kind == "ios_terminal":
                item = {
                    "device_id": str(raw.get("terminal_id") or ""),
                    "workspace_id": workspace_id,
                    "role": "ios_terminal",
                    "role_label": "iOS 主控端",
                    "status": str(raw.get("status") or "active"),
                    "last_seen_at": str(raw.get("last_seen_at") or ""),
                    "client": str(raw.get("client") or ""),
                }
            else:
                item = {
                    "device_id": str(raw.get("worker_id") or ""),
                    "workspace_id": workspace_id,
                    "role": "remote_worker",
                    "role_label": "远程执行端",
                    "status": str(raw.get("status") or "unknown"),
                    "last_seen_at": str(raw.get("last_seen_at") or ""),
                    "client": str(raw.get("client") or ""),
                    "capabilities": raw.get("capabilities") if isinstance(raw.get("capabilities"), list) else [],
                }
            items.append(item)
        return self._recent(items, key="last_seen_at")

    def _binding_items_for_workspace(self, values: Any, workspace_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict) or normalize_workspace_id(raw.get("workspace_id")) != workspace_id:
                continue
            if str(raw.get("status") or "") != "active" and not include_inactive:
                continue
            if str(raw.get("status") or "") == "active" and include_inactive:
                continue
            role = str(raw.get("device_role") or "")
            items.append(
                {
                    "device_id": str(raw.get("device_id") or raw.get("worker_id") or raw.get("terminal_id") or ""),
                    "workspace_id": workspace_id,
                    "role": role,
                    "role_label": self._device_role_label(role),
                    "status": str(raw.get("status") or "active"),
                    "token_id": str(raw.get("token_id") or ""),
                    "last_seen_at": str(raw.get("last_seen_at") or raw.get("bound_at") or ""),
                    "client": str(raw.get("client") or ""),
                }
            )
        return self._recent(items, key="last_seen_at")

    def _pairing_record(self, raw: dict[str, Any]) -> dict[str, Any]:
        role = str(raw.get("device_role") or "")
        return {
            "token_id": str(raw.get("token_id") or ""),
            "request_id": str(raw.get("request_id") or raw.get("token_id") or ""),
            "workspace_id": normalize_workspace_id(raw.get("workspace_id") or DEFAULT_WORKSPACE_ID),
            "device_id": str(raw.get("device_id") or ""),
            "device_role": role,
            "role": role,
            "role_label": self._device_role_label(role),
            "status": str(raw.get("status") or "unknown"),
            "created_at": str(raw.get("created_at") or ""),
            "updated_at": str(raw.get("updated_at") or ""),
            "expires_at": str(raw.get("expires_at") or ""),
            "approved_at": str(raw.get("approved_at") or ""),
            "requested_by": str(raw.get("requested_by") or ""),
            "bound_device_id": str(raw.get("bound_device_id") or ""),
            "bound_at": str(raw.get("bound_at") or ""),
            "cancelled_at": str(raw.get("cancelled_at") or ""),
            "revoked_at": str(raw.get("revoked_at") or ""),
            "expired_at": str(raw.get("expired_at") or ""),
        }

    def _workflow_controls_for_device(self, values: Any, workspace_id: str, device_id: str) -> list[dict[str, Any]]:
        controls: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict):
                continue
            if normalize_workspace_id(raw.get("workspace_id")) != workspace_id:
                continue
            if str(raw.get("device_id") or "") != str(device_id or ""):
                continue
            controls.append(
                {
                    "control_id": str(raw.get("control_id") or ""),
                    "workspace_id": workspace_id,
                    "device_id": str(raw.get("device_id") or ""),
                    "workflow_id": str(raw.get("workflow_id") or DEFAULT_WORKFLOW_TEMPLATE_ID),
                    "enabled": bool(raw.get("enabled", True)),
                    "status": str(raw.get("status") or ("enabled" if raw.get("enabled", True) else "paused")),
                    "reason": str(raw.get("reason") or ""),
                    "last_repair_type": str(raw.get("last_repair_type") or ""),
                    "last_repair_command_id": str(raw.get("last_repair_command_id") or ""),
                    "last_repair_at": str(raw.get("last_repair_at") or ""),
                    "updated_at": str(raw.get("updated_at") or ""),
                    "updated_by": str(raw.get("updated_by") or ""),
                }
            )
        controls.sort(key=lambda item: str(item.get("workflow_id") or ""))
        return controls[:10]

    def _device_workflow_control_id(self, workspace_id: str, device_id: str, workflow_id: str) -> str:
        safe_workspace = normalize_workspace_id(workspace_id or DEFAULT_WORKSPACE_ID)
        safe_device = safe_name(device_id or "", "android_device")
        safe_workflow = safe_name(workflow_id or DEFAULT_WORKFLOW_TEMPLATE_ID, DEFAULT_WORKFLOW_TEMPLATE_ID)
        return f"dwc_{safe_workspace}_{safe_device}_{safe_workflow}"

    def _pairing_items_for_workspace(self, values: Any, workspace_id: str, *, status: str = "pending") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict) or normalize_workspace_id(raw.get("workspace_id")) != workspace_id:
                continue
            if str(raw.get("status") or "") != status:
                continue
            role = str(raw.get("device_role") or "")
            items.append(
                {
                    "token_id": str(raw.get("token_id") or ""),
                    "request_id": str(raw.get("request_id") or raw.get("token_id") or ""),
                    "device_id": str(raw.get("device_id") or ""),
                    "workspace_id": workspace_id,
                    "role": role,
                    "role_label": self._device_role_label(role),
                    "status": status,
                    "created_at": str(raw.get("created_at") or ""),
                    "updated_at": str(raw.get("updated_at") or ""),
                    "expires_at": str(raw.get("expires_at") or ""),
                    "requested_by": str(raw.get("requested_by") or ""),
                }
            )
        return self._recent(items, key="created_at")

    def _latest_timestamp(self, values: list[dict[str, Any]]) -> str:
        timestamps = [
            str(item.get("last_seen_at") or item.get("created_at") or item.get("expires_at") or "")
            for item in values
            if isinstance(item, dict)
        ]
        return max((item for item in timestamps if item), default="")

    def _device_role_label(self, role: str) -> str:
        if role == "android_bridge":
            return "Android 手机端"
        if role == "ios_terminal":
            return "iOS 主控端"
        if role == "remote_worker":
            return "远程执行端"
        return role or "设备"

    def _android_device_diagnostic(self, device: dict[str, Any], recent_failures: list[dict[str, Any]]) -> dict[str, Any]:
        state = device.get("state") if isinstance(device.get("state"), dict) else {}
        modules = state.get("automation_modules") if isinstance(state.get("automation_modules"), list) else []
        issues: list[dict[str, str]] = []
        actions: list[dict[str, str]] = []
        foreground = str(state.get("foreground_package") or state.get("current_app") or "")
        module_status = {
            str(item.get("id") or ""): str(item.get("status") or "")
            for item in modules
            if isinstance(item, dict)
        }
        installed_packages = {
            str(item.get("package") or "")
            for item in (device.get("installed_apps") if isinstance(device.get("installed_apps"), list) else [])
            if isinstance(item, dict)
        }
        capabilities = {str(item) for item in (device.get("capabilities") if isinstance(device.get("capabilities"), list) else [])}

        if device.get("status") != "online":
            issues.append({"code": "android.offline", "severity": "blocked", "message": "手机端离线"})
            actions.append({"label": "打开手机端并开启后台同步", "command": "android.open_bridge", "kind": "queue_command"})
        if module_status.get("core.command_sync") == "paused":
            issues.append({"code": "heartbeat.paused", "severity": "blocked", "message": "后台同步已暂停"})
            actions.append({"label": "在手机端开启后台同步", "command": "android.enable_heartbeat", "kind": "manual"})
        if "com.xunmeng.pinduoduo" not in installed_packages and module_status.get("pdd.automation") == "missing_app":
            issues.append({"code": "pdd.missing", "severity": "blocked", "message": "手机未安装拼多多"})
            actions.append({"label": "在手机上安装拼多多", "command": "android.install_pdd", "kind": "manual"})
        if state.get("pdd_accessibility_granted") is False:
            issues.append(
                {
                    "code": "accessibility.not_granted",
                    "severity": "blocked",
                    "message": "手机在线，绑定有效；重装或升级后系统关闭了无障碍，需要重新开启后才能执行 PDD 自动化",
                }
            )
            actions.append({"label": "打开手机无障碍设置", "command": "android.open_accessibility_settings", "kind": "queue_command"})
        elif state.get("pdd_accessibility_granted") is True and state.get("pdd_accessibility_connected") is False:
            issues.append({"code": "accessibility.not_connected", "severity": "warning", "message": "无障碍已授权但服务未连接"})
            actions.append({"label": "回到手机端或重新开关无障碍服务", "command": "android.restart_accessibility", "kind": "manual"})
        if module_status.get("android.ui_snapshot") == "needs_accessibility":
            issues.append({"code": "ui_snapshot.needs_accessibility", "severity": "warning", "message": "页面快照需要无障碍连接"})
        if foreground and foreground not in {"com.xunmeng.pinduoduo", "com.spiritkin.mobilelinkbridge"}:
            issues.append({"code": "foreground.not_pdd", "severity": "warning", "message": f"前台应用是 {foreground}，不是拼多多"})
            actions.append({"label": "先打开拼多多再执行自动化", "command": "pdd.launch", "kind": "queue_command"})

        for command in recent_failures[:3]:
            failure_class = self._android_failure_class(command)
            issues.append(
                {
                    "code": "command.failed",
                    "severity": "warning",
                    "message": f"{command.get('operation') or '命令'} 失败：{command.get('message') or '未知错误'}",
                    "failure_class": failure_class,
                }
            )
            if command.get("operation") in {"pdd.create_listing", "android.ui_snapshot"}:
                actions.append({"label": "上传页面快照用于排查选择器", "command": "android.ui_snapshot", "kind": "queue_command"})
            actions.append(
                {
                    "label": f"重试 {command.get('operation') or '命令'}",
                    "command": str(command.get("operation") or ""),
                    "operation": str(command.get("operation") or ""),
                    "params": command.get("params") if isinstance(command.get("params"), dict) else {},
                    "kind": "retry_command",
                    "source_command_id": str(command.get("command_id") or ""),
                    "failure_class": failure_class,
                }
            )

        severity_rank = {"blocked": 0, "warning": 1}
        status = "ready"
        if issues:
            worst = min(severity_rank.get(item.get("severity", "warning"), 1) for item in issues)
            status = "blocked" if worst == 0 else "warning"
        return {
            "device_id": str(device.get("device_id") or ""),
            "workspace_id": str(device.get("workspace_id") or ""),
            "status": status,
            "foreground_package": foreground,
            "pdd_accessibility_granted": bool(state.get("pdd_accessibility_granted")),
            "pdd_accessibility_connected": bool(state.get("pdd_accessibility_connected")),
            "module_status": module_status,
            "issues": issues[:8],
            "actions": self._diagnostic_actions(actions, capabilities, str(device.get("device_id") or ""))[:6],
            "updated_at": str(device.get("last_seen_at") or ""),
        }

    def _android_failure_class(self, command: dict[str, Any]) -> str:
        message = str(command.get("message") or "").lower()
        operation = str(command.get("operation") or "")
        if "accessibility" in message or "无障碍" in message:
            return "accessibility"
        if "download" in message or "http" in message or "artifact" in message or "下载" in message:
            return "artifact_download"
        if "field" in message or "selector" in message or "按钮" in message or operation.startswith("pdd."):
            return "selector_or_foreground"
        return "unknown"

    def _diagnostic_actions(self, actions: list[dict[str, Any]], capabilities: set[str], device_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for action in self._dedupe_actions(actions):
            command = str(action.get("command") or "")
            item = dict(action)
            item["device_id"] = device_id
            item.setdefault("operation", command)
            item.setdefault("params", {})
            item.setdefault("kind", "queue_command")
            item["supported"] = bool(command and (not capabilities or command in capabilities))
            if command and capabilities and command not in capabilities:
                item["reason"] = "device did not report this capability"
            result.append(item)
        return result

    def _dedupe_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for action in actions:
            key = "|".join(
                [
                    str(action.get("kind") or ""),
                    str(action.get("command") or ""),
                    str(action.get("source_command_id") or ""),
                    str(action.get("label") or ""),
                ]
            )
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(action)
        return result

    def _action_log_from_events(self, events: object) -> list[dict[str, Any]]:
        if not isinstance(events, list):
            return []
        items: list[dict[str, Any]] = []
        for event in events[-MAX_ACTION_LOGS:]:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            action = str(event.get("type") or "event")
            items.append(
                {
                    "action_id": str(event.get("event_id") or f"act_{sha12(action + str(event.get('at') or ''))}"),
                    "event_id": event.get("event_id"),
                    "action": action,
                    "status": "recorded",
                    "workspace_id": str(payload.get("workspace_id") or ""),
                    "actor": str(payload.get("requested_by") or payload.get("client") or "system"),
                    "target_type": self._infer_target_type(action),
                    "target_id": self._infer_target_id(payload),
                    "at": str(event.get("at") or utc_now()),
                    "summary": self._action_summary(action, payload),
                    "payload": payload,
                }
            )
        return items

    def _pairing_by_token(self, state: dict[str, Any], token: str) -> dict[str, Any] | None:
        for item in state["pairing_tokens"].values():
            if isinstance(item, dict) and item.get("token") == token:
                return item
        return None

    def _append_event(self, state: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        event_id = f"evt_{int(time.time() * 1000)}_{sha12(event_type + json.dumps(payload, sort_keys=True, ensure_ascii=False))}"
        event = {
            "event_id": event_id,
            "type": event_type,
            "at": utc_now(),
            "payload": payload,
        }
        state.setdefault("events", []).append(event)
        if len(state["events"]) > MAX_EVENTS:
            del state["events"][:-MAX_EVENTS]
        self._append_action_log(state, event_type, payload, event_id=event_id, at=str(event["at"]))

    def _append_action_log(
        self,
        state: dict[str, Any],
        action: str,
        payload: dict[str, Any],
        *,
        event_id: str = "",
        at: str | None = None,
    ) -> None:
        if not isinstance(payload, dict):
            payload = {}
        action_id = f"act_{int(time.time() * 1000)}_{sha12(action + json.dumps(payload, sort_keys=True, ensure_ascii=False))}"
        item = {
            "action_id": action_id,
            "event_id": event_id,
            "action": action,
            "status": str(payload.get("status") or "recorded"),
            "workspace_id": str(payload.get("workspace_id") or ""),
            "actor": str(payload.get("requested_by") or payload.get("client") or payload.get("actor") or "system"),
            "target_type": self._infer_target_type(action),
            "target_id": self._infer_target_id(payload),
            "at": at or utc_now(),
            "summary": self._action_summary(action, payload),
            "payload": payload,
        }
        state.setdefault("action_log", []).append(item)
        if len(state["action_log"]) > MAX_ACTION_LOGS:
            del state["action_log"][:-MAX_ACTION_LOGS]

    def _infer_target_type(self, action: str) -> str:
        prefixes = (
            ("artifact_", "artifact"),
            ("android_command_", "android_command"),
            ("android_", "android_device"),
            ("pairing_", "pairing_token"),
            ("device_", "device_binding"),
            ("workflow_", "workflow_run"),
            ("worker_task_", "worker_task"),
            ("worker_", "remote_worker"),
            ("ios_", "ios_terminal"),
            ("mobile_link_", "mobile_link"),
            ("workspace_", "workspace"),
            ("state_", "state"),
        )
        for prefix, target_type in prefixes:
            if action.startswith(prefix):
                return target_type
        return "control_plane"

    def _infer_target_id(self, payload: dict[str, Any]) -> str:
        for key in (
            "command_id",
            "artifact_id",
            "run_id",
            "worker_task_id",
            "task_id",
            "device_id",
            "worker_id",
            "terminal_id",
            "token_id",
            "link_id",
            "workspace_id",
        ):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def _action_summary(self, action: str, payload: dict[str, Any]) -> str:
        target_id = self._infer_target_id(payload)
        workspace_id = str(payload.get("workspace_id") or "")
        parts = [action]
        if target_id:
            parts.append(target_id)
        if workspace_id and workspace_id != target_id:
            parts.append(f"workspace={workspace_id}")
        return " ".join(parts)

    def _decode_file_payload(self, item: dict[str, Any]) -> tuple[bytes, str]:
        if item.get("data_url"):
            data_url = str(item.get("data_url") or "")
            header, sep, encoded = data_url.partition(",")
            if not sep:
                raise ValueError("invalid data_url artifact payload")
            mime_type = "application/octet-stream"
            if header.startswith("data:"):
                mime_type = header[5:].split(";")[0] or mime_type
            return self._decode_base64(encoded), mime_type
        if item.get("base64"):
            return self._decode_base64(str(item.get("base64") or "")), str(item.get("mime_type") or item.get("type") or "application/octet-stream")
        if "text" in item:
            return str(item.get("text") or "").encode("utf-8"), str(item.get("mime_type") or item.get("type") or "text/plain")
        reference = str(item.get("uri") or item.get("path") or "").strip()
        if reference:
            return (reference + "\n").encode("utf-8"), "text/plain"
        raise ValueError("artifact file requires data_url, base64, text, uri, or path")

    def _decode_base64(self, value: str) -> bytes:
        try:
            data = base64.b64decode(value, validate=True)
        except binascii.Error as exc:
            raise ValueError("invalid base64 artifact payload") from exc
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact payload is too large")
        return data

    def _dedup_target(self, target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem or "artifact"
        suffix = target.suffix
        for index in range(2, 1000):
            candidate = target.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                return candidate
        return target.with_name(f"{stem}_{int(time.time())}{suffix}")

    def _assert_under(self, path: Path, root: Path) -> None:
        resolved = path.resolve()
        allowed = root.resolve()
        if resolved != allowed and allowed not in resolved.parents:
            raise ValueError(f"refusing to touch path outside {allowed}: {resolved}")

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        resolved = path.resolve()
        allowed = root.resolve()
        return resolved == allowed or allowed in resolved.parents

    def _recent(self, values: Any, *, key: str = "created_at", limit: int = MAX_RECENT) -> list[dict[str, Any]]:
        items = [dict(item) for item in values if isinstance(item, dict)]
        items.sort(key=lambda item: str(item.get(key) or item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return items[: max(0, int(limit or MAX_RECENT))]

    def _workflow_run_items(self, values: Any, *, terminal: bool, limit: int = MAX_RECENT) -> list[dict[str, Any]]:
        items = []
        for item in values:
            if not isinstance(item, dict):
                continue
            is_terminal = str(item.get("status") or "") in TERMINAL_WORKFLOW_STATUSES
            if is_terminal == terminal:
                items.append(dict(item))
        items.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return items[: max(0, int(limit or MAX_RECENT))]
