from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.codex_work_events import TRACE_SCHEMA_VERSION
from backend.app.command_gateway import (
    _collaboration_trace_participant_kind,
    _strip_collaboration_mentions,
    build_command_response,
    build_desktop_collaboration_response,
    build_desktop_collaboration_update_response,
)


class CommandGatewayCollaborationTests(unittest.TestCase):
    def test_llamacpp_participants_are_classified_as_model_apis(self):
        for participant_id in ("llamacpp", "llama_cpp", "llama.cpp"):
            with self.subTest(participant_id=participant_id):
                self.assertEqual(_collaboration_trace_participant_kind(participant_id), "model_api")

    def test_stripping_at_mention_keeps_text_after_colon(self):
        self.assertEqual(_strip_collaboration_mentions("@Codex:你好，请检查当前项目"), "你好，请检查当前项目")

    def test_runtime_model_name_with_slash_routes_only_to_canonical_participant(self):
        model_name = "qwen/qwen3.6-35b-a3b"
        registry = {
            "participants": [
                {
                    "participant_id": "model_lmstudio_qwen35b",
                    "label": "LM Studio Qwen 35B",
                    "aliases": ["LMStudioQwen35B", model_name],
                    "can_chat": True,
                },
                {
                    "participant_id": "provider_lmstudio",
                    "label": "LM Studio Qwen 35B",
                    "aliases": ["LMStudio", model_name],
                    "can_chat": True,
                },
            ],
            "aliases": {"qwen/qwen3635ba3b": "model_lmstudio_qwen35b"},
        }
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_COLLABORATION_ROOT": str(Path(tmp) / "collaboration"),
                "SPIRITKIN_AGENT_ROUTE_BUS_ROOT": str(Path(tmp) / "agent_route_bus"),
            },
            clear=False,
        ), patch(
            "backend.app.collaboration_participants.build_collaboration_participant_registry",
            return_value=registry,
        ):
            runtime = unittest.mock.Mock()
            status, payload = build_command_response(
                runtime,
                {
                    "text": f"@{model_name} 检查当前项目",
                    "channel": "desktop",
                    "metadata": {"session_id": "session-runtime-model-name"},
                },
                client_id="desktop",
            )

        self.assertEqual(status, 200)
        runtime.handle_input.assert_not_called()
        self.assertEqual(payload["message"]["to_agents"], ["model_lmstudio_qwen35b"])

    def test_command_with_plain_model_name_redirects_only_to_that_model(self):
        registry = {
            "participants": [
                {
                    "participant_id": "main_text",
                    "label": "Spirit",
                    "aliases": ["Spirit", "主Agent"],
                    "can_chat": True,
                },
                {
                    "participant_id": "model_deepseek",
                    "label": "模型 DeepSeek",
                    "aliases": ["DeepSeek", "deepseek-v4"],
                    "can_chat": True,
                },
            ],
            "aliases": {"spirit": "main_text", "deepseek": "model_deepseek"},
        }
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                runtime = unittest.mock.Mock()
                with patch(
                    "backend.app.collaboration_participants.build_collaboration_participant_registry",
                    return_value=registry,
                ):
                    status, payload = build_command_response(
                        runtime,
                        {
                            "text": "DeepSeek，打开百度",
                            "channel": "desktop",
                            "metadata": {"session_id": "session-direct-model"},
                        },
                        client_id="desktop",
                    )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        runtime.handle_input.assert_not_called()
        self.assertEqual(payload["message"]["to_agents"], ["model_deepseek"])

    def test_command_with_collaboration_mention_redirects_before_runtime(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                runtime = unittest.mock.Mock()
                status, payload = build_command_response(
                    runtime,
                    {
                        "text": "@Codex 你是谁？",
                        "channel": "desktop",
                        "metadata": {
                            "session_id": "session-mention",
                            "project_title": "SpiritKinAI",
                            "workspace_path": "D:\\SpiritKinAI",
                            "request_id": "request-mention",
                            "permission_mode": "full_access",
                            "full_access_granted": True,
                        },
                    },
                    client_id="desktop",
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        runtime.handle_input.assert_not_called()
        self.assertTrue(payload["collaboration_redirect"])
        self.assertIsNone(payload["reply"])
        self.assertEqual(payload["message"]["to_agents"], ["codex"])
        self.assertIn("Workspace path: D:\\SpiritKinAI", payload["message"]["content"])
        self.assertEqual(payload["message"]["metadata"]["permission_mode"], "full_access")
        self.assertTrue(payload["message"]["agent_envelope"]["metadata"]["full_access_granted"])
        self.assertEqual(payload["collaboration"], {})
        self.assertTrue(payload["events"])

    def test_command_with_registry_participant_mention_redirects_before_runtime(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                runtime = unittest.mock.Mock()
                status, payload = build_command_response(
                    runtime,
                    {
                        "text": "@编程Agent 请检查这个项目。",
                        "channel": "desktop",
                        "metadata": {"session_id": "session-programming", "workspace_path": "D:\\SpiritKinAI"},
                    },
                    client_id="desktop",
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        runtime.handle_input.assert_not_called()
        self.assertTrue(payload["collaboration_redirect"])
        self.assertEqual(payload["message"]["to_agents"], ["programming"])
        self.assertEqual(payload["collaboration"], {})

    def test_command_with_codex_typo_alias_redirects_before_runtime(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                runtime = unittest.mock.Mock()
                status, payload = build_command_response(
                    runtime,
                    {
                        "text": "@Code 你是谁？",
                        "channel": "desktop",
                        "metadata": {"session_id": "session-code-alias", "workspace_path": "D:\\SpiritKinAI"},
                    },
                    client_id="desktop",
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        runtime.handle_input.assert_not_called()
        self.assertTrue(payload["collaboration_redirect"])
        self.assertEqual(payload["message"]["to_agents"], ["codex"])
        self.assertIsNone(payload["reply"])

    def test_desktop_collaboration_emits_structured_work_trace_events(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            emitted_events: list[dict] = []

            try:
                with patch("backend.app.command_gateway.dispatch_runtime_event", side_effect=lambda _url, event: emitted_events.append(event) or True):
                    status, payload = build_desktop_collaboration_update_response(
                        {
                            "action": "post_message",
                            "thread_id": "thread-trace",
                            "task_id": "task-trace",
                            "from_agent": "codex",
                            "to_agents": ["claude_code"],
                            "role": "question",
                            "content": "Please review the trace stream.",
                        }
                    )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        self.assertEqual(payload["event"]["type"], "desktop.collaboration_updated")
        self.assertEqual(emitted_events[0]["type"], "desktop.collaboration_updated")
        work_events = payload["work_events"]
        self.assertEqual([event["type"] for event in emitted_events[1:]], ["assistant.work_updated"] * len(work_events))
        self.assertEqual(len(work_events), 3)
        work_payloads = [event["payload"] for event in work_events]
        self.assertEqual([item["seq"] for item in work_payloads], sorted(item["seq"] for item in work_payloads))
        self.assertEqual(work_payloads[-1]["is_terminal"], True)
        self.assertEqual(work_payloads[-1]["status"], "completed")
        for item in work_payloads:
            self.assertEqual(item["trace_schema_version"], TRACE_SCHEMA_VERSION)
            self.assertEqual(item["surface"], "collaboration")
            self.assertEqual(item["channel"], "collaboration")
            self.assertTrue(item["event_id"])
            self.assertTrue(item["run_id"].startswith("collab-thread-trace"))
            self.assertEqual(item["agent_id"], "codex")
            self.assertEqual(item["detail"]["surface"], "collaboration")
            self.assertEqual(item["detail"]["thread_id"], "thread-trace")
            self.assertEqual(item["detail"]["task_id"], "task-trace")
            self.assertEqual(item["detail"]["message_id"], payload["message"]["message_id"])
            self.assertEqual(item["detail"]["participant_id"], "codex")
            self.assertEqual(item["detail"]["run_id"], item["run_id"])
            self.assertTrue(item["detail"]["span_id"])
            self.assertIn("parent_id", item["detail"])
            self.assertIn("tool_call_id", item["detail"])
            self.assertIn("target", item["detail"])
            self.assertIn("operation", item["detail"])
            self.assertIn("stream", item["detail"])
            self.assertIn("output", item["detail"])
            self.assertEqual(item["detail"]["status"], item["status"])
            self.assertEqual(item["detail"]["phase"] in {"agent", "route"}, True)

    def test_human_model_dispatch_trace_uses_persisted_route_results(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "thread_id": "thread-dispatch",
                        "task_id": "thread-dispatch",
                        "from_agent": "human_desktop",
                        "to_agents": ["main_text", "codex"],
                        "role": "question",
                        "content": "Inspect the workspace.",
                    }
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        work_payloads = [event["payload"] for event in payload["work_events"]]
        self.assertEqual(
            [item["detail"]["dispatch_stage"] for item in work_payloads],
            ["accepted", "policy", "route_bus"],
        )
        self.assertTrue(all(item["detail"]["card_kind"] == "model_dispatch" for item in work_payloads))
        self.assertTrue(
            all(
                [target["agent_id"] for target in item["detail"]["call_targets"]]
                == ["main_text", "codex"]
                for item in work_payloads
            )
        )
        self.assertTrue(all(item["detail"]["call_targets"][0]["label"] for item in work_payloads))
        self.assertEqual(work_payloads[-1]["status"], "completed")
        self.assertTrue(work_payloads[-1]["is_terminal"])
        self.assertTrue(work_payloads[-1]["detail"]["route_bus_event"]["mirrored"])

    def test_desktop_collaboration_route_bus_worker_event_action(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            previous_trajectory = os.environ.get("SPIRITKIN_TRAJECTORY_LOG")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            trajectory_path = Path(tmp) / "evolution" / "trajectories.jsonl"
            os.environ["SPIRITKIN_TRAJECTORY_LOG"] = str(trajectory_path)
            try:
                event_status, event_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "record_agent_route_bus_worker_event",
                        "agent": "claude_code",
                        "status": "failed",
                        "message_id": "agentmsg-command-gateway",
                        "thread_id": "gateway-thread",
                        "task_id": "task-gateway",
                        "error": "assistant is not enabled",
                    }
                )
                status_code, status_payload = build_desktop_collaboration_update_response(
                    {
                        "action": "agent_route_bus_worker_status",
                        "agents": ["claude_code"],
                        "thread_id": "gateway-thread",
                        "task_id": "task-gateway",
                    }
                )
                snapshot_status, snapshot = build_desktop_collaboration_response()
                records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus
                if previous_trajectory is None:
                    os.environ.pop("SPIRITKIN_TRAJECTORY_LOG", None)
                else:
                    os.environ["SPIRITKIN_TRAJECTORY_LOG"] = previous_trajectory

        self.assertEqual(event_status, 200)
        self.assertTrue(event_payload["agent_route_bus_worker_event"]["recorded"])
        self.assertEqual(event_payload["agent_route_bus_worker_event"]["event"]["status"], "failed")
        self.assertEqual(event_payload["agent_route_bus_worker_event"]["event"]["trajectory_record"]["source"], "collaboration.worker_event")
        self.assertEqual(status_code, 200)
        status = status_payload["agent_route_bus_worker_status"]
        self.assertEqual(status["agents"][0]["latest_worker_event"]["message_id"], "agentmsg-command-gateway")
        self.assertEqual(status["recent_worker_events"][0]["context_id"], "gateway-thread")
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot["collaboration"]["agent_route_bus"]["worker_event_count"], 1)
        self.assertEqual(records[0]["metadata"]["source"], "collaboration.worker_event")
        self.assertEqual(records[0]["agent_id"], "claude_code")
        self.assertEqual(records[0]["overall_success"], False)
        self.assertEqual(records[0]["bottleneck_stage"], "collaboration_worker")

    def test_desktop_collaboration_run_participant_once_alias_emits_worker_trace(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                build_desktop_collaboration_update_response(
                    {
                        "action": "post_message",
                        "from_agent": "human_desktop",
                        "to_agents": ["codex"],
                        "role": "question",
                        "content": "@Codex dry run",
                        "thread_id": "participant-alias-thread",
                    }
                )
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "run_participant_once",
                        "agent": "codex",
                        "thread_id": "participant-alias-thread",
                        "dry_run": True,
                    }
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        self.assertEqual(payload["agent_route_bus_worker"]["status"], "processed")
        self.assertEqual(payload["participant_run"], payload["agent_route_bus_worker"])
        work_payloads = [event["payload"] for event in payload["work_events"]]
        self.assertEqual(len(work_payloads), 3)
        self.assertEqual(work_payloads[1]["detail"]["participant_id"], "codex")
        self.assertEqual(work_payloads[1]["detail"]["phase"], "execution")

    def test_desktop_collaboration_worker_stream_event_uses_output_as_trace_text(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            previous_trajectory = os.environ.get("SPIRITKIN_TRAJECTORY_LOG")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            trajectory_path = Path(tmp) / "evolution" / "trajectories.jsonl"
            os.environ["SPIRITKIN_TRAJECTORY_LOG"] = str(trajectory_path)
            try:
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "record_agent_route_bus_worker_event",
                        "agent": "codex",
                        "status": "stream",
                        "message_id": "agentmsg-stream",
                        "thread_id": "gateway-thread",
                        "task_id": "task-gateway",
                        "metadata": {"stream": "stdout", "output": "first token"},
                    }
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus
                if previous_trajectory is None:
                    os.environ.pop("SPIRITKIN_TRAJECTORY_LOG", None)
                else:
                    os.environ["SPIRITKIN_TRAJECTORY_LOG"] = previous_trajectory

        self.assertEqual(status, 200)
        work_payloads = [event["payload"] for event in payload["work_events"]]
        self.assertEqual(work_payloads[1]["status"], "running")
        self.assertEqual(work_payloads[1]["text"], "stdout: first token")
        self.assertEqual(work_payloads[1]["detail"]["worker_event"]["metadata"]["output"], "first token")
        self.assertEqual(work_payloads[1]["detail"]["participant_id"], "codex")
        self.assertEqual(work_payloads[1]["detail"]["stream"], "stdout")
        self.assertEqual(work_payloads[1]["detail"]["output"], "first token")
        self.assertFalse(trajectory_path.exists())

    def test_desktop_collaboration_external_cli_tool_event_preserves_real_call_identity(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "record_agent_route_bus_worker_event",
                        "agent": "codex",
                        "status": "stream",
                        "message_id": "agentmsg-cli-tool",
                        "thread_id": "gateway-thread",
                        "metadata": {
                            "stream": "command",
                            "output": "$ git status --short",
                            "lifecycle": "tool_running",
                            "tool_call_id": "cmd-live-1",
                            "target": "external_cli",
                            "operation": "command_execution",
                        },
                    }
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        detail = payload["work_events"][1]["payload"]["detail"]
        self.assertEqual(detail["phase"], "tool")
        self.assertEqual(detail["lifecycle"], "tool_running")
        self.assertEqual(detail["tool_call_id"], "cmd-live-1")
        self.assertEqual(detail["target"], "external_cli")
        self.assertEqual(detail["operation"], "command_execution")

    def test_desktop_collaboration_tool_call_trace_exposes_structured_detail(self):
        with TemporaryDirectory() as tmp:
            previous_root = os.environ.get("SPIRITKIN_COLLABORATION_ROOT")
            previous_bus = os.environ.get("SPIRITKIN_AGENT_ROUTE_BUS_ROOT")
            os.environ["SPIRITKIN_COLLABORATION_ROOT"] = str(Path(tmp) / "collaboration")
            os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = str(Path(tmp) / "agent_route_bus")
            try:
                status, payload = build_desktop_collaboration_update_response(
                    {
                        "action": "request_tool_call",
                        "agent": "codex",
                        "thread_id": "tool-thread",
                        "target": "local_pc",
                        "operation": "launch_app",
                        "params": {"app_name": "cmd"},
                        "requires_review": True,
                    }
                )
            finally:
                if previous_root is None:
                    os.environ.pop("SPIRITKIN_COLLABORATION_ROOT", None)
                else:
                    os.environ["SPIRITKIN_COLLABORATION_ROOT"] = previous_root
                if previous_bus is None:
                    os.environ.pop("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", None)
                else:
                    os.environ["SPIRITKIN_AGENT_ROUTE_BUS_ROOT"] = previous_bus

        self.assertEqual(status, 200)
        call = payload["agent_route_bus_tool_call"]["tool_call"]
        work_payloads = [event["payload"] for event in payload["work_events"]]
        detail = work_payloads[1]["detail"]
        self.assertEqual(detail["phase"], "tool")
        self.assertEqual(detail["lifecycle"], "permission_required")
        self.assertEqual(detail["tool_call_id"], call["tool_call_id"])
        self.assertEqual(detail["target"], "local_pc")
        self.assertEqual(detail["operation"], "launch_app")
        self.assertEqual(detail["worker_event"]["metadata"]["output"], "Tool request local_pc.launch_app is waiting for approval.")


if __name__ == "__main__":
    unittest.main()
