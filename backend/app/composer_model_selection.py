from __future__ import annotations

from typing import Any

from backend.app.learning_workflow import AssistModelSettings, load_assist_models

REASONING_EFFORTS = frozenset({"auto", "none", "low", "medium", "high"})


def normalize_reasoning_effort(value: object) -> str:
    effort = str(value or "auto").strip().lower()
    return effort if effort in REASONING_EFFORTS else "auto"


def resolve_configured_assist_model(model_id: object) -> AssistModelSettings | None:
    requested = str(model_id or "").strip()
    if not requested:
        return None
    return next(
        (
            model
            for model in load_assist_models()
            if model.model_id == requested and model.enabled and model.configured
        ),
        None,
    )


def canonicalize_composer_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Validate desktop model selection without exposing stored credentials."""

    result = dict(metadata or {})
    requested_id = str(result.get("model_id") or "").strip()
    result["reasoning_effort"] = normalize_reasoning_effort(result.get("reasoning_effort"))
    selected = resolve_configured_assist_model(requested_id)
    if selected is None:
        result.update(
            {
                "model_id": "",
                "model_display": "自动（主模型）",
                "model_provider": "",
                "model_name": "",
                "model_source": "runtime_route",
                "model_selection_validated": True,
                "model_selection_status": "automatic" if not requested_id else "fallback_unavailable",
            }
        )
        if requested_id:
            result["requested_model_id"] = requested_id
        return result

    result.update(
        {
            "model_id": selected.model_id,
            "model_display": selected.display_name,
            "model_provider": selected.provider,
            "model_name": selected.model,
            "model_source": "configured_assist_model",
            "model_selection_validated": True,
            "model_selection_status": "configured",
        }
    )
    result.pop("requested_model_id", None)
    return result
