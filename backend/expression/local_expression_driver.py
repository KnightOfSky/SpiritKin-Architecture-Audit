"""Optional local-model fallback for inferring avatar emotion/action from text.

The avatar's expression today comes from three layers, in order:
  1. the main LLM emitting ``<emotion:x><action:y>`` tags in its reply,
  2. a static ``ACTION_MAP`` dict that derives an action from the emotion, and
  3. the frontend keyword regex (``semanticReaction``).

None of these is a dedicated emotion/action *inference* model. This module adds
an optional fourth path: when the main LLM produced *no* explicit emotion tag
(i.e. it fell back to the default ``neutral``), we ask a **local** model to
classify the reply text into one of the supported emotion/action buckets.

Design constraints (guarded by tests):
- Disabled by default (``SPIRITKIN_LOCAL_EXPRESSION_ENABLED`` off). When off,
  ``infer_emotion_action`` is never called and behavior is unchanged.
- Only ever drives a *local* provider (llamacpp / local_transformers by
  default) so it never burns cloud API tokens.
- Output is always normalized back into ``SUPPORTED_EMOTIONS`` /
  ``SUPPORTED_ACTIONS``; any illegal or unparseable output falls back to the
  caller-supplied defaults (i.e. no change).
"""

from __future__ import annotations

import json
import os
import re

from backend.agents.base import _normalize_action, _normalize_emotion
from backend.prompts.expression import EXPRESSION_CLASSIFIER_PROMPT

_ENABLED_ENV = "SPIRITKIN_LOCAL_EXPRESSION_ENABLED"
_PROVIDER_ENV = "SPIRITKIN_LOCAL_EXPRESSION_PROVIDER"
_MODEL_ENV = "SPIRITKIN_LOCAL_EXPRESSION_MODEL"
_DEFAULT_PROVIDER = "llamacpp"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def local_expression_enabled() -> bool:
    return str(os.getenv(_ENABLED_ENV, "")).strip().lower() in _TRUE_VALUES


def _resolve_provider(provider: str | None) -> str:
    return (provider or os.getenv(_PROVIDER_ENV) or _DEFAULT_PROVIDER).strip()


def _resolve_model(model: str | None) -> str | None:
    resolved = (model or os.getenv(_MODEL_ENV) or "").strip()
    return resolved or None


def _extract_json_object(raw: str) -> dict | None:
    cleaned = (raw or "").strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def infer_emotion_action(
    text: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    default_emotion: str = "neutral",
    default_action: str = "idle",
    llm_client=None,
) -> tuple[str, str]:
    """Classify ``text`` into (emotion, action), normalized to supported sets.

    Returns the caller-supplied defaults when disabled, when the text is empty,
    or when the local model fails / returns anything illegal. ``llm_client`` is
    injectable for tests; production uses ``get_llm_response`` on a local
    provider only.
    """
    if not local_expression_enabled():
        return default_emotion, default_action
    stripped = str(text or "").strip()
    if not stripped:
        return default_emotion, default_action

    if llm_client is None:
        from backend.services.conversation_engine import get_llm_response

        def llm_client(prompt: str) -> str:
            return get_llm_response(prompt, provider=_resolve_provider(provider), model_name=_resolve_model(model))

    prompt = EXPRESSION_CLASSIFIER_PROMPT.substitute(text=stripped[:600])
    try:
        raw = llm_client(prompt)
    except Exception:
        return default_emotion, default_action

    data = _extract_json_object(str(raw or ""))
    if not isinstance(data, dict):
        return default_emotion, default_action

    emotion = _normalize_emotion(data.get("emotion"), default_emotion)
    action = _normalize_action(data.get("action"), default_action)
    return emotion, action
