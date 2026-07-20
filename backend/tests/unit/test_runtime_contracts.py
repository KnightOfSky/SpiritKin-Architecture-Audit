from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from backend.app.agent_management import save_agent_management_state
from backend.app.collaboration import (
    build_collaboration_snapshot,
    handle_collaboration_action,
    post_collaboration_message,
)
from backend.app.collaboration_worker_status import build_collaboration_worker_config_status
from backend.app.context_write_applier import apply_context_write_intent
from backend.app.learning_workflow import ModelProviderConfig
from backend.app.model_provider_context import attach_model_provider_context_ref
from backend.orchestrator.agent_protocol import AgentEnvelope, AgentRoutePolicy, InMemoryAgentRouter, JsonlAgentRouteBus
from backend.orchestrator.capability_graph import capability_from_skill
from backend.orchestrator.context_mirror import build_project_context_mirror
from backend.orchestrator.context_store import (
    AppendOnlyContextStore,
    ContextPatch,
    JsonlContextStore,
    append_context_patch,
    load_context_patches,
)
from backend.orchestrator.context_write_intents import (
    approve_context_write_intent,
    context_write_intent_snapshot,
    submit_context_write_intent,
)
from backend.orchestrator.execution_finalizer import ExecutionFinalizer, ExecutionSummary
from backend.orchestrator.runtime_metadata import normalize_runtime_metadata
from backend.orchestrator.scheduler_task_finalizer import finalize_scheduler_task, scheduler_task_execution_summary
from backend.orchestrator.task_queue import TaskQueue
from backend.orchestrator.workflow_graph import (
    RUN_SUCCEEDED,
    WorkflowDefinition,
    WorkflowNodeDefinition,
    start_workflow_run,
)
from backend.orchestrator.workflow_runtime_contracts import (
    workflow_run_context_patches,
    workflow_run_contract_snapshot,
    workflow_run_execution_summary,
)
from backend.skills.base import SkillSpec


