from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from backend.orchestrator.brain_router import BrainRouter, BrainRouterDecision


class ModelCallCoordinator:
    """Own model routing and per-request model parameters for Agent calls."""

    PRIMARY_AGENT_IDS = frozenset({"main_text", "agent_cluster"})
    REASONING_EFFORTS = frozenset({"auto", "none", "low", "medium", "high"})
    TEXT_MODES = frozenset({"fast", "balanced", "strong"})

    def __init__(
        self,
        llm_client: Callable[..., str],
        brain_router: BrainRouter,
        *,
        input_metadata: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._brain_router = brain_router
        self._input_metadata = input_metadata or (lambda: {})

    def call(self, prompt: str, *args: Any, **kwargs: Any) -> str:
        requested_agent = str(kwargs.pop("agent_name", "") or "").strip()
        agent_name = requested_agent or self.infer_agent_name(prompt)
        routed_kwargs = dict(kwargs)
        decision = self.route(agent_name or "main_text", prompt, route="llm_call")
        if agent_name:
            routed_kwargs.setdefault("agent_name", agent_name)
        if decision.provider:
            routed_kwargs.setdefault("provider", decision.provider)
        if decision.model:
            routed_kwargs.setdefault("model_name", decision.model)
        metadata = dict(self._input_metadata() or {})
        primary_request = agent_name in self.PRIMARY_AGENT_IDS
        ios_request = str(metadata.get("input_channel") or metadata.get("channel") or "").lower() == "ios"
        if primary_request:
            model_id = str(metadata.get("model_id") or "").strip()
            if model_id and metadata.get("model_selection_validated") is True:
                routed_kwargs.setdefault("model_id", model_id)
        if primary_request or ios_request:
            reasoning_effort = str(metadata.get("reasoning_effort") or "auto").strip().lower()
            routed_kwargs.setdefault(
                "reasoning_effort",
                reasoning_effort if reasoning_effort in self.REASONING_EFFORTS else "auto",
            )
            text_mode = str(metadata.get("text_mode") or metadata.get("model_mode") or "").strip().lower()
            if text_mode in self.TEXT_MODES:
                routed_kwargs.setdefault("mode", text_mode)
            try:
                max_new_tokens = int(metadata.get("max_new_tokens") or 0)
            except (TypeError, ValueError):
                max_new_tokens = 0
            if 8 <= max_new_tokens <= 4096:
                routed_kwargs.setdefault("max_new_tokens", max_new_tokens)
            try:
                request_timeout = float(metadata.get("model_timeout_seconds") or 0)
            except (TypeError, ValueError):
                request_timeout = 0
            if 1 <= request_timeout <= 180:
                routed_kwargs.setdefault("request_timeout", request_timeout)
        routed_kwargs.setdefault("brain_profile", decision.brain_profile)
        routed_kwargs.setdefault("brain_route", decision.route)
        try:
            return self._llm_client(prompt, *args, **routed_kwargs)
        except TypeError:
            compatible_kwargs = dict(routed_kwargs)
            compatible_kwargs.pop("agent_name", None)
            compatible_kwargs.pop("brain_profile", None)
            compatible_kwargs.pop("brain_route", None)
            try:
                return self._llm_client(prompt, *args, **compatible_kwargs)
            except TypeError:
                return self._llm_client(prompt, *args, **kwargs)

    def route(
        self,
        agent_id: str,
        task_text: str = "",
        *,
        route: str = "",
        domain: str = "",
        risk_level: str = "",
        required_capabilities: list[str] | tuple[str, ...] | None = None,
    ) -> BrainRouterDecision:
        metadata = dict(self._input_metadata() or {})
        preferred_profile = None
        if agent_id in self.PRIMARY_AGENT_IDS and metadata.get("model_selection_validated") is True:
            selected_model = str(metadata.get("model_name") or "").strip()
            selected_provider = str(metadata.get("model_provider") or "").strip()
            selected_model_id = str(metadata.get("model_id") or "").strip()
            if selected_model_id and selected_model and selected_provider:
                preferred_profile = {
                    "provider": selected_provider,
                    "model": selected_model,
                    "model_id": selected_model_id,
                    "brain_profile": f"composer_{selected_model_id}",
                }
        return self._brain_router.route(
            agent_id=agent_id or "main_text",
            task_text=task_text,
            route=route,
            domain=domain,
            risk_level=risk_level,
            required_capabilities=required_capabilities,
            preferred_profile=preferred_profile,
        )

    @staticmethod
    def infer_agent_name(prompt: str) -> str:
        prompt_text = str(prompt or "")
        markers = (
            ("编程助理", "programming"),
            ("电商助理", "ecommerce"),
            ("视频动画助理", "video_animation"),
            ("游戏制作助理", "game_development"),
        )
        for marker, agent_name in markers:
            if marker in prompt_text:
                return agent_name
        return ""
