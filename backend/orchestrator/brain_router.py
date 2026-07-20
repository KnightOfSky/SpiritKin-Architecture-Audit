from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

BRAIN_ROUTER_SCHEMA_VERSION = "spiritkin.brain_router.v1"


@dataclass(frozen=True)
class BrainModelCard:
    profile_id: str
    provider: str = ""
    model: str = ""
    model_id: str = ""
    role: str = "general"
    domain: str = "general"
    cost_tier: str = "unknown"
    latency_tier: str = "unknown"
    context_tokens: int = 0
    supports_tools: bool = False
    supports_json: bool = False
    privacy_tier: str = "unknown"
    local: bool = False
    strengths: tuple[str, ...] = ()
    weak_spots: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "provider": self.provider,
            "model": self.model,
            "model_id": self.model_id,
            "role": self.role,
            "domain": self.domain,
            "cost_tier": self.cost_tier,
            "latency_tier": self.latency_tier,
            "context_tokens": self.context_tokens,
            "supports_tools": self.supports_tools,
            "supports_json": self.supports_json,
            "privacy_tier": self.privacy_tier,
            "local": self.local,
            "strengths": list(self.strengths),
            "weak_spots": list(self.weak_spots),
            "fallback_chain": list(self.fallback_chain),
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class BrainRouterDecision:
    decision_id: str
    agent_id: str
    brain_profile: str
    provider: str = ""
    model: str = ""
    model_id: str = ""
    route: str = "local_default"
    reason: str = ""
    complexity_score: int = 0
    risk_score: int = 0
    privacy_score: int = 0
    context_score: int = 0
    confidence: float = 1.0
    expected_cost_tier: str = "unknown"
    expected_latency_tier: str = "unknown"
    required_capabilities: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = ()
    audit: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": BRAIN_ROUTER_SCHEMA_VERSION,
            "decision_id": self.decision_id,
            "agent_id": self.agent_id,
            "brain_profile": self.brain_profile,
            "provider": self.provider,
            "model": self.model,
            "model_id": self.model_id,
            "route": self.route,
            "reason": self.reason,
            "complexity_score": self.complexity_score,
            "risk_score": self.risk_score,
            "privacy_score": self.privacy_score,
            "context_score": self.context_score,
            "confidence": self.confidence,
            "expected_cost_tier": self.expected_cost_tier,
            "expected_latency_tier": self.expected_latency_tier,
            "required_capabilities": list(self.required_capabilities),
            "fallback_chain": list(self.fallback_chain),
            "audit": dict(self.audit or {}),
        }


