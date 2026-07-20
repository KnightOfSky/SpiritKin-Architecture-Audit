from __future__ import annotations

from typing import Any


class DefaultAgentClusterAppPort:
    """Bind app-owned state and learning workflows to AgentCluster ports."""

    def workflow_management_snapshot(self) -> dict[str, Any]:
        from backend.app.workflow_management import build_workflow_management_snapshot

        return dict(build_workflow_management_snapshot() or {})

    def model_catalog_snapshot(self) -> dict[str, Any]:
        from backend.app.model_catalog import load_model_catalog

        return dict(load_model_catalog() or {})

    def skill_assist_policy(self) -> dict[str, Any]:
        from backend.app.agent_management import load_agent_management_state

        state = load_agent_management_state()
        return dict(state.skill_assist.snapshot() or {})

    def build_skill_assist_prompt(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        context: str,
    ) -> str:
        from backend.app.learning_workflow import build_review_prompt

        return build_review_prompt(
            problem,
            skill_name=skill_name,
            context=f"用户输入：{user_input}\n{context}".strip(),
        )

    def record_skill_failure_assist(
        self,
        *,
        skill_name: str,
        problem: str,
        user_input: str,
        policy: dict[str, Any],
        review_prompt: str,
    ) -> dict[str, Any]:
        from backend.app.learning_workflow import (
            append_learning_record,
            export_learning_dataset,
            request_model_review,
            request_multi_model_review,
        )

        record = append_learning_record(
            {
                "source": "skill_runtime",
                "skill_name": skill_name,
                "problem": problem,
                "correction": review_prompt,
                "project": "runtime",
                "tags": ["skill_failure", str(policy.get("mode") or "human_review")],
                "metadata": {"user_input": user_input},
            }
        )
        dataset = export_learning_dataset()
        result: dict[str, Any] = {
            "learning_record": record.snapshot(),
            "dataset": dict(dataset.get("export") or {}),
        }
        assist_mode = str(policy.get("mode") or "")
        if policy.get("allow_external_model") and assist_mode in {"cloud_model_review", "multi_model_review"}:
            multi_review = request_multi_model_review(problem, skill_name=skill_name, context=user_input)
            result["multi_model_review"] = multi_review.snapshot()
            if multi_review.reviews:
                result["model_review"] = multi_review.reviews[0].snapshot()
            elif assist_mode == "cloud_model_review":
                result["model_review"] = request_model_review(
                    problem,
                    skill_name=skill_name,
                    context=user_input,
                ).snapshot()
        return result
