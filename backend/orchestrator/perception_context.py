from __future__ import annotations

from backend.executors.base import ExecutionRequest

TRUTHY_VALUES = {"1", "true", "yes", "y", "on", "enabled", "enable"}
PERCEPTION_REQUEST_KEYS = (
    "include_perception_context",
    "include_screen_context",
    "screen_context_enabled",
    "perception_context_enabled",
)
OCR_MODES = {"ocr", "text", "screen_text", "read_text", "extract_text"}


def perception_context_requested(metadata: dict | None) -> bool:
    metadata = dict(metadata or {})
    return any(_truthy(metadata.get(key)) for key in PERCEPTION_REQUEST_KEYS)


def build_perception_request(user_input: str, metadata: dict | None) -> ExecutionRequest:
    metadata = dict(metadata or {})
    mode = str(
        metadata.get("perception_context_mode")
        or metadata.get("screen_context_mode")
        or metadata.get("perception_mode")
        or ""
    ).strip().lower()
    params: dict[str, object] = {}
    if isinstance(metadata.get("perception_region"), (list, tuple, dict)):
        params["region"] = metadata["perception_region"]
    if mode in OCR_MODES:
        if metadata.get("perception_lang"):
            params["lang"] = str(metadata.get("perception_lang") or "")
        return ExecutionRequest(target="screen", operation="screen_extract_text", params=params)

    query = str(
        metadata.get("perception_context_query")
        or metadata.get("screen_context_query")
        or user_input
        or "请描述当前屏幕并指出可操作区域。"
    ).strip()
    params["query"] = query or "请描述当前屏幕并指出可操作区域。"
    return ExecutionRequest(target="screen", operation="screen_understand", params=params)


def summarize_perception_data(data, *, operation: str, max_chars: int = 1200) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        text = data.strip()
    elif isinstance(data, dict):
        preferred = data.get("text") or data.get("summary") or data.get("description") or data.get("result")
        text = str(preferred).strip() if preferred is not None else str(data).strip()
    else:
        text = str(data).strip()
    if not text:
        return ""
    label = "屏幕文字" if operation == "screen_extract_text" else "屏幕理解"
    text = text[: max(1, int(max_chars))]
    return f"{label}：{text}"


def merge_visual_context(existing: str, perception_summary: str) -> str:
    existing = str(existing or "").strip()
    perception_summary = str(perception_summary or "").strip()
    if not perception_summary:
        return existing
    if not existing:
        return perception_summary
    if perception_summary in existing:
        return existing
    return f"{existing}\n{perception_summary}"


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in TRUTHY_VALUES
