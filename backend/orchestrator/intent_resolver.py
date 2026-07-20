from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from backend.agents.base import AgentContext
from backend.executors.base import ExecutionRequest
from backend.prompts.voice import INTENT_RESOLVER_PROMPT
from backend.tools.base import ToolSpec


@dataclass(frozen=True)
class IntentResolution:
    status: str
    reason: str = ""
    confidence: float = 0.0
    message: str = ""
    corrected_text: str = ""
    execution_request: ExecutionRequest | None = None
    raw_response: str = ""


class IntentResolver:
    def __init__(self, llm_client, min_confidence: float = 0.45):
        self._llm_client = llm_client
        self._min_confidence = float(min_confidence)

    def resolve(self, context: AgentContext, available_tools: list[ToolSpec]) -> IntentResolution:
        if not available_tools:
            return IntentResolution(status="none", reason="no_tools")

        prompt = self._build_prompt(context, available_tools)
        try:
            raw_response = self._llm_client(prompt)
        except Exception as exc:
            return IntentResolution(status="none", reason=f"llm_error:{type(exc).__name__}")

        data = self._extract_json_object(raw_response)
        if not isinstance(data, dict):
            return IntentResolution(status="none", reason="invalid_json", raw_response=raw_response or "")

        status = str(data.get("intent") or data.get("status") or "none").strip().lower()
        confidence = self._coerce_float(data.get("confidence"), default=0.0)
        message = str(data.get("message") or "").strip()
        corrected_text = str(data.get("corrected_text") or "").strip()

        if status in {"clarify", "clarification", "ask"}:
            return IntentResolution(status="clarify", confidence=confidence, message=message, corrected_text=corrected_text, raw_response=raw_response or "")

        if status not in {"execute", "tool", "action"}:
            return IntentResolution(status="none", confidence=confidence, corrected_text=corrected_text, raw_response=raw_response or "")

        if confidence < self._min_confidence:
            return IntentResolution(status="clarify", confidence=confidence, message=message or "这个动作我没理解准确，请换个说法。", corrected_text=corrected_text, raw_response=raw_response or "")

        spec = self._resolve_tool_spec(data, available_tools)
        if spec is None:
            return IntentResolution(status="none", reason="tool_not_found", corrected_text=corrected_text, raw_response=raw_response or "")

        params = self._build_params(spec, data)
        req = ExecutionRequest(target=spec.target, operation=spec.operation, params=params)
        return IntentResolution(status="execute", confidence=confidence, execution_request=req, corrected_text=corrected_text, raw_response=raw_response or "")

    @staticmethod
    def _build_prompt(context: AgentContext, available_tools: list[ToolSpec]) -> str:
        user_lower = context.user_input.lower()

        # Always show essential tools: app.launch, app.close, browser, screen
        essential = [t for t in available_tools if t.name in ('app.launch','app.close','app.force_close','browser.open_url','browser.search','screen.capture','software.list','window.list','clipboard.read','clipboard.write')]
        others = [t for t in available_tools if t not in essential]
        # Score others by relevance
        scored = [(sum([5 if t.target in user_lower else 0, 3 if t.operation in user_lower else 0, *[1 for w in user_lower.split() if w in t.description.lower()]]), t) for t in others]
        scored.sort(key=lambda x: -x[0])
        shown = essential + [t for _, t in scored[:10]]

        tool_lines = [f"- {t.name}: {t.target}.{t.operation} -- {t.description}" for t in shown]
        tools_text = "\n".join(tool_lines)

        # Get installed apps from context metadata
        inv = str(context.metadata.get("inventory_context", ""))
        if not inv:
            # Fallback: scan directly
            try:
                from backend.devices.registry import get_device_backend
                be = get_device_backend("local_pc")
                if hasattr(be, "list_installed_apps"):
                    apps = be.list_installed_apps(limit=80)
                    names = [a.get("name","")[:40] for a in apps if a.get("name")]
                    inv = "Installed: " + ", ".join(names[:40])
            except Exception:
                pass
        inv = inv[:800]
        asr_context = IntentResolver._build_asr_context(context)

        return INTENT_RESOLVER_PROMPT.substitute(
            inv=inv,
            asr_context=asr_context,
            tools_text=tools_text,
            user_input=context.user_input,
        )

    @staticmethod
    def _build_asr_context(context: AgentContext) -> str:
        metadata = dict(context.metadata or {})
        lines: list[str] = []
        if metadata.get("raw_voice_text"):
            lines.append(f"Raw ASR text: {metadata.get('raw_voice_text')}")
        if metadata.get("asr_original_text") and metadata.get("asr_corrected_text"):
            lines.append(f"Rule-corrected ASR: {metadata.get('asr_original_text')} -> {metadata.get('asr_corrected_text')}")
        metrics = metadata.get("asr_metrics") if isinstance(metadata.get("asr_metrics"), dict) else {}
        if metrics:
            if metrics.get("error"):
                lines.append(f"ASR error: {metrics.get('error')}")
            if metrics.get("microphone"):
                lines.append(f"Microphone: {metrics.get('microphone')}")
            rejected = metrics.get("rejected_segments")
            if rejected:
                lines.append(f"Rejected low-confidence ASR segments: {rejected}")
            segments = metrics.get("segments") if isinstance(metrics.get("segments"), list) else []
            segment_texts = []
            for segment in segments[:4]:
                if isinstance(segment, dict):
                    segment_texts.append(
                        f"{segment.get('text', '')}"
                        f"(accepted={segment.get('accepted')}, logprob={segment.get('avg_logprob')}, no_speech={segment.get('no_speech_prob')})"
                    )
            if segment_texts:
                lines.append("ASR segments: " + " | ".join(segment_texts))
        return "\n".join(lines) if lines else "ASR diagnostics: none"

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any] | None:
        if not raw:
            return None
        cleaned = (raw or "").strip()
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```", "", cleaned)
        cleaned = re.sub(r"//[^\n]*", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{[^{}]*\}", cleaned, flags=re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _coerce_float(val, default=0.0):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _resolve_tool_spec(data: dict[str, Any], available_tools: list[ToolSpec]):
        tool_name = str(data.get("tool_name") or "").strip()
        spec_map = {t.name: t for t in available_tools}
        if tool_name in spec_map:
            return spec_map[tool_name]
        # Try fuzzy match
        for t in available_tools:
            if tool_name.lower() in t.name.lower() or t.name.lower() in tool_name.lower():
                return t
        return None

    @staticmethod
    def _build_params(spec: ToolSpec, data: dict[str, Any]) -> dict[str, Any]:
        params = dict(data.get("params") or data.get("arguments") or {})
        schema = spec.schema or {}
        for key in schema:
            if key not in params and data.get(key):
                params[key] = data[key]
        return params