class RuntimeContractTests(unittest.TestCase):
    def test_runtime_metadata_normalizes_common_fields(self):
        metadata = normalize_runtime_metadata(
            {"status": "active", "tags": ["commerce", "publish"], "success_rate": "0.9", "custom": "kept"},
            object_type="skill",
            object_id="commerce.publish",
            defaults={"domain": "commerce", "risk_level": "medium"},
        )

        snapshot = metadata.snapshot()
        self.assertEqual(snapshot["schema_version"], "spiritkin.runtime_metadata.v1")
        self.assertEqual(snapshot["object_type"], "skill")
        self.assertEqual(snapshot["domain"], "commerce")
        self.assertEqual(snapshot["risk_level"], "medium")
        self.assertEqual(snapshot["success_rate"], 0.9)
        self.assertEqual(snapshot["custom"], "kept")

    def test_context_store_filters_task_and_worker_views(self):
        store = AppendOnlyContextStore()
        store.append_patch(context_id="ctx-1", patch_type="set", actor="master", path="/intent", value={"goal": "publish"})
        store.append_patch(context_id="ctx-1", patch_type="set", actor="worker", path="/worker/android", value={"ready": True})
        store.append_patch(context_id="ctx-1", patch_type="set", actor="agent", path="/agent/private", value={"notes": "hidden"})
        store.append_patch(
            context_id="ctx-1",
            patch_type="set",
            actor="agent",
            path="/diagnostics",
            value={"safe": True},
            metadata={"views": ["worker"]},
        )

        task_paths = [patch.path for patch in store.list_patches(context_id="ctx-1", view="task")]
        worker_paths = [patch.path for patch in store.list_patches(context_id="ctx-1", view="worker")]

        self.assertEqual(task_paths, ["/intent"])
        self.assertEqual(worker_paths, ["/worker/android", "/diagnostics"])

    def test_jsonl_context_store_persists_and_filters_patches(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context_patches.jsonl"
            store = JsonlContextStore(path)
            store.append_patch(
                context_id="ctx-1",
                patch_type="set",
                actor="master",
                path="/task/title",
                value={"title": "Unit"},
                metadata={"views": ["task"]},
            )
            store.append_patch(
                context_id="ctx-1",
                patch_type="set",
                actor="worker",
                path="/worker/browser",
                value={"ready": True},
            )
            manual = ContextPatch(context_id="ctx-2", patch_type="set", actor="audit", path="/intent", value={"goal": "manual"})
            append_context_patch(manual, path=path)
            reloaded = JsonlContextStore(path)
            task_patches = reloaded.list_patches(context_id="ctx-1", view="task")
            worker_patches = load_context_patches(path=path, context_id="ctx-1", view="worker")
            manual_patches = load_context_patches(path=path, context_id="ctx-2", view="task")

        self.assertEqual(len(task_patches), 1)
        self.assertEqual(task_patches[0].path, "/task/title")
        self.assertEqual(worker_patches[0].path, "/worker/browser")
        self.assertEqual(manual_patches[0].patch_id, manual.patch_id)

    def test_model_provider_action_context_patch_records_health_observation(self):
        with TemporaryDirectory() as tmp:
            previous_store = os.environ.get("SPIRITKIN_CONTEXT_STORE_PATH")
            context_path = Path(tmp) / "context_patches.jsonl"
            os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = str(context_path)
            try:
                provider_action = attach_model_provider_context_ref(
                    {
                        "ok": False,
                        "action": "test_provider",
                        "provider": "unit_provider",
                        "display_name": "Unit Provider",
                        "endpoint": "",
                        "model": "unit-model",
                        "status": "not_configured",
                        "health_status": "not_configured",
                        "duration_ms": 3,
                        "checked_at": 12.5,
                        "model_count": 0,
                        "error": "Provider endpoint is empty.",
                    }
                )
                patches = load_context_patches(
                    path=context_path,
                    context_id="model_provider:unit_provider:unit-model",
                    view="task",
                )
            finally:
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = previous_store

        self.assertEqual(provider_action["context_path"], "/model/providers/health")
        self.assertEqual(patches[-1].path, "/model/providers/health")
        self.assertEqual(patches[-1].value["health_status"], "not_configured")
        self.assertEqual(patches[-1].metadata["source"], "model_provider_health")

    def test_context_write_intent_ledger_submit_approve_reject(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context_write_intents.jsonl"
            submitted = submit_context_write_intent(
                {
                    "context_id": "project:unit",
                    "target_path": "project/active/title",
                    "operation": "set",
                    "payload": {"title": "Unit"},
                    "actor": "unit-test",
                    "requires_review": True,
                },
                path=path,
            )
            approved = approve_context_write_intent(submitted.intent_id, reviewer="reviewer", review_note="ok", path=path)
            rejected = submit_context_write_intent({"operation": "set", "payload": {"bad": True}}, path=path)
            snapshot = context_write_intent_snapshot(path=path)

        self.assertEqual(submitted.status, "submitted")
        self.assertEqual(submitted.target_path, "/project/active/title")
        self.assertEqual(approved.status, "approved")
        self.assertEqual(approved.reason, "approved_but_not_applied")
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(snapshot["status_counts"]["approved"], 1)
        self.assertEqual(snapshot["status_counts"]["rejected"], 1)

    def test_context_write_applier_only_updates_approved_context_policy(self):
        with TemporaryDirectory() as tmp:
            previous_policy = os.environ.get("SPIRITKIN_CONTEXT_STATE_PATH")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            previous_store = os.environ.get("SPIRITKIN_CONTEXT_STORE_PATH")
            os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = str(Path(tmp) / "context.json")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = str(Path(tmp) / "context_patches.jsonl")
            try:
                submitted = submit_context_write_intent(
                    {
                        "context_id": "project:unit",
                        "target_path": "/context/policy",
                        "operation": "merge",
                        "payload": {"mode": "compact", "max_recent_messages": 6, "unknown": "ignored"},
                    }
                )
                not_approved = apply_context_write_intent(submitted.intent_id)
                approve_context_write_intent(submitted.intent_id, reviewer="unit")
                applied = apply_context_write_intent(submitted.intent_id)
                patches = load_context_patches(path=Path(tmp) / "context_patches.jsonl", context_id="project:unit", view="task")
                blocked = submit_context_write_intent(
                    {
                        "context_id": "project:unit",
                        "target_path": "/project/active/title",
                        "operation": "set",
                        "payload": {"title": "blocked"},
                    }
                )
                approve_context_write_intent(blocked.intent_id, reviewer="unit")
                blocked_result = apply_context_write_intent(blocked.intent_id)
            finally:
                if previous_policy is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STATE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STATE_PATH"] = previous_policy
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = previous_store

        self.assertFalse(not_approved.ok)
        self.assertEqual(not_approved.error, "context_write_intent_not_approved")
        self.assertTrue(applied.ok)
        self.assertEqual(applied.policy.snapshot()["mode"], "compact")
        self.assertEqual(applied.policy.snapshot()["max_recent_messages"], 6)
        self.assertNotIn("unknown", applied.applied_payload)
        self.assertEqual(applied.context_patch.path, "/context/write_intents/applied")
        self.assertEqual(patches[-1].value["intent_id"], submitted.intent_id)
        self.assertEqual(patches[-1].value["result_type"], "context_policy")
        self.assertEqual(applied.intent.status, "applied")
        self.assertFalse(blocked_result.ok)
        self.assertEqual(blocked_result.error, "target_path_not_applicable")

    def test_context_write_applier_creates_project_overview_proposal_without_overwrite(self):
        with TemporaryDirectory() as tmp:
            overview_path = Path(tmp) / "overview.md"
            review_path = Path(tmp) / "overview_reviews.jsonl"
            overview_path.write_text("# Base\n\nOriginal.", encoding="utf-8")
            previous_overview = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_PATH")
            previous_review = os.environ.get("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = str(overview_path)
            os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = str(review_path)
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                submitted = submit_context_write_intent(
                    {
                        "context_id": "project:unit",
                        "target_path": "/project/overview/proposal",
                        "operation": "merge",
                        "payload": {"append_markdown": "## Proposed\n\nNew section.", "author": "unit"},
                    }
                )
                approve_context_write_intent(submitted.intent_id, reviewer="unit")
                applied = apply_context_write_intent(submitted.intent_id)
                current_text = overview_path.read_text(encoding="utf-8")
            finally:
                if previous_overview is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_PATH"] = previous_overview
                if previous_review is None:
                    os.environ.pop("SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH", None)
                else:
                    os.environ["SPIRITKIN_PROJECT_OVERVIEW_REVIEW_PATH"] = previous_review
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertTrue(applied.ok)
        self.assertEqual(applied.message, "project_overview_proposal_created")
        self.assertEqual(applied.project_overview_change.status, "pending")
        self.assertIn("Proposed", applied.project_overview_change.proposed_markdown)
        self.assertEqual(current_text, "# Base\n\nOriginal.")

    def test_context_write_applier_posts_collaboration_message(self):
        with TemporaryDirectory() as tmp:
            previous_collab = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                submitted = submit_context_write_intent(
                    {
                        "context_id": "thread:unit",
                        "target_path": "/collaboration/message",
                        "operation": "append",
                        "payload": {
                            "thread_id": "thread-unit",
                            "from_agent": "codex",
                            "to_agents": ["claude_code"],
                            "role": "question",
                            "content": "Review this context write path.",
                        },
                    }
                )
                not_approved = apply_context_write_intent(submitted.intent_id)
                approve_context_write_intent(submitted.intent_id, reviewer="unit")
                applied = apply_context_write_intent(submitted.intent_id)
            finally:
                if previous_collab is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_collab
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertFalse(not_approved.ok)
        self.assertTrue(applied.ok)
        self.assertEqual(applied.message, "collaboration_message_posted")
        self.assertEqual(applied.collaboration_message.from_agent, "codex")
        self.assertEqual(applied.collaboration_message.to_agents, ("claude_code",))
        self.assertEqual(applied.collaboration_message.snapshot()["agent_envelope"]["sender"], "codex")

    def test_context_write_applier_records_collaboration_decision_and_review(self):
        with TemporaryDirectory() as tmp:
            previous_collab = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_intents = os.environ.get("SPIRITKIN_CONTEXT_WRITE_INTENTS")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = str(Path(tmp) / "context_write_intents.jsonl")
            try:
                decision_intent = submit_context_write_intent(
                    {
                        "context_id": "thread:unit",
                        "target_path": "/collaboration/decision",
                        "operation": "append",
                        "payload": {
                            "task_id": "task-unit",
                            "title": "Use governed writes",
                            "decision": "Use ContextWriteIntent for low-risk writes.",
                            "rationale": "Keeps state mutation auditable.",
                            "actor": "codex",
                        },
                    }
                )
                review_intent = submit_context_write_intent(
                    {
                        "context_id": "thread:unit",
                        "target_path": "/collaboration/review",
                        "operation": "append",
                        "payload": {
                            "task_id": "task-unit",
                            "reviewer": "claude_code",
                            "verdict": "pass",
                            "summary": "Looks bounded.",
                            "evidence": ["context_write_applier.py"],
                        },
                    }
                )
                approve_context_write_intent(decision_intent.intent_id, reviewer="unit")
                approve_context_write_intent(review_intent.intent_id, reviewer="unit")
                decision_result = apply_context_write_intent(decision_intent.intent_id)
                review_result = apply_context_write_intent(review_intent.intent_id)
            finally:
                if previous_collab is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_collab
                if previous_intents is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_WRITE_INTENTS", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_WRITE_INTENTS"] = previous_intents

        self.assertTrue(decision_result.ok)
        self.assertEqual(decision_result.message, "collaboration_decision_recorded")
        self.assertEqual(decision_result.collaboration_decision.actor, "codex")
        self.assertTrue(review_result.ok)
        self.assertEqual(review_result.message, "collaboration_review_recorded")
        self.assertEqual(review_result.collaboration_review.reviewer, "claude_code")

    def test_context_mirror_projects_sessions_collaboration_and_ecommerce(self):
        mirror = build_project_context_mirror(
            desktop_state={
                "revision": 3,
                "active_session_id": "session-1",
                "sessions": [
                    {
                        "id": "session-1",
                        "title": "Main",
                        "status": "active",
                        "project_id": "project-1",
                        "messages": [{"id": "m1", "role": "user", "text": "继续", "created_at": 1.0}],
                        "updated_at": 2.0,
                    }
                ],
                "projects": [{"id": "project-1", "title": "SpiritKinAI", "status": "active", "workspace_path": "D:/SpiritKinAI"}],
                "tasks": [],
            },
            collaboration_snapshot={
                "overview": {"task_count": 1, "active_task_count": 1, "message_count": 1, "unread_message_count": 1},
                "active_tasks": [{"task_id": "collab-1", "title": "Wire context", "owner": "codex", "status": "active"}],
                "agent_route_bus": {
                    "total": 2,
                    "routed": 1,
                    "blocked": 1,
                    "recent_messages": [{"message_id": "agentmsg-1"}],
                    "recent_audit_events": [{"event_id": "audit-1"}, {"event_id": "audit-2"}],
                    "storage": {"messages": "state/agent_route_bus/messages.jsonl"},
                },
                "agent_route_bus_worker": {
                    "mode": "dry_run_only",
                    "real_worker_status": "not_enabled",
                    "dry_run_available": True,
                    "pending_count": 1,
                    "ack_count": 0,
                    "worker_event_count": 1,
                    "recent_worker_events": [{"event_id": "worker-event-1", "status": "failed"}],
                    "agents": [
                        {
                            "agent": "claude_code",
                            "worker_mode": "dry_run_only",
                            "real_worker_status": "not_enabled",
                            "pending_count": 1,
                            "ack_count": 0,
                            "latest_worker_event": {"event_id": "worker-event-1", "status": "failed"},
                            "external_worker": {"external_assistant": {"status": "disabled"}},
                        }
                    ],
                    "storage": {"message_acks": "state/agent_route_bus/message_acks.jsonl"},
                },
                "recent_messages": [
                    {
                        "message_id": "msg-1",
                        "thread_id": "thread-1",
                        "agent_envelope": {"sender": "codex", "recipient": "claude_code", "message_type": "question", "content": "Review"},
                    }
                ],
            },
            ecommerce_queue={
                "updated_at": "2026-06-29T00:00:00+00:00",
                "tasks": [{"id": "eco-1", "type": "source_image_upload", "status": "workflow_complete", "workflow_run_id": "wfr-1"}],
            },
            context_id="project:unit",
        )

        snapshot = mirror.snapshot(view="task")
        paths = [patch["path"] for patch in snapshot["context"]["patches"]]

        self.assertEqual(snapshot["source_count"], 3)
        self.assertIn("/desktop/active_session", paths)
        self.assertIn("/project/active", paths)
        self.assertIn("/collaboration/summary", paths)
        self.assertIn("/agent_route_bus/summary", paths)
        self.assertIn("/agent_route_bus/worker_status", paths)
        self.assertIn("/ecommerce/queue_summary", paths)
        route_bus_summary = next(patch for patch in snapshot["context"]["patches"] if patch["path"] == "/agent_route_bus/summary")
        self.assertEqual(route_bus_summary["value"]["routed"], 1)
        self.assertEqual(route_bus_summary["value"]["blocked"], 1)
        route_bus_worker = next(patch for patch in snapshot["context"]["patches"] if patch["path"] == "/agent_route_bus/worker_status")
        self.assertEqual(route_bus_worker["value"]["real_worker_status"], "not_enabled")
        self.assertEqual(route_bus_worker["value"]["agents"][0]["pending_count"], 1)
        self.assertEqual(route_bus_worker["value"]["agents"][0]["latest_worker_event"]["status"], "failed")
        self.assertEqual(route_bus_worker["value"]["agents"][0]["external_assistant_status"], "disabled")
        ecommerce_summary = next(patch for patch in snapshot["context"]["patches"] if patch["path"] == "/ecommerce/queue_summary")
        self.assertEqual(ecommerce_summary["value"]["status_counts"]["workflow_complete"], 1)

    def test_agent_router_keeps_structured_messages(self):
        router = InMemoryAgentRouter()
        router.send(
            AgentEnvelope(
                sender="codex",
                recipient="claude_code",
                message_type="review_request",
                content={"question": "check finalizer"},
                context_id="ctx-1",
                task_id="task-1",
                requires_review=True,
            )
        )

        messages = router.list_messages(recipient="claude_code", context_id="ctx-1")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_type, "review_request")
        self.assertTrue(messages[0].snapshot()["requires_review"])

    def test_agent_router_blocks_direct_worker_and_unreviewed_privileged_scope(self):
        router = InMemoryAgentRouter(
            AgentRoutePolicy(
                allowed_senders=("codex", "claude_code", "human_desktop"),
                allowed_recipients=("codex", "claude_code", "human_desktop", "worker"),
            )
        )
        worker_result = router.try_send(
            AgentEnvelope(
                sender="codex",
                recipient="worker",
                message_type="handoff",
                content={"request": "run device command"},
                context_id="ctx-1",
            )
        )
        execute_result = router.try_send(
            AgentEnvelope(
                sender="codex",
                recipient="claude_code",
                message_type="review_request",
                content="please execute",
                context_id="ctx-1",
                permission_scope="execute",
                requires_review=False,
            )
        )
        reviewed_result = router.try_send(
            AgentEnvelope(
                sender="codex",
                recipient="claude_code",
                message_type="review_request",
                content="please review the execution plan",
                context_id="ctx-1",
                permission_scope="write_intent",
                requires_review=True,
            )
        )

        self.assertFalse(worker_result.verdict.allowed)
        self.assertIn("recipient_blocked:worker", worker_result.verdict.issues)
        self.assertFalse(execute_result.verdict.allowed)
        self.assertIn("permission_scope_not_allowed:execute", execute_result.verdict.issues)
        self.assertTrue(reviewed_result.verdict.allowed)
        snapshot = router.snapshot()
        self.assertEqual(snapshot["routed"], 1)
        self.assertEqual(snapshot["blocked"], 2)
        self.assertEqual(snapshot["audit_events"][-1]["message_id"], reviewed_result.envelope.message_id)

    def test_jsonl_agent_route_bus_persists_messages_and_audit(self):
        with TemporaryDirectory() as tmp:
            bus = JsonlAgentRouteBus(
                root=Path(tmp) / "agent_route_bus",
                policy=AgentRoutePolicy(
                    allowed_senders=("codex", "human_desktop"),
                    allowed_recipients=("claude_code", "worker"),
                ),
            )
            allowed = bus.try_send(
                AgentEnvelope(
                    sender="codex",
                    recipient="claude_code",
                    message_type="question",
                    content="请评审 Context ledger.",
                    context_id="ctx-1",
                    task_id="task-1",
                )
            )
            blocked = bus.try_send(
                AgentEnvelope(
                    sender="codex",
                    recipient="worker",
                    message_type="handoff",
                    content="run command",
                    context_id="ctx-1",
                )
            )
            reloaded = JsonlAgentRouteBus(root=Path(tmp) / "agent_route_bus")
            messages = reloaded.list_messages(recipient="claude_code", context_id="ctx-1")
            audit = reloaded.audit_events()

        self.assertTrue(allowed.verdict.allowed)
        self.assertFalse(blocked.verdict.allowed)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, allowed.envelope.message_id)
        self.assertEqual(len(audit), 2)
        self.assertEqual(sum(1 for event in audit if not event["allowed"]), 1)

    def test_jsonl_agent_route_bus_ack_filters_by_consumer(self):
        with TemporaryDirectory() as tmp:
            bus = JsonlAgentRouteBus(root=Path(tmp) / "agent_route_bus")
            sent = bus.send(
                AgentEnvelope(
                    sender="codex",
                    recipient="claude_code",
                    message_type="question",
                    content="ack this",
                    context_id="ctx-ack",
                )
            )
            bus.ack_message(message_id=sent.message_id, consumer="claude_code", note="consumed")
            open_for_claude = bus.list_messages(recipient="claude_code", context_id="ctx-ack", consumer="claude_code", include_acked=False)
            open_for_codex = bus.list_messages(recipient="claude_code", context_id="ctx-ack", consumer="codex", include_acked=False)
            acks = bus.ack_events(consumer="claude_code")

        self.assertEqual(open_for_claude, [])
        self.assertEqual(len(open_for_codex), 1)
        self.assertEqual(acks[0]["message_id"], sent.message_id)

    def test_jsonl_agent_route_bus_records_worker_events(self):
        with TemporaryDirectory() as tmp:
            bus = JsonlAgentRouteBus(root=Path(tmp) / "agent_route_bus")
            event = bus.record_worker_event(
                agent="Claude Code",
                status="failed",
                message_id="agentmsg-failed",
                context_id="ctx-worker",
                task_id="task-worker",
                transport="route_bus",
                dry_run=False,
                error="assistant is not enabled",
                metadata={"script": "collaboration_agent_worker"},
            )
            reloaded = JsonlAgentRouteBus(root=Path(tmp) / "agent_route_bus")
            events = reloaded.worker_events(agent="claude_code")
            context_events = reloaded.worker_events(agent="claude_code", context_id="ctx-worker")
            other_context_events = reloaded.worker_events(agent="claude_code", context_id="ctx-other")
            snapshot = reloaded.snapshot()

        self.assertEqual(event["agent"], "claude_code")
        self.assertEqual(events[0]["message_id"], "agentmsg-failed")
        self.assertEqual(len(context_events), 1)
        self.assertEqual(other_context_events, [])
        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["error"], "assistant is not enabled")
        self.assertEqual(snapshot["worker_event_count"], 1)
        self.assertIn("worker_events", snapshot["storage"])

    def test_execution_finalizer_commits_only_verified_completed_tasks(self):
        finalizer = ExecutionFinalizer(commit_threshold=0.8)
        verdict = finalizer.finalize(
            ExecutionSummary(
                task_id="task-1",
                status="COMPLETED",
                success=True,
                success_criteria=("uploaded",),
                metadata={"success_checks": {"uploaded": True}},
            )
        )

        self.assertEqual(verdict.decision, "commit")
        self.assertEqual(verdict.next_status, "COMMITTED")

    def test_execution_finalizer_blocks_missing_success_criteria(self):
        finalizer = ExecutionFinalizer(commit_threshold=0.8)
        verdict = finalizer.finalize(
            ExecutionSummary(
                task_id="task-2",
                status="COMPLETED",
                success=True,
                success_criteria=("uploaded",),
                metadata={"success_checks": {}},
            )
        )

        self.assertEqual(verdict.decision, "retry")
        self.assertIn("missing_success_criteria", verdict.reasons)

    def test_scheduler_task_finalizer_maps_complete_task_to_commit(self):
        with TemporaryDirectory() as tmp:
            previous_store = os.environ.get("SPIRITKIN_CONTEXT_STORE_PATH")
            context_path = Path(tmp) / "context_patches.jsonl"
            os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = str(context_path)
            try:
                queue = TaskQueue()
                task = queue.enqueue(
                    request="整理项目",
                    visual_context="",
                    plan=SimpleNamespace(route="agent", domain="programming", priority_score=5, resource_profile="gpu_heavy"),
                    project_id="project-unit",
                )
                queue.start(task.task_id, "start")
                complete_task = queue.complete(task.task_id, result_summary="done")
                summary = scheduler_task_execution_summary(complete_task)
                verdict = finalize_scheduler_task(complete_task)
                updated = queue.apply_finalizer_verdict(complete_task.task_id, verdict)
                patches = load_context_patches(path=context_path, context_id=f"task:{task.task_id}", view="task")
            finally:
                if previous_store is None:
                    os.environ.pop("SPIRITKIN_CONTEXT_STORE_PATH", None)
                else:
                    os.environ["SPIRITKIN_CONTEXT_STORE_PATH"] = previous_store

        self.assertEqual(summary.status, "COMPLETED")
        self.assertTrue(summary.success)
        self.assertEqual(verdict.decision, "commit")
        finalizer_snapshot = updated.snapshot()["finalizer"]
        self.assertEqual(finalizer_snapshot["next_status"], "COMMITTED")
        self.assertEqual(finalizer_snapshot["context_id"], f"task:{task.task_id}")
        self.assertEqual(finalizer_snapshot["context_path"], "/scheduler/tasks/finalizer")
        self.assertEqual(patches[-1].path, "/scheduler/tasks/finalizer")
        self.assertEqual(patches[-1].value["finalizer"]["decision"], "commit")
        self.assertEqual(patches[-1].metadata["source"], "scheduler_task_finalizer")

    def test_scheduler_task_finalizer_retries_failed_and_waits_running_tasks(self):
        queue = TaskQueue()
        plan = SimpleNamespace(route="agent", domain="programming", priority_score=5, resource_profile="gpu_heavy")
        failed = queue.enqueue(request="失败任务", visual_context="", plan=plan)
        queue.start(failed.task_id, "start")
        failed = queue.fail(failed.task_id, "boom")
        failed_verdict = finalize_scheduler_task(failed)
        blocked = queue.enqueue(request="阻塞任务", visual_context="", plan=plan)
        queue.start(blocked.task_id, "start")
        blocked = queue.block(blocked.task_id, "waiting")
        blocked_verdict = finalize_scheduler_task(blocked)
        queued = queue.enqueue(request="等待任务", visual_context="", plan=plan)
        queued_verdict = finalize_scheduler_task(queued)

        self.assertEqual(failed_verdict.decision, "retry")
        self.assertEqual(blocked_verdict.decision, "retry")
        self.assertEqual(queued_verdict.decision, "wait")

    def test_skill_runtime_metadata_feeds_capability_graph(self):
        skill = SkillSpec(
            name="commerce.publish",
            description="publish product",
            input_schema={"type": "object"},
            output_schema={"type": "object", "required": ["listing_id"]},
            cost_hint="medium",
            latency_hint_ms=2000,
            success_rate=0.85,
            required_capabilities=("commerce.publish",),
            required_worker_needs=("browser", "android"),
            side_effects=("network_write",),
            artifact_contract={"produces": ["listing"]},
            metadata={"status": "active", "domain": "commerce"},
        )

        record = capability_from_skill(skill)
        snapshot = record.snapshot()

        self.assertEqual(snapshot["domain"], "commerce")
        self.assertEqual(snapshot["worker_requirements"], ["browser", "android"])
        self.assertIn("commerce.publish", snapshot["tags"])
        self.assertEqual(snapshot["metadata"]["success_rate"], 0.85)
        self.assertEqual(snapshot["bindings"][0]["metadata"]["output_schema"]["required"], ["listing_id"])

    def test_workflow_run_contract_builds_context_and_finalizer_input(self):
        definition = WorkflowDefinition(
            name="publish.workflow",
            metadata={"success_criteria": ["listing_created"]},
            nodes=(WorkflowNodeDefinition("publish", "agent_task", assigned_agent="commerce"),),
        )
        run = start_workflow_run(definition, run_id="run-1")
        run = replace(
            run,
            status=RUN_SUCCEEDED,
            events=[
                *run.events,
                {"type": "success_checks", "payload": {"success_checks": {"listing_created": True}}},
            ],
        )

        summary = workflow_run_execution_summary(definition, run)
        patches = workflow_run_context_patches(definition, run, context_id="ctx-1")
        contract = workflow_run_contract_snapshot(definition, run, context_id="ctx-1")

        self.assertEqual(summary.status, "COMPLETED")
        self.assertTrue(summary.metadata["success_checks"]["listing_created"])
        self.assertEqual(patches[0].path, "/workflow/run")
        self.assertEqual(contract["context_id"], "ctx-1")
        self.assertEqual(contract["execution_summary"]["success_criteria"], ["listing_created"])

    def test_collaboration_message_exposes_agent_envelope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                message = post_collaboration_message(
                    {
                        "from_agent": "codex",
                        "to_agent": "ClaudeCode",
                        "role": "question",
                        "content": "请审查 Runtime Metadata 契约。",
                        "thread_id": "runtime-thread",
                        "task_id": "task-1",
                    },
                    root=Path(tmp) / "collaboration",
                ).snapshot()
                route_bus = JsonlAgentRouteBus(root=Path(tmp) / "agent_route_bus")
                mirrored_messages = route_bus.list_messages(recipient="claude_code", context_id="runtime-thread")
                mirrored_audit = route_bus.audit_events()
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        envelope = message["agent_envelope"]
        self.assertEqual(envelope["schema_version"], "spiritkin.agent_protocol.v1")
        self.assertEqual(envelope["sender"], "codex")
        self.assertEqual(envelope["recipient"], "claude_code")
        self.assertEqual(envelope["message_type"], "question")
        self.assertEqual(envelope["context_id"], "runtime-thread")
        self.assertTrue(message["route_verdict"]["allowed"])
        self.assertEqual(message["route_audit_event"]["action"], "collaboration_message_route")
        self.assertEqual(message["route_audit_event"]["recipients"], ["claude_code"])
        self.assertTrue(message["route_bus_event"]["mirrored"])
        self.assertEqual(message["route_bus_event"]["agent_message_id"], envelope["message_id"])
        self.assertEqual(len(mirrored_messages), 1)
        self.assertEqual(mirrored_messages[0].message_id, envelope["message_id"])
        self.assertEqual(len(mirrored_audit), 1)
        self.assertTrue(mirrored_audit[0]["allowed"])

    def test_collaboration_snapshot_exposes_agent_route_bus_summary(self):
        with TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                post_collaboration_message(
                    {
                        "from_agent": "codex",
                        "to_agent": "ClaudeCode",
                        "role": "question",
                        "content": "同步到底层 route bus。",
                        "thread_id": "runtime-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                snapshot = build_collaboration_snapshot(Path(tmp) / "collaboration")
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        route_bus = snapshot["agent_route_bus"]
        self.assertEqual(route_bus["routed"], 1)
        self.assertEqual(route_bus["blocked"], 0)
        self.assertEqual(len(route_bus["recent_messages"]), 1)
        self.assertEqual(route_bus["recent_messages"][0]["recipient"], "claude_code")

    def test_collaboration_action_reads_agent_route_bus_messages(self):
        with TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                sent = handle_collaboration_action(
                    {
                        "action": "post_message",
                        "from_agent": "codex",
                        "to_agent": "claude_code",
                        "role": "question",
                        "content": "读取 route bus。",
                        "thread_id": "route-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                route_inbox = handle_collaboration_action(
                    {
                        "action": "list_agent_route_bus_messages",
                        "to_agent": "claude_code",
                        "thread_id": "route-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                ack = handle_collaboration_action(
                    {
                        "action": "ack_agent_route_bus_message",
                        "message_id": route_inbox["agent_route_bus_messages"]["messages"][0]["message_id"],
                        "consumer": "claude_code",
                        "note": "worker consumed",
                    },
                    root=Path(tmp) / "collaboration",
                )
                open_inbox = handle_collaboration_action(
                    {
                        "action": "list_agent_route_bus_messages",
                        "to_agent": "claude_code",
                        "consumer": "claude_code",
                        "thread_id": "route-thread",
                        "include_acked": False,
                    },
                    root=Path(tmp) / "collaboration",
                )
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertTrue(sent["message"]["route_bus_event"]["mirrored"])
        inbox = route_inbox["agent_route_bus_messages"]
        self.assertEqual(inbox["message_count"], 1)
        self.assertEqual(inbox["messages"][0]["sender"], "codex")
        self.assertEqual(inbox["messages"][0]["recipient"], "claude_code")
        self.assertEqual(inbox["filters"]["context_id"], "route-thread")
        self.assertTrue(ack["agent_route_bus_ack"]["acked"])
        self.assertEqual(open_inbox["agent_route_bus_messages"]["message_count"], 0)

    def test_collaboration_dry_run_route_bus_worker_processes_one_message(self):
        with TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            previous_auto_reply = os.environ.get("SPIRITKIN_COLLABORATION_AUTO_REPLY")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            # The dry-run worker posts a model→model answer, which requires the
            # explicit auto-reply switch (off by default) even in simulation.
            os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = "1"
            try:
                sent = handle_collaboration_action(
                    {
                        "action": "post_message",
                        "from_agent": "codex",
                        "to_agent": "claude_code",
                        "role": "question",
                        "content": "请 dry-run 处理。",
                        "thread_id": "route-worker-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                worker = handle_collaboration_action(
                    {
                        "action": "run_participant_once",
                        "agent": "claude_code",
                        "thread_id": "route-worker-thread",
                        "dry_run": True,
                        "post_answer": True,
                    },
                    root=Path(tmp) / "collaboration",
                )
                idle = handle_collaboration_action(
                    {
                        "action": "run_agent_route_bus_worker_once",
                        "agent": "claude_code",
                        "thread_id": "route-worker-thread",
                        "dry_run": True,
                    },
                    root=Path(tmp) / "collaboration",
                )
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus
                if previous_auto_reply is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_AUTO_REPLY", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = previous_auto_reply

        self.assertTrue(sent["message"]["route_bus_event"]["mirrored"])
        worker_result = worker["agent_route_bus_worker"]
        self.assertEqual(worker["participant_run"], worker_result)
        self.assertEqual(worker_result["status"], "processed")
        self.assertEqual(worker_result["agent"], "claude_code")
        self.assertEqual(worker_result["message"]["sender"], "codex")
        self.assertEqual(worker_result["ack"]["consumer"], "claude_code")
        self.assertEqual(worker_result["answer"]["from_agent"], "claude_code")
        self.assertEqual(idle["agent_route_bus_worker"]["status"], "idle")

    def test_collaboration_auto_reply_resolves_env_then_file_then_default(self):
        from backend.app.collaboration import (
            collaboration_auto_reply_enabled,
            get_collaboration_auto_reply_state,
            set_collaboration_auto_reply,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "collaboration"
            previous = os.environ.get("SPIRITKIN_COLLABORATION_AUTO_REPLY")
            try:
                # 默认（无 env、无文件）→ 开。
                os.environ.pop("SPIRITKIN_COLLABORATION_AUTO_REPLY", None)
                self.assertTrue(collaboration_auto_reply_enabled(root=root))
                self.assertEqual(get_collaboration_auto_reply_state(root=root)["source"], "default")

                # 文件写 False → 关。
                set_collaboration_auto_reply(False, root=root)
                self.assertFalse(collaboration_auto_reply_enabled(root=root))
                self.assertEqual(get_collaboration_auto_reply_state(root=root)["source"], "file")

                # env 显式覆盖文件。
                os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = "1"
                self.assertTrue(collaboration_auto_reply_enabled(root=root))
                self.assertEqual(get_collaboration_auto_reply_state(root=root)["source"], "env")
                os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = "0"
                set_collaboration_auto_reply(True, root=root)
                self.assertFalse(collaboration_auto_reply_enabled(root=root))
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_AUTO_REPLY", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = previous

    def test_collaboration_auto_reply_gateway_action_get_and_set(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "collaboration"
            previous = os.environ.get("SPIRITKIN_COLLABORATION_AUTO_REPLY")
            os.environ.pop("SPIRITKIN_COLLABORATION_AUTO_REPLY", None)
            try:
                got = handle_collaboration_action({"action": "collaboration_auto_reply", "op": "get"}, root=root)
                self.assertTrue(got["auto_reply"]["enabled"])

                set_result = handle_collaboration_action(
                    {"action": "collaboration_auto_reply", "op": "set", "enabled": False},
                    root=root,
                )
                self.assertFalse(set_result["auto_reply"]["enabled"])
                self.assertEqual(set_result["auto_reply"]["source"], "file")

                got_again = handle_collaboration_action({"action": "collaboration_auto_reply"}, root=root)
                self.assertFalse(got_again["auto_reply"]["enabled"])
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_AUTO_REPLY", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_AUTO_REPLY"] = previous

    def test_collaboration_route_bus_worker_status_reports_pending_by_agent(self):
        with TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                handle_collaboration_action(
                    {
                        "action": "post_message",
                        "from_agent": "codex",
                        "to_agent": "claude_code",
                        "role": "question",
                        "content": "状态检查。",
                        "thread_id": "route-status-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                status = handle_collaboration_action(
                    {
                        "action": "agent_route_bus_worker_status",
                        "agents": ["claude_code", "codex"],
                        "thread_id": "route-status-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                worker = handle_collaboration_action(
                    {
                        "action": "run_agent_route_bus_worker_once",
                        "agent": "claude_code",
                        "thread_id": "route-status-thread",
                        "dry_run": True,
                    },
                    root=Path(tmp) / "collaboration",
                )
                failed_event = handle_collaboration_action(
                    {
                        "action": "record_agent_route_bus_worker_event",
                        "agent": "claude_code",
                        "status": "failed",
                        "message_id": "agentmsg-failed",
                        "thread_id": "route-status-thread",
                        "error": "assistant is not enabled",
                    },
                    root=Path(tmp) / "collaboration",
                )
                handle_collaboration_action(
                    {
                        "action": "record_agent_route_bus_worker_event",
                        "agent": "claude_code",
                        "status": "failed",
                        "message_id": "agentmsg-other-thread",
                        "thread_id": "other-route-thread",
                        "error": "wrong thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                after = handle_collaboration_action(
                    {
                        "action": "agent_route_bus_worker_status",
                        "agents": ["claude_code", "codex"],
                        "thread_id": "route-status-thread",
                    },
                    root=Path(tmp) / "collaboration",
                )
                snapshot = build_collaboration_snapshot(Path(tmp) / "collaboration")
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        before_status = status["agent_route_bus_worker_status"]
        by_agent = {item["agent"]: item for item in before_status["agents"]}
        self.assertIn(before_status["real_worker_status"], {"ready", "not_enabled"})
        self.assertTrue(before_status["dry_run_available"])
        self.assertIn("run_participant_once", before_status["supported_actions"])
        self.assertEqual(by_agent["claude_code"]["pending_count"], 1)
        self.assertEqual(by_agent["codex"]["pending_count"], 0)
        self.assertEqual(worker["agent_route_bus_worker"]["status"], "processed")
        self.assertEqual(worker["agent_route_bus_worker"]["worker_event"]["status"], "processed")
        self.assertTrue(failed_event["agent_route_bus_worker_event"]["recorded"])
        after_by_agent = {item["agent"]: item for item in after["agent_route_bus_worker_status"]["agents"]}
        self.assertEqual(after_by_agent["claude_code"]["pending_count"], 0)
        self.assertEqual(after_by_agent["claude_code"]["ack_count"], 1)
        self.assertEqual(after_by_agent["claude_code"]["latest_worker_event"]["status"], "failed")
        self.assertEqual(after["agent_route_bus_worker_status"]["recent_worker_events"][-1]["error"], "assistant is not enabled")
        self.assertTrue(
            all(
                event.get("context_id") == "route-status-thread"
                for event in after["agent_route_bus_worker_status"]["recent_worker_events"]
            )
        )
        snapshot_by_agent = {item["agent"]: item for item in snapshot["agent_route_bus_worker"]["agents"]}
        self.assertEqual(snapshot_by_agent["claude_code"]["pending_count"], 0)

    def test_collaboration_worker_config_status_reports_external_assistant_readiness(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                save_agent_management_state(
                    {
                        "external_assistants": [
                            {
                                "assistant_id": "codex_cli",
                                "label": "Codex CLI",
                                "command": sys.executable,
                                "enabled": True,
                                "review_only": True,
                            },
                            {
                                "assistant_id": "claude_code",
                                "label": "Claude Code",
                                "command": "definitely-missing-spiritkin-test-cli",
                                "enabled": True,
                                "review_only": True,
                            },
                        ]
                    }
                )
                status = build_collaboration_worker_config_status({"agents": ["codex", "claude_code"]})
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

        by_agent = {item["agent"]: item for item in status["agents"]}
        self.assertEqual(status["real_worker_status"], "ready")
        self.assertTrue(by_agent["codex"]["can_start_real_worker"])
        self.assertEqual(by_agent["codex"]["external_assistant"]["status"], "ready")
        self.assertFalse(by_agent["claude_code"]["can_start_real_worker"])
        self.assertEqual(by_agent["claude_code"]["external_assistant"]["status"], "missing_executable")

    def test_collaboration_route_bus_worker_status_includes_external_worker_config(self):
        with TemporaryDirectory() as tmp:
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                save_agent_management_state(
                    {
                        "external_assistants": [
                            {"assistant_id": "codex_cli", "label": "Codex CLI", "command": sys.executable, "enabled": True},
                            {"assistant_id": "claude_code", "label": "Claude Code", "command": "", "enabled": False},
                        ]
                    }
                )
                status = handle_collaboration_action(
                    {"action": "agent_route_bus_worker_status", "agents": ["codex", "claude_code"]},
                    root=Path(tmp) / "collaboration",
                )
            finally:
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

        worker_status = status["agent_route_bus_worker_status"]
        by_agent = {item["agent"]: item for item in worker_status["agents"]}
        self.assertEqual(worker_status["real_worker_status"], "ready")
        self.assertTrue(worker_status["external_cli_worker_available"])
        self.assertEqual(by_agent["codex"]["real_worker_status"], "ready")
        self.assertEqual(by_agent["codex"]["external_worker"]["external_assistant"]["status"], "ready")
        self.assertEqual(by_agent["claude_code"]["real_worker_status"], "not_enabled")

    def test_collaboration_message_route_policy_blocks_worker_and_privileged_scope(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as blocked_worker:
                post_collaboration_message(
                    {
                        "from_agent": "codex",
                        "to_agent": "worker",
                        "role": "handoff",
                        "content": "run this directly",
                        "thread_id": "runtime-thread",
                    },
                    root=tmp,
                )
            with self.assertRaises(ValueError) as blocked_scope:
                post_collaboration_message(
                    {
                        "from_agent": "codex",
                        "to_agent": "claude_code",
                        "role": "review_request",
                        "content": "please execute",
                        "thread_id": "runtime-thread",
                        "permission_scope": "execute",
                    },
                    root=tmp,
                )

        self.assertIn("recipient_not_allowed", str(blocked_worker.exception))
        self.assertIn("permission_scope_not_allowed", str(blocked_scope.exception))

    def test_model_provider_config_exposes_runtime_metadata(self):
        local = ModelProviderConfig("llama.cpp", "qwen.gguf", True, "http://127.0.0.1:8080/v1")
        cloud = ModelProviderConfig("anthropic", "claude", False, "https://api.anthropic.com")

        local_metadata = local.snapshot()["runtime_metadata"]
        cloud_metadata = cloud.snapshot()["runtime_metadata"]

        self.assertEqual(local_metadata["object_type"], "model_provider")
        self.assertEqual(local_metadata["permission_scope"], "local_model")
        self.assertEqual(local_metadata["data_boundary"], "local")
        self.assertEqual(cloud_metadata["permission_scope"], "cloud_model")
        self.assertEqual(cloud_metadata["status"], "candidate")

    def test_select_provider_prefers_model_match_within_openai_family(self):
        from backend.app.learning_workflow import _select_provider

        lmstudio = ModelProviderConfig("lmstudio", "qwen/qwen3.6-35b-a3b", True, "http://127.0.0.1:1234/v1")
        deepseek = ModelProviderConfig("openai_compatible", "deepseek-v4-pro", True, "https://api.deepseek.com/v1")
        providers = [deepseek, lmstudio]

        # 事故场景：main_text（openai_compatible + qwen）不能被解析到 DeepSeek 端点。
        qwen = _select_provider(providers, "openai_compatible", "qwen/qwen3.6-35b-a3b")
        self.assertEqual(qwen.endpoint, "http://127.0.0.1:1234/v1")

        ds = _select_provider(providers, "openai_compatible", "deepseek-v4-pro")
        self.assertEqual(ds.endpoint, "https://api.deepseek.com/v1")

        # model 无匹配时回退旧行为：按 provider 名匹配。
        fallback = _select_provider(providers, "openai_compatible", "unknown-model")
        self.assertEqual(fallback.endpoint, "https://api.deepseek.com/v1")

        # 跨家族不允许仅凭 model 名劫持。
        anthropic = ModelProviderConfig("anthropic", "claude-3", True, "https://api.anthropic.com")
        hijack = ModelProviderConfig("lmstudio", "claude-3", True, "http://127.0.0.1:1234/v1")
        selected = _select_provider([hijack, anthropic], "anthropic", "claude-3")
        self.assertEqual(selected.provider, "anthropic")

    def test_select_provider_prefers_llamacpp_over_lmstudio_when_unscoped(self):
        from backend.app.learning_workflow import _select_provider

        lmstudio = ModelProviderConfig("lmstudio", "legacy", True, "http://127.0.0.1:1234/v1")
        llamacpp = ModelProviderConfig("llamacpp", "qwen", True, "http://127.0.0.1:8080/v1")

        selected = _select_provider([lmstudio, llamacpp])

        self.assertEqual(selected.provider, "llamacpp")
        self.assertEqual(selected.endpoint, "http://127.0.0.1:8080/v1")


if __name__ == "__main__":
    unittest.main()
