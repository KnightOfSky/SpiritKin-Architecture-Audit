from __future__ import annotations

from typing import Any, Protocol


class AgentClusterAppPort(Protocol):
    """App-owned capabilities consumed by the orchestration layer."""

    def workflow_management_snapshot(self) -> dict[str, Any]: ...

    def model_catalog_snapshot(self) -> dict[str, Any]: ...

    def skill_assist_policy(self) -> dict[str, Any]: ...

    def build_skill_assist_prompt(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        context: str,
    ) -> str: ...

    def record_skill_failure_assist(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        policy: dict[str, Any],
        review_prompt: str,
    ) -> dict[str, Any]: ...


class NullAgentClusterAppPort:
    """Standalone orchestrator default with no dependency on backend.app."""

    def workflow_management_snapshot(self) -> dict[str, Any]:
        return {}
    def model_catalog_snapshot(self) -> dict[str, Any]:
        return {}

    def skill_assist_policy(self) -> dict[str, Any]:
        return {}

    def build_skill_assist_prompt(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        context: str,
    ) -> str:
        return ""

    def record_skill_failure_assist(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        policy: dict[str, Any],
        review_prompt: str,
    ) -> dict[str, Any]:
        return {}