class BrainRouter:
    """Local-first model/profile router with explicit audit-friendly decisions."""

    def __init__(self, *, agent_profiles: dict[str, dict[str, Any]] | None = None, model_catalog: dict[str, Any] | None = None):
        self._agent_profiles = {
            str(agent_id): dict(profile)
            for agent_id, profile in (agent_profiles or {}).items()
            if str(agent_id).strip() and isinstance(profile, dict)
        }
        self._model_catalog = dict(model_catalog or {})
        self._model_cards_by_key = self._build_model_cards_by_key(self._model_catalog)
        self._audit: list[BrainRouterDecision] = []

    @property
    def audit(self) -> list[dict[str, Any]]:
        return [item.snapshot() for item in self._audit[-80:]]

    def route(
        self,
        *,
        agent_id: str,
        task_text: str = "",
        route: str = "",
        domain: str = "",
        risk_level: str = "",
        required_capabilities: list[str] | tuple[str, ...] | None = None,
        preferred_profile: dict[str, Any] | None = None,
    ) -> BrainRouterDecision:
        agent_id = str(agent_id or "main_text").strip() or "main_text"
        profile = {**dict(self._agent_profiles.get(agent_id, {})), **dict(preferred_profile or {})}
        brain_profile = str(profile.get("brain_profile") or profile.get("brain_profile_id") or profile.get("profile_id") or "").strip()
        provider = str(profile.get("provider") or "").strip()
        model = str(profile.get("model") or profile.get("model_name") or "").strip()
        model_id = str(profile.get("model_id") or model or "").strip()
        role = str(profile.get("role") or ("chief_coordinator" if agent_id in {"main_text", "coordinator"} else "specialist"))
        capability_tuple = tuple(str(item) for item in (required_capabilities or profile.get("capabilities") or []) if str(item).strip())
        card = self._select_model_card(provider=provider, model=model, model_id=model_id, role=role, domain=domain or str(profile.get("domain") or ""))
        if card is not None:
            provider = provider or card.provider
            model = model or card.model
            model_id = model_id or card.model_id
        if not brain_profile:
            brain_profile = _default_brain_profile(agent_id, provider, model or model_id, role)

        complexity = _score_complexity(task_text, route=route, domain=domain)
        risk = _score_risk(task_text, risk_level=risk_level, route=route)
        privacy = _score_privacy(task_text)
        context = _score_context(task_text)
        selected_route, reason = _route_label(
            provider=provider,
            model=model,
            card=card,
            complexity=complexity,
            risk=risk,
            privacy=privacy,
            context=context,
        )
        decision = BrainRouterDecision(
            decision_id=f"brain-{len(self._audit) + 1}",
            agent_id=agent_id,
            brain_profile=brain_profile,
            provider=provider,
            model=model,
            model_id=model_id,
            route=selected_route,
            reason=reason,
            complexity_score=complexity,
            risk_score=risk,
            privacy_score=privacy,
            context_score=context,
            confidence=_confidence(complexity, risk, provider, model),
            expected_cost_tier=card.cost_tier if card is not None else _infer_cost_tier(provider, model),
            expected_latency_tier=card.latency_tier if card is not None else _infer_latency_tier(provider, model),
            required_capabilities=capability_tuple,
            fallback_chain=card.fallback_chain if card is not None else tuple(str(item) for item in profile.get("fallback_chain") or () if str(item).strip()),
            audit={
                "input_route": route,
                "domain": domain,
                "role": role,
                "model_card_found": card is not None,
                "task_length": len(task_text or ""),
            },
        )
        self._audit.append(decision)
        return decision

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": BRAIN_ROUTER_SCHEMA_VERSION,
            "model_card_count": len(self._model_cards_by_key),
            "audit": self.audit,
        }

    def _select_model_card(self, *, provider: str, model: str, model_id: str, role: str, domain: str) -> BrainModelCard | None:
        keys = [
            _model_key(provider, model),
            _model_key(provider, model_id),
            _model_key("", model),
            _model_key("", model_id),
        ]
        for key in keys:
            if key in self._model_cards_by_key:
                return self._model_cards_by_key[key]
        role_domain = f"{role} {domain}".lower()
        candidates = list(self._model_cards_by_key.values())
        ranked = [
            card for card in candidates
            if card.role.lower() in role_domain or card.domain.lower() in role_domain or domain.lower() in card.domain.lower()
        ]
        if ranked:
            return sorted(ranked, key=lambda card: int(card.metadata.get("priority") or 0), reverse=True)[0]
        return None

    @staticmethod
    def _build_model_cards_by_key(catalog: dict[str, Any]) -> dict[str, BrainModelCard]:
        cards: dict[str, BrainModelCard] = {}
        for raw in catalog.get("models") or []:
            if not isinstance(raw, dict):
                continue
            card = model_card_from_catalog_entry(raw)
            for key in {_model_key(card.provider, card.model), _model_key(card.provider, card.model_id), _model_key("", card.model_id)}:
                if key.strip(":"):
                    cards[key] = card
        return cards


def model_card_from_catalog_entry(raw: dict[str, Any]) -> BrainModelCard:
    model_id = str(raw.get("model_id") or raw.get("model") or "").strip()
    provider = str(raw.get("provider") or "").strip()
    size_class = str(raw.get("size_class") or "").lower()
    role = str(raw.get("role") or "general")
    domain = str(raw.get("domain") or "general")
    metadata = dict(raw.get("metadata") or {})
    return BrainModelCard(
        profile_id=_default_brain_profile(role, provider, model_id, role),
        provider=provider,
        model=model_id,
        model_id=model_id,
        role=role,
        domain=domain,
        cost_tier=_infer_cost_tier(provider, model_id, size_class=size_class),
        latency_tier=_infer_latency_tier(provider, model_id, size_class=size_class),
        context_tokens=int(metadata.get("context_tokens") or _infer_context_tokens(model_id, size_class)),
        supports_tools=_infer_tool_support(model_id, domain),
        supports_json=True,
        privacy_tier="local" if _is_local_provider(provider, model_id) else "cloud",
        local=_is_local_provider(provider, model_id),
        strengths=tuple(_split_domain(domain)),
        weak_spots=(),
        fallback_chain=tuple(str(item) for item in raw.get("fallback_chain") or () if str(item).strip()),
        metadata={**metadata, "priority": raw.get("priority"), "size_class": raw.get("size_class"), "source_url": raw.get("source_url", "")},
    )


