from __future__ import annotations

import sys
from typing import Any

_SNAPSHOT_CONTEXT_REFS: dict[int, dict[str, str]] = {}
_INSTALLED = False


def install_model_provider_action_context_patch() -> None:
    """Decorate ModelProviderActionResult snapshots with Context ledger refs."""

    global _INSTALLED
    if _INSTALLED:
        return
    if _module_is_initializing("backend.app"):
        return
    from backend.app.learning_workflow import ModelProviderActionResult

    original_snapshot = ModelProviderActionResult.snapshot
    if getattr(original_snapshot, "_spiritkin_context_patch", False):
        _INSTALLED = True
        return

    def snapshot_with_context(self) -> dict[str, Any]:
        snapshot = original_snapshot(self)
        if _should_record_provider_action_context(snapshot):
            ref = _SNAPSHOT_CONTEXT_REFS.get(id(self))
            if ref is None:
                try:
                    ref = record_model_provider_action_context(snapshot)
                except Exception as exc:  # pragma: no cover - provider action response should survive audit-sidecar failures.
                    ref = {"context_patch_error": f"{type(exc).__name__}: {exc}"}
                _SNAPSHOT_CONTEXT_REFS[id(self)] = ref
            snapshot.update(ref)
        return snapshot

    snapshot_with_context._spiritkin_context_patch = True  # type: ignore[attr-defined]
    ModelProviderActionResult.snapshot = snapshot_with_context  # type: ignore[method-assign]
    _INSTALLED = True


def record_model_provider_action_context(provider_action: dict[str, Any]) -> dict[str, str]:
    """Append a Context ledger record for an explicit user-triggered provider action."""

    from backend.orchestrator.context_store import ContextPatch, append_context_patch

    provider = _clean_id(provider_action.get("provider"), fallback="unknown")
    model = _clean_id(provider_action.get("model"), fallback="unconfigured")
    patch = ContextPatch(
        context_id=f"model_provider:{provider}:{model}",
        patch_type="set",
        actor="desktop_provider_action",
        path="/model/providers/health",
        value={
            "provider": provider_action.get("provider") or "",
            "display_name": provider_action.get("display_name") or "",
            "endpoint": provider_action.get("endpoint") or "",
            "model": provider_action.get("model") or "",
            "action": provider_action.get("action") or "",
            "status": provider_action.get("status") or "",
            "ok": bool(provider_action.get("ok")),
            "health_status": provider_action.get("health_status") or "",
            "duration_ms": int(provider_action.get("duration_ms") or 0),
            "checked_at": float(provider_action.get("checked_at") or 0.0),
            "model_count": int(provider_action.get("model_count") or 0),
            "error": provider_action.get("error") or "",
        },
        metadata={
            "source": "model_provider_health",
            "views": ["task"],
            "provider": provider,
            "model": model,
            "action": provider_action.get("action") or "",
            "health_status": provider_action.get("health_status") or "",
        },
    )
    append_context_patch(patch)
    return {
        "context_id": patch.context_id,
        "context_patch_id": patch.patch_id,
        "context_path": patch.path,
    }


def attach_model_provider_context_ref(provider_action: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(provider_action or {})
    try:
        snapshot.update(record_model_provider_action_context(snapshot))
    except Exception as exc:  # pragma: no cover - provider action response should survive audit-sidecar failures.
        snapshot["context_patch_error"] = f"{type(exc).__name__}: {exc}"
    return snapshot


def _should_record_provider_action_context(provider_action: dict[str, Any]) -> bool:
    action = str(provider_action.get("action") or "").strip()
    if action not in {"test_provider", "sync_provider_models"}:
        return False
    return bool(provider_action.get("health_status") or provider_action.get("checked_at"))


def _clean_id(value: Any, *, fallback: str) -> str:
    cleaned = str(value or "").strip().lower().replace(" ", "_").replace("/", "_").replace("\\", "_")
    return cleaned or fallback


def _module_is_initializing(module_name: str) -> bool:
    module = sys.modules.get(module_name)
    spec = getattr(module, "__spec__", None)
    return bool(getattr(spec, "_initializing", False))
