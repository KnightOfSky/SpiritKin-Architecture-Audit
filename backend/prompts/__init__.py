"""Central prompt resource layer for SpiritKinAI.

All instruction templates sent to LLMs live in this package so that prompt
text can be audited, translated, and tuned without touching business logic.
Pure stdlib (string.Template) — this package sits below every other layer and
must never import from backend.app / backend.orchestrator / backend.agents.

Templates use ``string.Template`` ($placeholders) instead of ``str.format``
so that JSON-heavy prompt bodies do not require brace escaping.
"""

from __future__ import annotations

from string import Template

from backend.prompts.agent_roles import (
    ECOMMERCE_AGENT_PROMPT,
    GAME_DEVELOPMENT_AGENT_PROMPT,
    PROGRAMMING_AGENT_PROMPT,
    VIDEO_ANIMATION_AGENT_PROMPT,
)
from backend.prompts.execution import RETRY_PROMPT
from backend.prompts.expression import EXPRESSION_CLASSIFIER_PROMPT
from backend.prompts.review import (
    CODE_JURY_PROMPT,
    ECOSYSTEM_REVIEW_PROMPT,
    SKILL_ASSIST_FALLBACK_PROMPT,
    SKILL_REVIEW_PROMPT,
)
from backend.prompts.voice import (
    ASR_INITIAL_PROMPT_AUTO,
    ASR_INITIAL_PROMPT_YUE,
    ASR_INITIAL_PROMPT_ZH,
    INTENT_RESOLVER_PROMPT,
)

PROMPT_REGISTRY: dict[str, Template] = {
    "agent.programming": PROGRAMMING_AGENT_PROMPT,
    "agent.ecommerce": ECOMMERCE_AGENT_PROMPT,
    "agent.game_development": GAME_DEVELOPMENT_AGENT_PROMPT,
    "agent.video_animation": VIDEO_ANIMATION_AGENT_PROMPT,
    "execution.retry": RETRY_PROMPT,
    "expression.classifier": EXPRESSION_CLASSIFIER_PROMPT,
    "review.skill": SKILL_REVIEW_PROMPT,
    "review.jury": CODE_JURY_PROMPT,
    "review.ecosystem": ECOSYSTEM_REVIEW_PROMPT,
    "review.skill_assist_fallback": SKILL_ASSIST_FALLBACK_PROMPT,
    "voice.intent_resolver": INTENT_RESOLVER_PROMPT,
    "voice.asr_initial_yue": Template(ASR_INITIAL_PROMPT_YUE),
    "voice.asr_initial_auto": Template(ASR_INITIAL_PROMPT_AUTO),
    "voice.asr_initial_zh": Template(ASR_INITIAL_PROMPT_ZH),
}


def render_prompt(key: str, **params: object) -> str:
    """Render a registered prompt template by key. Raises KeyError on unknown key."""
    return PROMPT_REGISTRY[key].substitute(**params)


def list_prompt_keys() -> list[str]:
    return sorted(PROMPT_REGISTRY)


__all__ = [
    "ASR_INITIAL_PROMPT_AUTO",
    "ASR_INITIAL_PROMPT_YUE",
    "ASR_INITIAL_PROMPT_ZH",
    "CODE_JURY_PROMPT",
    "ECOMMERCE_AGENT_PROMPT",
    "ECOSYSTEM_REVIEW_PROMPT",
    "EXPRESSION_CLASSIFIER_PROMPT",
    "GAME_DEVELOPMENT_AGENT_PROMPT",
    "INTENT_RESOLVER_PROMPT",
    "PROGRAMMING_AGENT_PROMPT",
    "PROMPT_REGISTRY",
    "RETRY_PROMPT",
    "SKILL_ASSIST_FALLBACK_PROMPT",
    "SKILL_REVIEW_PROMPT",
    "VIDEO_ANIMATION_AGENT_PROMPT",
    "render_prompt",
    "list_prompt_keys",
]