def _model_key(provider: str, model: str) -> str:
    return f"{provider.strip().lower()}:{model.strip().lower()}"


def _default_brain_profile(agent_id: str, provider: str, model: str, role: str) -> str:
    parts = [agent_id or role or "agent", provider or "default", model or "unconfigured"]
    return re.sub(r"[^a-zA-Z0-9_]+", "_", "_".join(parts).lower()).strip("_")


def _score_complexity(text: str, *, route: str, domain: str) -> int:
    normalized = (text or "").lower()
    score = min(45, len(normalized) // 40)
    if route in {"agent", "general"}:
        score += 20
    if domain in {"programming", "ecommerce", "game_development", "video_animation"}:
        score += 15
    if any(token in normalized for token in ("架构", "规划", "重构", "debug", "review", "评审", "复杂", "多步骤", "workflow")):
        score += 25
    return min(100, score)


def _score_risk(text: str, *, risk_level: str, route: str) -> int:
    risk_map = {"low": 15, "medium": 45, "high": 80, "critical": 95}
    score = risk_map.get((risk_level or "").lower(), 20 if route in {"builtin", "tool"} else 35)
    normalized = (text or "").lower()
    if any(token in normalized for token in ("删除", "覆盖", "提交", "部署", "发布", "付款", "refund", "delete", "deploy", "publish")):
        score += 25
    return min(100, score)


def _score_privacy(text: str) -> int:
    normalized = (text or "").lower()
    score = 20
    if any(token in normalized for token in ("api key", "token", "密码", "密钥", "客户", "订单", "隐私")):
        score += 45
    return min(100, score)


def _score_context(text: str) -> int:
    length = len(text or "")
    if length > 8000:
        return 90
    if length > 2500:
        return 65
    if length > 800:
        return 40
    return 15


def _route_label(*, provider: str, model: str, card: BrainModelCard | None, complexity: int, risk: int, privacy: int, context: int) -> tuple[str, str]:
    local = card.local if card is not None else _is_local_provider(provider, model)
    if privacy >= 60 and local:
        return "local_private", "privacy score favors local brain"
    if complexity >= 75 or context >= 80 or risk >= 85:
        if local:
            return "local_complex_with_review_candidate", "complex/risky task on local brain; cloud review may be needed"
        return "cloud_complex", "complex/risky task routed to configured cloud brain"
    if local:
        return "local_default", "local/default brain is sufficient"
    if provider:
        return "configured_profile", "using configured provider/model profile"
    return "unconfigured_default", "no explicit brain profile configured"


def _confidence(complexity: int, risk: int, provider: str, model: str) -> float:
    confidence = 0.9
    if not provider and not model:
        confidence -= 0.35
    if complexity > 80 or risk > 85:
        confidence -= 0.15
    return max(0.25, round(confidence, 2))


def _infer_cost_tier(provider: str, model: str, *, size_class: str = "") -> str:
    source = f"{provider} {model} {size_class}".lower()
    if _is_local_provider(provider, model):
        return "local"
    if any(token in source for token in ("opus", "gpt-5", "gemini-2.5-pro", "large", "70b")):
        return "high"
    if source.strip():
        return "medium"
    return "unknown"


def _infer_latency_tier(provider: str, model: str, *, size_class: str = "") -> str:
    source = f"{provider} {model} {size_class}".lower()
    if any(token in source for token in ("embedding", "reranker", "8b", "9b")):
        return "low"
    if _is_local_provider(provider, model):
        return "medium"
    if source.strip():
        return "high"
    return "unknown"


def _infer_context_tokens(model: str, size_class: str) -> int:
    source = f"{model} {size_class}".lower()
    if any(token in source for token in ("kimi", "gemini", "claude")):
        return 128000
    if any(token in source for token in ("gpt", "qwen", "glm")):
        return 32768
    return 8192


def _infer_tool_support(model: str, domain: str) -> bool:
    source = f"{model} {domain}".lower()
    return any(token in source for token in ("agent", "tool", "planning", "coding", "qwen", "gpt", "claude", "gemini", "glm"))


def _is_local_provider(provider: str, model: str) -> bool:
    source = f"{provider} {model}".lower()
    return any(token in source for token in ("ollama", "lmstudio", "lm_studio", "llamacpp", "llama_cpp", "llama.cpp", "local", "localhost", "qwen/qwen", "bge", "nomic"))


def _split_domain(domain: str) -> list[str]:
    return [item for item in re.split(r"[_\s,]+", domain or "") if item]
