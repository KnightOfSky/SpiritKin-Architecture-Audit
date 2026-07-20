import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.collaboration import (
    handle_collaboration_action,
    post_collaboration_message,
)
from backend.app.collaboration_participants import (
    build_collaboration_participant_registry,
    collaboration_participants_mentioned_in_text,
    resolve_collaboration_participant,
)
from backend.orchestrator.agent_protocol import JsonlAgentRouteBus


class CollaborationParticipantRegistryTests(unittest.TestCase):
    def test_text_routing_matches_at_alias_and_plain_model_name_without_partial_words(self):
        registry = {
            "aliases": {"deepseek": "programming", "deepseekr1": "programming"},
            "participants": [
                {
                    "participant_id": "programming",
                    "label": "Programming",
                    "kind": "local_agent",
                    "aliases": ["DeepSeek", "deepseek-r1"],
                    "metadata": {"model": "deepseek-r1"},
                }
            ],
        }

        self.assertEqual(
            collaboration_participants_mentioned_in_text("请 @DeepSeek 执行这一部分", registry),
            ("programming",),
        )
        self.assertEqual(
            collaboration_participants_mentioned_in_text("这一部分交给 deepseek-r1 执行", registry),
            ("programming",),
        )
        self.assertEqual(collaboration_participants_mentioned_in_text("deepseek-r10 不应误触发", registry), ())

    def test_human_message_model_mention_overrides_stale_client_recipients(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(Path(tmp) / "route_bus")}, clear=False), patch(
                "backend.app.collaboration.collaboration_participants_mentioned_in_text",
                return_value=("programming",),
            ):
                message = post_collaboration_message(
                    {
                        "thread_id": "thread-model-mention",
                        "from_agent": "human_desktop",
                        "to_agents": ["codex"],
                        "role": "question",
                        "content": "请 deepseek-r1 执行这一部分。",
                    },
                    root=Path(tmp) / "collaboration",
                )

        self.assertEqual(message.to_agents, ("programming",))

    def test_registry_exposes_dynamic_chat_participants_and_aliases(self):
        registry = build_collaboration_participant_registry()
        ids = {item["participant_id"] for item in registry["participants"]}
        self.assertIn("codex", ids)
        self.assertIn("claude_code", ids)
        self.assertIn("programming", ids)
        self.assertEqual(resolve_collaboration_participant("@Codex", registry), "codex")
        self.assertEqual(resolve_collaboration_participant("@Code", registry), "codex")
        self.assertEqual(resolve_collaboration_participant("@编程Agent", registry), "programming")
        qwen = next(item for item in registry["participants"] if item["participant_id"] == "model_lmstudio_qwen35b")
        self.assertIn(qwen["metadata"]["model"], qwen["aliases"])
        self.assertEqual(resolve_collaboration_participant("@qwen/qwen3.6-35b-a3b", registry), "model_lmstudio_qwen35b")

    def test_dynamic_route_bus_accepts_configured_participant(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(Path(tmp) / "route_bus")}, clear=False):
                message = post_collaboration_message(
                    {
                        "thread_id": "thread-unit",
                        "from_agent": "human_desktop",
                        "to_agents": ["programming"],
                        "role": "question",
                        "content": "请读取上下文。",
                    },
                    root=Path(tmp) / "collaboration",
                )
                self.assertTrue(message.route_verdict["allowed"])
                routed = JsonlAgentRouteBus().list_messages(recipient="programming", context_id="thread-unit")
                self.assertEqual(len(routed), 1)
                worker_events = JsonlAgentRouteBus().worker_events(agent="programming", context_id="thread-unit", limit=10)
                lifecycles = [event.get("metadata", {}).get("lifecycle") for event in worker_events]
                self.assertIn("queued", lifecycles)
                self.assertIn("routed", lifecycles)

    def test_answer_message_records_reply_posted_and_terminal_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(Path(tmp) / "route_bus")}, clear=False):
                message = post_collaboration_message(
                    {
                        "thread_id": "thread-answer",
                        "task_id": "task-answer",
                        "from_agent": "codex",
                        "to_agents": ["human_desktop"],
                        "parent_message_id": "agentmsg-parent",
                        "role": "answer",
                        "content": "done",
                    },
                    root=Path(tmp) / "collaboration",
                )
                self.assertEqual(message.role, "answer")
                worker_events = JsonlAgentRouteBus().worker_events(agent="codex", context_id="thread-answer", limit=10)
                lifecycles = [event.get("metadata", {}).get("lifecycle") for event in worker_events]
                self.assertIn("reply_posted", lifecycles)
                self.assertIn("terminal", lifecycles)
                terminal = next(event for event in worker_events if event.get("metadata", {}).get("lifecycle") == "terminal")
                self.assertEqual(terminal["status"], "completed")
                self.assertEqual(terminal["metadata"]["is_terminal"], True)

    def test_message_id_retry_is_idempotent_across_persistence_and_route_bus(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            payload = {
                "message_id": "desktop-stable-send",
                "thread_id": "thread-idempotent",
                "from_agent": "human_desktop",
                "to_agents": ["programming"],
                "role": "question",
                "content": "只执行一次。",
            }
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                first = post_collaboration_message(payload, root=collab_root)
                second = post_collaboration_message(payload, root=collab_root)

                self.assertEqual(first.message_id, second.message_id)
                self.assertEqual(len((collab_root / "messages.jsonl").read_text(encoding="utf-8").splitlines()), 1)
                self.assertEqual(
                    len(JsonlAgentRouteBus().list_messages(recipient="programming", context_id="thread-idempotent")),
                    1,
                )

    def test_message_id_retry_rejects_conflicting_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            payload = {
                "message_id": "desktop-conflict",
                "thread_id": "thread-idempotent",
                "from_agent": "human_desktop",
                "to_agents": ["programming"],
                "role": "question",
                "content": "first",
            }
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                post_collaboration_message(payload, root=collab_root)
                with self.assertRaisesRegex(ValueError, "message_id_conflict"):
                    post_collaboration_message({**payload, "content": "second"}, root=collab_root)

    def test_collaboration_message_preserves_full_access_metadata_in_route_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                message = post_collaboration_message(
                    {
                        "thread_id": "thread-full-access",
                        "from_agent": "human_desktop",
                        "to_agents": ["codex"],
                        "role": "question",
                        "content": "打开命令提示符",
                        "metadata": {"permission_mode": "full_access", "full_access_granted": True},
                    },
                    root=collab_root,
                )

                snapshot = message.snapshot()
                self.assertEqual(snapshot["metadata"]["permission_mode"], "full_access")
                self.assertTrue(snapshot["agent_envelope"]["metadata"]["full_access_granted"])

    def test_full_access_tool_call_skips_second_permission_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "thread-full-access-tool",
                        "target": "local_pc",
                        "operation": "launch_app",
                        "params": {"app_name": "cmd"},
                        "client_id": "desktop",
                        "metadata": {"permission_mode": "full_access", "full_access_granted": True},
                    },
                    root=collab_root,
                )["agent_route_bus_tool_call"]

                self.assertFalse(requested["requires_review"])
                self.assertEqual(requested["tool_call"]["status"], "approved")

    def test_full_access_tool_call_without_trusted_client_still_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "thread-untrusted-full-access-tool",
                        "target": "local_pc",
                        "operation": "launch_app",
                        "params": {"app_name": "cmd"},
                        "metadata": {"permission_mode": "full_access", "full_access_granted": True},
                    },
                    root=collab_root,
                )["agent_route_bus_tool_call"]

                self.assertTrue(requested["requires_review"])
                self.assertEqual(requested["tool_call"]["status"], "permission_required")

    def test_launch_app_normalizes_model_app_alias_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "main_text",
                        "thread_id": "thread-cmd-alias",
                        "target": "local_pc",
                        "operation": "launch_app",
                        "params": {"app": "cmd"},
                        "requires_review": False,
                    },
                    root=collab_root,
                )["agent_route_bus_tool_call"]

                self.assertEqual(requested["tool_call"]["params"]["app_name"], "cmd")

    def test_tool_call_requires_permission_then_executes_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "thread-tool",
                        "target": "local_pc",
                        "operation": "list_installed_apps",
                        "params": {"limit": 1},
                    },
                    root=collab_root,
                )
                call = requested["agent_route_bus_tool_call"]["tool_call"]
                self.assertEqual(call["status"], "approved")

                executed = handle_collaboration_action(
                    {
                        "action": "execute_tool_call",
                        "tool_call_id": call["tool_call_id"],
                        "dry_run": True,
                    },
                    root=collab_root,
                )
                result = executed["agent_route_bus_tool_result"]
                self.assertTrue(result["executed"])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["tool_result"]["status"], "completed")

    def test_same_human_message_deduplicates_tool_call_across_collaborators(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                base = {
                    "action": "request_tool_call",
                    "message_id": "message-one-command",
                    "thread_id": "thread-tool",
                    "target": "local_pc",
                    "operation": "screen_understand",
                    "params": {"query": "当前屏幕"},
                }
                first = handle_collaboration_action({**base, "agent": "codex"}, root=collab_root)["agent_route_bus_tool_call"]
                second = handle_collaboration_action({**base, "agent": "claude_code"}, root=collab_root)["agent_route_bus_tool_call"]

                self.assertTrue(first["requested"])
                self.assertFalse(second["requested"])
                self.assertTrue(second["deduplicated"])
                self.assertEqual(first["tool_call"]["tool_call_id"], second["tool_call"]["tool_call_id"])
                self.assertEqual(len(JsonlAgentRouteBus().tool_calls(limit=100)), 1)

    def test_tool_call_approval_immediately_dispatches_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "thread-approved-tool",
                        "target": "local_pc",
                        "operation": "list_installed_apps",
                        "params": {"limit": 1},
                        "requires_review": True,
                    },
                    root=collab_root,
                )
                call = requested["agent_route_bus_tool_call"]["tool_call"]
                self.assertEqual(call["status"], "permission_required")

                decided = handle_collaboration_action(
                    {
                        "action": "decide_tool_call",
                        "tool_call_id": call["tool_call_id"],
                        "decision": "approved",
                        "dry_run": True,
                    },
                    root=collab_root,
                )

                execution = decided["agent_route_bus_tool_result"]
                self.assertTrue(execution["executed"])
                self.assertEqual(execution["status"], "completed")
                self.assertEqual(execution["tool_call"]["status"], "completed")
                self.assertEqual(decided["agent_route_bus_tool_call"]["worker_event"]["metadata"]["params"], {"limit": 1})

    def test_completed_tool_call_retry_returns_existing_result_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "message_id": "message-retry",
                        "thread_id": "thread-tool",
                        "target": "local_pc",
                        "operation": "list_installed_apps",
                        "params": {"limit": 1},
                    },
                    root=collab_root,
                )
                call_id = requested["agent_route_bus_tool_call"]["tool_call"]["tool_call_id"]
                first = handle_collaboration_action(
                    {"action": "execute_tool_call", "tool_call_id": call_id, "dry_run": True},
                    root=collab_root,
                )["agent_route_bus_tool_result"]
                second = handle_collaboration_action(
                    {"action": "execute_tool_call", "tool_call_id": call_id, "dry_run": True},
                    root=collab_root,
                )["agent_route_bus_tool_result"]

                self.assertTrue(first["executed"])
                self.assertFalse(second["executed"])
                self.assertTrue(second["deduplicated"])
                self.assertEqual(first["tool_result"]["tool_result_id"], second["tool_result"]["tool_result_id"])
                self.assertEqual(len(JsonlAgentRouteBus().tool_results(limit=100)), 1)

    def test_remote_tool_call_blocks_when_worker_is_not_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            route_root = Path(tmp) / "route_bus"
            collab_root = Path(tmp) / "collaboration"
            with patch.dict(os.environ, {"SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(route_root)}, clear=False):
                requested = handle_collaboration_action(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "thread-remote-tool",
                        "target": "remote",
                        "operation": "launch_app",
                        "params": {"app_name": "cmd"},
                        "requires_review": False,
                    },
                    root=collab_root,
                )
                call = requested["agent_route_bus_tool_call"]["tool_call"]

                executed = handle_collaboration_action(
                    {"action": "execute_tool_call", "tool_call_id": call["tool_call_id"]},
                    root=collab_root,
                )

                result = executed["agent_route_bus_tool_result"]
                self.assertFalse(result["executed"])
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["tool_call"]["status"], "blocked")
                self.assertEqual(result["tool_result"]["status"], "blocked")
                self.assertEqual(result["worker_event"]["status"], "blocked")
                self.assertEqual(result["worker_event"]["metadata"]["lifecycle"], "tool_blocked")
                self.assertEqual(result["worker_event"]["metadata"]["blocked_reason"], "worker_not_registered")


if __name__ == "__main__":
    unittest.main()
