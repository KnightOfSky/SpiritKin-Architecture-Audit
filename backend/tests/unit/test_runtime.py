import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.agents.base import AgentReply
from backend.app.agent_management import save_agent_management_state
from backend.app.codex_work_events import TRACE_SCHEMA_VERSION, execution_command_preview, execution_output_preview
from backend.app.runtime import (
    AttachmentRef,
    InteractionInput,
    SpiritKinRuntime,
    build_remote_node_registry_from_settings,
)
from backend.app.runtime_state import aggregate_runtime_state
from backend.app.settings import RemoteWorkerNodeSetting
from backend.executors.base import ExecutionRequest
from backend.orchestrator.agent_cluster import AgentCluster
from backend.security import InMemoryAuditLog
from backend.services.feishu import FeishuSendResult


class RuntimeTests(unittest.TestCase):
    def assert_work_trace_v1(self, events):
        event_dicts = [event[1] if isinstance(event, tuple) else event for event in events]
        payloads = [event["payload"] for event in event_dicts]
        required_fields = {
            "trace_schema_version",
            "event_id",
            "run_id",
            "seq",
            "span_id",
            "parent_id",
            "agent_id",
            "status",
            "is_terminal",
            "detail",
        }
        self.assertTrue(payloads)
        self.assertEqual(len({payload["event_id"] for payload in payloads}), len(payloads))
        for payload in payloads:
            self.assertTrue(required_fields.issubset(payload.keys()))
            self.assertEqual(payload["trace_schema_version"], TRACE_SCHEMA_VERSION)
            self.assertTrue(payload["run_id"])
            self.assertTrue(payload["span_id"])
            self.assertIsInstance(payload["seq"], int)
            self.assertIsInstance(payload["detail"], dict)
            self.assertIn("phase", payload["detail"])

        grouped: dict[str, list[int]] = {}
        terminals: dict[str, list[dict]] = {}
        for payload in payloads:
            grouped.setdefault(payload["run_id"], []).append(payload["seq"])
            if payload["is_terminal"]:
                terminals.setdefault(payload["run_id"], []).append(payload)
        for run_id, seqs in grouped.items():
            self.assertEqual(seqs, sorted(seqs), run_id)
            self.assertEqual(seqs, list(range(1, len(seqs) + 1)), run_id)
            self.assertTrue(terminals.get(run_id), run_id)
            self.assertTrue(all(payload["status"] in {"completed", "failed", "skipped"} for payload in terminals[run_id]))

    def test_runtime_starts_and_stops_remote_heartbeat_poller_for_registered_nodes(self):
        class FakeRegistry:
            def list_nodes(self):
                return [object()]

        registry = FakeRegistry()

        with patch("backend.app.runtime.RemoteHeartbeatPoller") as poller_cls:
            runtime = SpiritKinRuntime(
                agent=object(),
                node_registry=registry,
                remote_heartbeat_interval_seconds=12.0,
                remote_heartbeat_ttl_seconds=48.0,
            )

        poller_cls.assert_called_once_with(registry, interval_seconds=12.0, ttl_seconds=48.0)
        poller_cls.return_value.start.assert_called_once_with()
        runtime.close()
        poller_cls.return_value.stop.assert_called_once_with()

    def test_runtime_builds_remote_node_registry_from_settings(self):
        setting = RemoteWorkerNodeSetting(
            node_id="office-pc",
            base_url="http://100.64.0.8:8790",
            auth_token="secret",
            aliases={"公司电脑"},
            metadata={"configured_from": "env"},
            timeout_seconds=2.0,
        )

        with patch("backend.app.runtime.resolve_remote_worker_nodes", return_value=[setting]):
            registry = build_remote_node_registry_from_settings(config_path="config/config.yaml")

        node = registry.get("office-pc")
        self.assertEqual(node.node_id, "office-pc")
        self.assertIn("公司电脑", node.aliases)
        self.assertEqual(node.metadata["base_url"], "http://100.64.0.8:8790")
        self.assertEqual(node.client.base_url, "http://100.64.0.8:8790")
        self.assertEqual(node.client.auth_token, "secret")

    def test_runtime_auto_uses_configured_remote_worker_registry(self):
        setting = RemoteWorkerNodeSetting(node_id="office-pc", base_url="http://100.64.0.8:8790")
        fake_agent = object()
        fake_memory = object()

        with patch("backend.app.runtime.resolve_remote_worker_nodes", return_value=[setting]), patch("backend.app.runtime.RemoteHeartbeatPoller") as poller_cls, patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
            runtime = SpiritKinRuntime(knowledge_backend="keyword")

        wiring = cluster_cls.call_args.kwargs["wiring"]
        passed_registry = wiring.node_registry
        self.assertEqual(type(wiring.app_port).__name__, "DefaultAgentClusterAppPort")
        self.assertEqual(passed_registry.get("office-pc").metadata["base_url"], "http://100.64.0.8:8790")
        poller_cls.return_value.start.assert_called_once_with()
        runtime.close()

    def test_runtime_injects_desktop_route_profile_into_llm_client(self):
        with TemporaryDirectory() as tmp:
            previous_state = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                save_agent_management_state(
                    {
                        "active_route_profile_id": "route-a",
                        "route_profiles": [
                            {
                                "profile_id": "route-a",
                                "label": "Desktop Route",
                                "members": [
                                    {
                                        "member_id": "main",
                                        "role": "primary_text",
                                        "provider": "openai_compatible",
                                        "model": "desktop-model",
                                        "weight": 1.0,
                                        "enabled": True,
                                    }
                                ],
                            }
                        ],
                        "agents": [
                            {
                                "agent_id": "programming",
                                "label": "编程 Agent",
                                "domain": "programming",
                                "enabled": False,
                                "priority": 5,
                            }
                        ],
                    }
                )
                calls = []

                def fake_llm(prompt, **kwargs):
                    calls.append(kwargs)
                    return "ok"

                fake_agent = object()
                with patch("backend.services.conversation_engine.get_llm_response", side_effect=fake_llm), patch("backend.app.runtime.resolve_remote_worker_nodes", return_value=[]), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
                    runtime = SpiritKinRuntime(knowledge_backend="keyword")

                llm_client = cluster_cls.call_args.kwargs["llm_client"]
                self.assertEqual(cluster_cls.call_args.kwargs["wiring"].managed_agents["disabled_agent_ids"], ["programming"])
                self.assertEqual(llm_client("hello"), "ok")
                self.assertEqual(calls[-1]["provider"], "openai_compatible")
                self.assertEqual(calls[-1]["model_name"], "desktop-model")
                self.assertEqual(runtime.build_capabilities_payload()["tooling"]["active_route_profile_id"], "route-a")
            finally:
                if previous_state is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_state

    def test_runtime_records_mobile_command_and_execution_audit(self):
        class FakeAgent:
            def process(self, text, visual_context="", channel="text", input_metadata=None):
                return AgentReply(
                    text="远端执行完成",
                    agent_name="remote_agent",
                    metadata={
                        "execution": {
                            "target": "remote:office-pc",
                            "operation": "status",
                            "success": True,
                            "metadata": {"node_id": "office-pc"},
                        }
                    },
                )

        audit_log = InMemoryAuditLog()
        runtime = SpiritKinRuntime(agent=FakeAgent(), audit_log=audit_log)

        runtime.handle_input(InteractionInput(text="查看远端电脑状态", channel="mobile", metadata={"client_id": "phone"}))

        audit = runtime.build_capabilities_payload()["audit"]
        self.assertEqual(audit["total"], 2)
        self.assertEqual(audit["mobile_count"], 2)
        self.assertEqual(audit["remote_count"], 1)
        self.assertEqual(audit["recent"][0]["event_type"], "command_received")
        self.assertEqual(audit["recent"][1]["event_type"], "execution_result")

    def test_runtime_records_confirmation_audit(self):
        class FakeAgent:
            def process(self, text, visual_context=""):
                return AgentReply(
                    text="高风险操作需要确认",
                    requires_confirmation=True,
                    agent_name="execution_guard",
                    metadata={
                        "response_kind": "confirmation_request",
                        "pending_target": "file",
                        "pending_operation": "file_write",
                        "risk_level": "high",
                    },
                )

        runtime = SpiritKinRuntime(agent=FakeAgent(), audit_log=InMemoryAuditLog())

        runtime.handle_input(InteractionInput(text="写入文件", channel="desktop"))

        audit = runtime.build_capabilities_payload()["audit"]
        self.assertEqual(audit["high_risk_count"], 1)
        self.assertEqual(audit["recent"][-1]["event_type"], "confirmation_requested")
        self.assertEqual(audit["recent"][-1]["target"], "file")

    def test_runtime_completes_voice_feishu_dry_run_confirmation_loop(self):
        class FakeFeishuClient:
            def __init__(self):
                self.calls = []

            def send_text_message(self, recipient: str, text: str):
                self.calls.append((recipient, text))
                return FeishuSendResult(True, recipient, f"user_id:{recipient}", "user_id", text, message_id="runtime-dry-run")

        feishu_client = FakeFeishuClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("飞书闭环不应退回 LLM"),
            feishu_client=feishu_client,
            voice_intent_mode="fallback",
        )
        emitted_events = []

        def fake_dispatch(url, event):
            emitted_events.append(event)
            return True

        with patch("backend.app.runtime.dispatch_runtime_event", side_effect=fake_dispatch):
            runtime = SpiritKinRuntime(
                agent=cluster,
                emit_runtime_events=True,
                event_sink_url="ws://example.test/events",
            )
            confirmation = runtime.handle_voice_input("给张三发飞书，说会议改到三点", visual_context="用户正看着聊天窗口")
            execution = runtime.handle_voice_input("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_target"], "feishu")
        self.assertEqual(confirmation.metadata["pending_operation"], "send_message")
        self.assertEqual(execution.agent_name, "executor_feishu")
        self.assertEqual(feishu_client.calls, [("张三", "会议改到三点")])
        self.assertIn("dry-run", execution.text)
        self.assertEqual(execution.metadata["execution"]["data"]["dry_run"], True)
        self.assertEqual(execution.metadata["execution"]["operation"], "send_message")
        work_events = [event for event in emitted_events if event["type"] == "assistant.work_updated"]
        main_events = [event for event in emitted_events if event["type"] not in {"assistant.work_updated", "speech.phoneme", "model.interaction", "runtime.aggregated_state", "presence.updated", "memory.updated", "personality.updated"}]
        self.assertTrue(work_events)
        self.assert_work_trace_v1(work_events)
        self.assertEqual([event["type"] for event in main_events], [
            "user_input",
            "assistant.message",
            "assistant.confirmation_requested",
            "avatar.state",
            "user_input",
            "assistant.message",
            "assistant.execution_updated",
            "avatar.state",
        ])
        self.assertEqual(main_events[0]["channel"], "voice")
        self.assertEqual(main_events[0]["visual_context"], "用户正看着聊天窗口")
        self.assertEqual(main_events[2]["payload"]["pending_target"], "feishu")
        self.assertEqual(main_events[6]["payload"]["data"]["recipient"], "张三")
        self.assertTrue(main_events[7]["payload"]["speaking"])
        self.assertTrue(any(event["type"] == "model.interaction" for event in emitted_events))
        self.assertTrue(any(event["type"] == "speech.phoneme" for event in emitted_events))
        self.assertTrue(any(event["type"] == "presence.updated" for event in emitted_events))

    def test_runtime_marks_model_call_only_around_actual_llm_invocation(self):
        emitted_events = []
        cluster = AgentCluster(
            llm_client=lambda _prompt, **_kwargs: "<emotion:happy><action:nod>你好，我在。",
        )

        with patch("backend.app.runtime.dispatch_runtime_event", side_effect=lambda _url, event: emitted_events.append(event) or True):
            runtime = SpiritKinRuntime(
                agent=cluster,
                emit_runtime_events=True,
                event_sink_url="ws://example.test/events",
            )
            reply = runtime.handle_text_input("你好")

        self.assertEqual(reply.text, "你好，我在。")
        work_payloads = [event["payload"] for event in emitted_events if event["type"] == "assistant.work_updated"]
        model_events = [payload for payload in work_payloads if isinstance(payload["detail"].get("model_call"), dict)]
        reasoning_events = [payload for payload in work_payloads if isinstance(payload["detail"].get("model_reasoning"), dict)]
        self.assertEqual([payload["status"] for payload in model_events], ["started", "completed"])
        self.assertTrue(all(":model:" in payload["span_id"] for payload in model_events))
        self.assertTrue(all(payload["detail"]["model_call"]["external"] is False for payload in model_events))
        self.assertTrue(all(payload["detail"]["model_call"]["target_agent_id"] == "main_text" for payload in model_events))
        self.assertTrue(all(payload["detail"]["model_call"]["target_label"] == "Spirit" for payload in model_events))
        self.assertTrue(all(payload["kind"] == "call" for payload in model_events))
        self.assertEqual([payload["text"] for payload in model_events], ["正在调用 Spirit。", "Spirit 调用完成。"])
        self.assertEqual(len(reasoning_events), 1)
        self.assertFalse(reasoning_events[0]["detail"]["model_reasoning"]["available"])
        self.assertIn("没有返回独立推理流", reasoning_events[0]["text"])

    def test_runtime_projects_model_reasoning_between_call_lifecycle_events(self):
        emitted_events = []

        def llm_client(_prompt, **_kwargs):
            from backend.services.conversation_engine import _LLM_REASONING_STREAM_LISTENER

            listener = _LLM_REASONING_STREAM_LISTENER.get()
            self.assertTrue(callable(listener))
            listener("分析", "分析")
            listener("用户请求", "分析用户请求")
            return "<emotion:happy>我在。"

        cluster = AgentCluster(llm_client=llm_client)
        with patch("backend.app.runtime.dispatch_runtime_event", side_effect=lambda _url, event: emitted_events.append(event) or True):
            runtime = SpiritKinRuntime(
                agent=cluster,
                emit_runtime_events=True,
                event_sink_url="ws://example.test/events",
            )
            runtime.handle_text_input("你好")

        payloads = [event["payload"] for event in emitted_events if event["type"] == "assistant.work_updated"]
        model_events = [payload for payload in payloads if isinstance(payload["detail"].get("model_call"), dict)]
        reasoning_events = [payload for payload in payloads if isinstance(payload["detail"].get("model_reasoning"), dict)]

        self.assertEqual(len(reasoning_events), 1)
        self.assertEqual(reasoning_events[0]["text"], "分析用户请求")
        self.assertEqual(reasoning_events[0]["status"], "completed")
        self.assertEqual(reasoning_events[0]["parent_id"], model_events[0]["span_id"])
        self.assertLess(model_events[0]["seq"], reasoning_events[0]["seq"])
        self.assertLess(reasoning_events[0]["seq"], model_events[-1]["seq"])

    def test_runtime_buffers_visible_reply_until_reasoning_and_model_call_complete(self):
        emitted_events = []

        def llm_client(_prompt, **_kwargs):
            from backend.services.conversation_engine import _LLM_REASONING_STREAM_LISTENER, _LLM_STREAM_LISTENER

            reasoning_listener = _LLM_REASONING_STREAM_LISTENER.get()
            reply_listener = _LLM_STREAM_LISTENER.get()
            self.assertTrue(callable(reasoning_listener))
            self.assertTrue(callable(reply_listener))

            reasoning_listener("先分析", "先分析")
            reply_listener("回复开头", "回复开头")
            self.assertFalse(any(event["type"] == "assistant.delta" for event in emitted_events))
            reasoning_listener("完毕", "先分析完毕")
            self.assertFalse(any(event["type"] == "assistant.delta" for event in emitted_events))
            reply_listener("，正文完成。", "回复开头，正文完成。<emotion:happy>")
            return "回复开头，正文完成。<emotion:happy>"

        cluster = AgentCluster(llm_client=llm_client)
        with patch("backend.app.runtime.dispatch_runtime_event", side_effect=lambda _url, event: emitted_events.append(event) or True):
            runtime = SpiritKinRuntime(
                agent=cluster,
                emit_runtime_events=True,
                event_sink_url="ws://example.test/events",
            )
            reply = runtime.handle_text_input("请先想完再回复")

        self.assertEqual(reply.text, "回复开头，正文完成。")
        delta_events = [event for event in emitted_events if event["type"] == "assistant.delta"]
        self.assertEqual(len(delta_events), 1)
        self.assertEqual(delta_events[0]["payload"]["text"], "回复开头，正文完成。")
        self.assertEqual(delta_events[0]["payload"]["status"], "completed")
        self.assertTrue(delta_events[0]["payload"]["is_final"])

        model_completed_index = max(
            index
            for index, event in enumerate(emitted_events)
            if event["type"] == "assistant.work_updated"
            and isinstance(event["payload"].get("detail", {}).get("model_call"), dict)
            and event["payload"].get("status") == "completed"
        )
        reasoning_completed_index = max(
            index
            for index, event in enumerate(emitted_events)
            if event["type"] == "assistant.work_updated"
            and isinstance(event["payload"].get("detail", {}).get("model_reasoning"), dict)
            and event["payload"].get("status") == "completed"
        )
        delta_index = emitted_events.index(delta_events[0])
        message_index = next(index for index, event in enumerate(emitted_events) if event["type"] == "assistant.message")
        self.assertLess(reasoning_completed_index, delta_index)
        self.assertLess(model_completed_index, delta_index)
        self.assertLess(delta_index, message_index)

    def test_execution_command_preview_reads_execution_request_params(self):
        request = ExecutionRequest(
            target="local_pc",
            operation="launch_app",
            params={"app_name": "cmd"},
        )

        self.assertEqual(execution_command_preview(request), "cmd")

    def test_execution_command_preview_reads_nested_result_data(self):
        execution = {
            "target": "local_pc",
            "operation": "launch_app",
            "data": {"app_name": "cmd", "resolved_app": "cmd.exe"},
        }

        self.assertEqual(execution_command_preview(None, execution), "cmd")

    def test_execution_command_preview_formats_real_argv(self):
        execution = {
            "target": "git",
            "operation": "git.status",
            "data": {"command": ["git", "-C", "D:\\SpiritKinAI", "status", "--short"]},
        }

        preview = execution_command_preview(None, execution)

        self.assertIn("git -C", preview)
        self.assertIn("status --short", preview)

    def test_execution_output_preview_prefers_process_stdout(self):
        execution = {
            "output": "friendly summary",
            "data": {"stdout": "M desktop/App.xaml\n", "stderr": ""},
        }

        self.assertEqual(execution_output_preview(execution, "fallback"), "M desktop/App.xaml")

    def test_runtime_passes_knowledge_backend_to_agent_cluster(self):
        fake_agent = object()
        fake_memory = object()

        with patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
            runtime = SpiritKinRuntime(knowledge_backend="embedding")

        self.assertIs(runtime.agent, fake_agent)
        cluster_cls.assert_called_once()
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].knowledge_backend, "embedding")

    def test_runtime_passes_openclaw_state_path_to_agent_cluster(self):
        fake_agent = object()
        fake_memory = object()

        with patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
            runtime = SpiritKinRuntime(knowledge_backend="keyword", openclaw_state_path="data/openclaw/state.json")

        self.assertIs(runtime.agent, fake_agent)
        self.assertEqual(runtime.openclaw_state_path, "data/openclaw/state.json")
        cluster_cls.assert_called_once()
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].knowledge_backend, "keyword")
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].openclaw_state_path, "data/openclaw/state.json")

    def test_runtime_voice_ack_env_supports_enabled_aliases(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(SpiritKinRuntime._voice_ack_enabled())
            self.assertFalse(SpiritKinRuntime._wake_ack_enabled())
            self.assertFalse(SpiritKinRuntime._startup_voice_prompt_enabled())

        with patch.dict("os.environ", {"SPIRITKIN_VOICE_ACK_ENABLED": "0", "SPIRITKIN_WAKE_ACK_ENABLED": "false"}, clear=True):
            self.assertFalse(SpiritKinRuntime._voice_ack_enabled())
            self.assertFalse(SpiritKinRuntime._wake_ack_enabled())

        with patch.dict("os.environ", {"SPIRITKIN_VOICE_ACK_ENABLED": "1", "SPIRITKIN_WAKE_ACK_ENABLED": "true", "SPIRITKIN_STARTUP_VOICE_PROMPT": "1"}, clear=True):
            self.assertTrue(SpiritKinRuntime._voice_ack_enabled())
            self.assertTrue(SpiritKinRuntime._wake_ack_enabled())
            self.assertTrue(SpiritKinRuntime._startup_voice_prompt_enabled())

    def test_runtime_detects_recent_playback_echo_from_voice_ack(self):
        runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")

        runtime._record_spoken_output("我听到：打开默认浏览器")

        self.assertTrue(runtime._is_probable_playback_echo("我听到打开默认浏览器"))
        self.assertTrue(runtime._is_probable_playback_echo("打开默认浏览器"))

    def test_runtime_does_not_suppress_confirmation_controls_as_echo(self):
        runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")

        runtime._record_spoken_output("这个操作需要确认，确认就说确认执行，取消就说取消执行。")

        self.assertFalse(runtime._is_probable_playback_echo("确认执行"))
        self.assertFalse(runtime._is_probable_playback_echo("取消执行"))

    def test_runtime_hotword_defaults_are_short_for_fast_wake_detection(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(SpiritKinRuntime._hotword_timeout_seconds(), 0.8)
            self.assertEqual(SpiritKinRuntime._hotword_phrase_time_limit_seconds(), 1.0)

        with patch.dict("os.environ", {"SPIRITKIN_HOTWORD_TIMEOUT": "0.1", "SPIRITKIN_HOTWORD_PHRASE_TIME_LIMIT": "0.1"}, clear=True):
            self.assertEqual(SpiritKinRuntime._hotword_timeout_seconds(), 0.2)
            self.assertEqual(SpiritKinRuntime._hotword_phrase_time_limit_seconds(), 0.35)

        with patch.dict("os.environ", {"SPIRITKIN_VOICE_ACK": "0", "SPIRITKIN_WAKE_ACK": "0"}, clear=True):
            self.assertFalse(SpiritKinRuntime._voice_ack_enabled())
            self.assertFalse(SpiritKinRuntime._wake_ack_enabled())

    def test_runtime_passes_node_registry_to_agent_cluster(self):
        fake_agent = object()
        node_registry = object()
        fake_memory = object()

        with patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
            runtime = SpiritKinRuntime(knowledge_backend="keyword", node_registry=node_registry)

        self.assertIs(runtime.agent, fake_agent)
        self.assertIs(runtime.node_registry, node_registry)
        cluster_cls.assert_called_once()
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].knowledge_backend, "keyword")
        self.assertIs(cluster_cls.call_args.kwargs["wiring"].node_registry, node_registry)

    def test_runtime_reads_knowledge_backend_from_env(self):
        fake_agent = object()
        fake_memory = object()

        with patch.dict("os.environ", {"SPIRIT_KNOWLEDGE_BACKEND": "embedding"}, clear=False):
            with patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
                runtime = SpiritKinRuntime()

        self.assertIs(runtime.agent, fake_agent)
        self.assertEqual(runtime.knowledge_backend, "embedding")
        cluster_cls.assert_called_once()
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].knowledge_backend, "embedding")

    def test_runtime_builds_event_sink_url_from_events_host_and_port_env(self):
        fake_agent = object()

        with patch.dict(
            "os.environ",
            {"SPIRITKIN_EVENTS_HOST": "127.0.0.1", "SPIRITKIN_EVENTS_PORT": "9999"},
            clear=False,
        ):
            with patch("backend.app.runtime.AgentCluster", return_value=fake_agent):
                runtime = SpiritKinRuntime()

        self.assertEqual(runtime.event_sink_url, "ws://127.0.0.1:9999")

    def test_runtime_prefers_explicit_events_ws_url_over_legacy_live2d_url(self):
        fake_agent = object()

        with patch.dict(
            "os.environ",
            {
                "SPIRITKIN_EVENTS_WS_URL": "ws://127.0.0.1:8765",
                "SPIRITKIN_LIVE2D_WS_URL": "ws://localhost:9999",
            },
            clear=False,
        ):
            with patch("backend.app.runtime.AgentCluster", return_value=fake_agent):
                runtime = SpiritKinRuntime()

        self.assertEqual(runtime.event_sink_url, "ws://127.0.0.1:8765")

    def test_runtime_prefers_explicit_hotword_and_backend_over_config(self):
        fake_agent = object()
        fake_memory = object()

        with patch("backend.app.runtime.build_workflow_memory", return_value=fake_memory), patch("backend.app.runtime.AgentCluster", return_value=fake_agent) as cluster_cls:
            runtime = SpiritKinRuntime(hotword="Kin", knowledge_backend="keyword", config_path="custom.yaml")

        self.assertEqual(runtime.hotword, "Kin")
        self.assertEqual(runtime.knowledge_backend, "keyword")
        cluster_cls.assert_called_once()
        self.assertEqual(cluster_cls.call_args.kwargs["wiring"].knowledge_backend, "keyword")

    def test_runtime_handle_input_routes_text_and_voice_through_same_agent(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context=""):
                self.calls.append((text, visual_context))
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()

        with patch("backend.app.runtime.AgentCluster", return_value=fake_agent):
            runtime = SpiritKinRuntime()

        text_reply = runtime.handle_input(InteractionInput(text="文本输入", channel="text", visual_context="桌面"))
        voice_reply = runtime.handle_input(InteractionInput(text="语音输入", channel="voice", visual_context="麦克风"))

        self.assertEqual(fake_agent.calls, [("文本输入", "桌面"), ("语音输入", "麦克风")])
        self.assertEqual(text_reply.metadata["input_channel"], "text")
        self.assertEqual(voice_reply.metadata["input_channel"], "voice")
        self.assertEqual(voice_reply.spoken_text, "收到：语音输入")

    def test_runtime_passes_channel_and_metadata_to_agent_when_supported(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context="", channel="text", input_metadata=None):
                self.calls.append((text, visual_context, channel, input_metadata))
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent)

        runtime.handle_input(
            InteractionInput(
                text="  机械B现在怎么装它  ",
                channel="voice",
                visual_context="麦克风",
                metadata={"asr_elapsed": 1.23},
            )
        )

        self.assertEqual(len(fake_agent.calls), 1)
        text, visual_context, channel, metadata = fake_agent.calls[0]
        self.assertEqual((text, visual_context, channel), ("机械臂现在怎么装它", "麦克风", "voice"))
        self.assertEqual(metadata["asr_elapsed"], 1.23)
        self.assertEqual(metadata["raw_voice_text"], "机械B现在怎么装它")
        self.assertEqual(metadata["asr_original_text"], "机械B现在怎么装它")
        self.assertEqual(metadata["asr_corrected_text"], "机械臂现在怎么装它")
        self.assertIn("current_time", metadata)

    def test_runtime_injects_current_time_metadata_for_llm_context(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context="", channel="text", input_metadata=None):
                self.calls.append(input_metadata or {})
                return AgentReply(text="ok")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent)

        runtime.handle_input(InteractionInput(text="现在几点", channel="desktop"))

        current = fake_agent.calls[0]["current_time"]
        self.assertIn("date", current)
        self.assertIn("time", current)
        self.assertIn("iso", current)

    def test_runtime_passes_asr_metrics_to_voice_agent(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context="", channel="text", input_metadata=None):
                self.calls.append((text, channel, input_metadata))
                return AgentReply(text="ok", spoken_text="ok")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent, hotword="Spirit")

        runtime.handle_voice_input(
            "打开默认游览器",
            metadata={"asr_metrics": {"rejected_segments": 1, "segments": [{"text": "打开默认游览器"}]}},
        )

        metadata = fake_agent.calls[0][2]
        self.assertEqual(fake_agent.calls[0][0], "打开默认浏览器")
        self.assertEqual(metadata["asr_metrics"]["rejected_segments"], 1)
        self.assertEqual(metadata["asr_corrected_text"], "打开默认浏览器")

    def test_runtime_corrects_common_asr_noise_before_agent_and_events(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context="", channel="text", input_metadata=None):
                self.calls.append((text, input_metadata))
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent, hotword="Spirit")

        reply = runtime.handle_voice_input("SPIRIT s现在能听到我所谓。。")

        self.assertEqual(fake_agent.calls[0][0], "现在能听到我说话。")
        self.assertEqual(fake_agent.calls[0][1]["raw_voice_text"], "SPIRIT s现在能听到我所谓。。")
        self.assertEqual(fake_agent.calls[0][1]["asr_corrected_text"], "现在能听到我说话。")
        self.assertEqual(reply.metadata["client_metadata"]["asr_corrected_text"], "现在能听到我说话。")

    def test_runtime_builds_shorter_voice_summary_for_long_reply(self):
        class FakeAgent:
            def process(self, text, visual_context=""):
                return AgentReply(text="第一句先说明情况。第二句展开详细步骤，方便在文本里查看。")

        with patch("backend.app.runtime.AgentCluster", return_value=FakeAgent()):
            runtime = SpiritKinRuntime()

        reply = runtime.handle_voice_input("帮我总结一下")

        self.assertEqual(reply.spoken_text, "第一句先说明情况。")

    def test_runtime_can_build_frontend_payload(self):
        reply = AgentReply(
            text="已生成飞书开发计划",
            spoken_text="我已经生成飞书开发计划。",
            emotion="thinking",
            action="plan_development",
            agent_name="development_planner",
            metadata={
                "response_kind": "development_plan",
                "development_plan": {"target_integrations": ["feishu"]},
            },
        )

        payload = SpiritKinRuntime.build_output_payload(reply)

        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["response_kind"], "development_plan")
        self.assertEqual(payload["presentation"]["primary"], "card")
        self.assertEqual(payload["spoken_text"], "我已经生成飞书开发计划。")
        self.assertEqual(payload["live2d"]["message"], "我已经生成飞书开发计划。")
        self.assertEqual(payload["model_interaction"]["protocol"], "spiritkin.model_interaction.v1")
        self.assertEqual(payload["model_interaction"]["action"], "nod")
        self.assertEqual(payload["data"]["development_plan"]["target_integrations"], ["feishu"])

    def test_runtime_can_build_input_payload_with_attachments(self):
        interaction = InteractionInput(
            text="请分析我上传的需求文档",
            channel="mobile",
            attachments=(
                AttachmentRef(
                    file_id="file_001",
                    name="需求说明.pdf",
                    mime_type="application/pdf",
                    uri="upload://file_001",
                    size_bytes=2048,
                ),
            ),
            metadata={"session_id": "sess-1"},
        )

        payload = SpiritKinRuntime.build_input_payload(interaction)

        self.assertEqual(payload["schema_version"], "v1")
        self.assertEqual(payload["channel"], "mobile")
        self.assertEqual(payload["attachments"][0]["file_id"], "file_001")
        self.assertEqual(payload["attachments"][0]["mime_type"], "application/pdf")

    def test_runtime_can_build_confirmation_events_for_frontends(self):
        reply = AgentReply(
            text="这个操作会控制 openclaw 执行 home。为安全起见，请先回复“确认执行”或“取消执行”。",
            spoken_text="这个动作需要你确认。确认就说确认执行，取消就说取消执行。",
            emotion="confused",
            action="await_confirmation",
            agent_name="execution_guard",
            requires_confirmation=True,
            metadata={
                "response_kind": "confirmation_request",
                "pending_target": "openclaw",
                "pending_operation": "home",
                "risk_level": "high",
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)

        self.assertEqual(events[0]["type"], "assistant.message")
        self.assertEqual(events[1]["type"], "assistant.confirmation_requested")
        self.assertEqual(events[1]["payload"]["pending_target"], "openclaw")
        self.assertEqual(events[2]["type"], "avatar.state")
        self.assertEqual(events[2]["payload"]["response_kind"], "confirmation_request")
        self.assertEqual(events[3]["type"], "model.interaction")
        self.assertEqual(events[3]["payload"]["phase"], "waiting_confirmation")

    def test_runtime_builds_speech_phoneme_events_for_avatar_shell(self):
        events = SpiritKinRuntime.build_speech_phoneme_events("你好 Spirit", max_events=4)

        self.assertGreater(len(events), 0)
        self.assertLessEqual(len(events), 4)
        self.assertEqual(events[0]["type"], "speech.phoneme")
        self.assertIn("mouth_shape", events[0]["payload"])
        self.assertEqual(events[0]["payload"]["source"], "text_timeline")

    def test_runtime_builds_lpm_state_events_for_avatar_shell(self):
        runtime = SpiritKinRuntime(agent=object())

        events = runtime.build_lpm_state_events()

        self.assertEqual(events[0]["type"], "presence.updated")

    def test_runtime_emits_task_update_event_for_task_metadata(self):
        reply = AgentReply(
            text="任务已加入队列。",
            emotion="thinking",
            action="queue_task",
            agent_name="scheduler",
            metadata={
                "response_kind": "scheduler_busy",
                "task": {
                    "task_id": "task_demo",
                    "status": "queued",
                    "domain": "video_animation",
                    "current_stage": "intake",
                    "stages": [{"name": "intake", "status": "pending", "detail": ""}],
                },
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)

        self.assertEqual(events[0]["type"], "assistant.message")
        self.assertEqual(events[1]["type"], "assistant.task_updated")
        self.assertEqual(events[1]["payload"]["task_id"], "task_demo")
        self.assertEqual(events[2]["type"], "avatar.state")
        self.assertTrue(events[0]["payload"]["presentation"]["show_task_status"])
        self.assertEqual(events[3]["type"], "model.interaction")

    def test_runtime_emits_project_update_event_for_project_metadata(self):
        reply = AgentReply(
            text="电商项目已推进到 listing 阶段",
            emotion="thinking",
            action="write_plan",
            agent_name="ecommerce",
            metadata={
                "project": {
                    "project_id": "ecom_demo",
                    "project_type": "store_launch",
                    "current_phase": "listing",
                    "status": "active",
                }
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)

        self.assertEqual(events[0]["type"], "assistant.message")
        self.assertEqual(events[1]["type"], "assistant.project_updated")
        self.assertEqual(events[1]["payload"]["project_id"], "ecom_demo")
        self.assertEqual(events[2]["type"], "avatar.state")
        self.assertTrue(events[0]["payload"]["presentation"]["show_project_status"])
        self.assertEqual(events[3]["type"], "model.interaction")

    def test_runtime_emits_execution_and_openclaw_state_events(self):
        reply = AgentReply(
            text="OpenClaw 当前状态：idle，位置 (1.0, 2.0, 3.0)，夹爪打开。",
            emotion="happy",
            action="execute_task",
            agent_name="executor_openclaw",
            metadata={
                "response_kind": "execution_result",
                "execution": {
                    "target": "openclaw",
                    "operation": "status",
                    "data": {
                        "state": "idle",
                        "position": {"x": 1.0, "y": 2.0, "z": 3.0},
                        "gripper_opened": True,
                        "last_command": "move_to",
                        "transport": "in_memory",
                    },
                    "metadata": {"target": "openclaw", "operation": "status"},
                },
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)

        self.assertEqual(events[0]["type"], "assistant.message")
        self.assertEqual(events[1]["type"], "assistant.execution_updated")
        self.assertEqual(events[1]["payload"]["target"], "openclaw")
        self.assertEqual(events[2]["type"], "device.openclaw_state_updated")
        self.assertEqual(events[2]["payload"]["state"]["position"]["x"], 1.0)
        self.assertEqual(events[3]["type"], "avatar.state")
        self.assertEqual(events[4]["type"], "model.interaction")

    def test_runtime_emits_aggregated_state_event_for_avatar_narrator(self):
        reply = AgentReply(
            text="这个操作需要确认。",
            spoken_text="请确认是否执行。",
            emotion="confused",
            action="await_confirmation",
            agent_name="execution_guard",
            requires_confirmation=True,
            metadata={
                "response_kind": "confirmation_request",
                "pending_target": "desktop",
                "pending_operation": "open_app",
                "request_id": "req-runtime-state",
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)
        aggregate_event = next(event for event in events if event["type"] == "runtime.aggregated_state")

        self.assertEqual(aggregate_event["payload"]["schema_version"], "spiritkin.aggregated_runtime_state.v1")
        self.assertEqual(aggregate_event["payload"]["state"], "need_user")
        self.assertEqual(aggregate_event["payload"]["emotion"], "waiting")
        self.assertEqual(aggregate_event["payload"]["action"], "await_confirmation")
        self.assertIn("需要你确认", aggregate_event["payload"]["speech_hint"])
        self.assertGreaterEqual(aggregate_event["payload"]["task_count"], 1)

    def test_aggregated_runtime_state_prioritizes_execution_failure(self):
        state = aggregate_runtime_state(
            [
                {
                    "type": "assistant.task_updated",
                    "schema_version": "v1",
                    "payload": {"task_id": "task-1", "title": "导入素材", "status": "running", "progress": 0.5},
                },
                {
                    "type": "assistant.execution_updated",
                    "schema_version": "v1",
                    "payload": {"target": "workflow", "operation": "import_assets", "success": False, "request_id": "task-1"},
                },
            ],
            now=1234.0,
        )

        snapshot = state.snapshot()
        self.assertEqual(snapshot["state"], "error")
        self.assertEqual(snapshot["overall_progress"], 100.0)
        self.assertIn("遇到错误", snapshot["speech_hint"])
        self.assertEqual(snapshot["tasks"][0]["status"], "error")

    def test_runtime_emits_openclaw_state_event_for_remote_execution_result(self):
        reply = AgentReply(
            text="远端机械臂状态已更新",
            emotion="happy",
            action="execute_task",
            agent_name="executor_remote",
            metadata={
                "response_kind": "execution_result",
                "execution": {
                    "target": "remote:lab-arm",
                    "operation": "status",
                    "data": {
                        "position": {"x": 4.0, "y": 5.0, "z": 6.0},
                        "gripper_opened": True,
                    },
                    "metadata": {
                        "node_id": "lab-arm",
                        "remote_target": "openclaw",
                    },
                },
            },
        )

        events = SpiritKinRuntime.build_response_events(reply)

        self.assertEqual(events[1]["type"], "assistant.execution_updated")
        self.assertEqual(events[1]["payload"]["target"], "remote:lab-arm")
        self.assertEqual(events[2]["type"], "device.openclaw_state_updated")
        self.assertEqual(events[2]["payload"]["target"], "openclaw")
        self.assertEqual(events[2]["payload"]["metadata"]["node_id"], "lab-arm")
        self.assertEqual(events[2]["payload"]["state"]["position"]["z"], 6.0)

    def test_runtime_can_describe_frontend_capabilities(self):
        class FakeCapabilitiesAgent:
            available_tools = [type("Tool", (), {"name": "file.read", "target": "file", "operation": "file_read", "risk_level": "high", "read_only": True})()]
            available_skills = [type("Skill", (), {"name": "scan_then_open", "description": "扫描后打开应用"})()]
            recent_inventory = {"software": [{"name": "火豹浏览器"}], "hardware": [{"FriendlyName": "Integrated Camera"}], "devices": {"local_pc": {"label": "local_pc", "software": [{"name": "火豹浏览器"}], "hardware": [{"FriendlyName": "Integrated Camera"}]}}}
            workflow_memory_snapshot = [{"workflow_id": "wf-000001", "operation": "list_installed_apps", "target": "local_pc"}]
            pending_execution = type("Pending", (), {"request": type("Req", (), {"target": "file", "operation": "file_read"})(), "risk_level": "high"})()

        class FakeNodeRegistry:
            def list_nodes(self):
                return []

            def snapshot(self):
                return {
                    "total": 2,
                    "status_counts": {"online": 1, "offline": 1},
                    "nodes": [
                        {"node_id": "office-pc", "status": "online", "targets": ["desktop"]},
                        {"node_id": "lab-arm", "status": "offline", "targets": ["openclaw"]},
                    ],
                }

        with patch("backend.app.runtime.describe_model_capabilities", return_value={
            "text": {
                "provider": "local_transformers",
                "model": "Qwen/Qwen3.5-9B",
                "default_mode": "balanced",
                "available_modes": ["balanced", "fast", "strong"],
                "generation": {"mode": "balanced", "temperature": 0.4, "top_p": 0.85, "max_new_tokens": 512},
            },
            "vision": {
                "provider": "openai_compatible",
                "model": "qwen3-vl:4b",
                "default_mode": "default",
                "available_modes": ["default", "detailed", "fast"],
                "generation": {"mode": "default", "temperature": 0.0, "max_tokens": 25},
                "base_url": "http://localhost:11434/v1",
            },
        }):
            runtime = SpiritKinRuntime(agent=FakeCapabilitiesAgent(), node_registry=FakeNodeRegistry(), audit_log=InMemoryAuditLog())

        capabilities = runtime.build_capabilities_payload()

        self.assertEqual(capabilities["type"], "runtime.capabilities")
        self.assertEqual(capabilities["preferences"]["default_text_mode"], "balanced")
        self.assertIn("voice_input", capabilities["supports"])
        self.assertTrue(capabilities["supports"]["execution_events"])
        self.assertTrue(capabilities["supports"]["device_state_events"])
        self.assertTrue(capabilities["supports"]["duplex_voice_session"])
        self.assertTrue(capabilities["supports"]["interruptible_speech"])
        self.assertTrue(capabilities["supports"]["performance_events"])
        self.assertTrue(capabilities["supports"]["task_queue"])
        self.assertTrue(capabilities["supports"]["project_state_events"])
        self.assertEqual(capabilities["models"]["vision"]["provider"], "llamacpp")
        self.assertEqual(capabilities["models"]["vision"]["model"], "qwen/qwen3.6-35b-a3b")
        self.assertEqual(capabilities["models"]["vision"]["base_url"], "http://127.0.0.1:8080/v1")
        self.assertIn("recommended_model_stack", capabilities)
        self.assertIn("llm_reasoning", capabilities["recommended_model_stack"])
        self.assertEqual(capabilities["tooling"]["tool_count"], 1)
        self.assertEqual(capabilities["inventory"]["software_count"], 1)
        self.assertEqual(capabilities["workflow_memory"]["recent_count"], 1)
        self.assertTrue(capabilities["safety"]["pending_confirmation"])
        self.assertEqual(capabilities["remote_nodes"]["total"], 2)
        self.assertEqual(capabilities["remote_nodes"]["status_counts"]["online"], 1)
        self.assertEqual(capabilities["remote_nodes"]["nodes"][0]["node_id"], "office-pc")
        self.assertEqual(capabilities["audit"]["total"], 0)

    def test_runtime_streams_input_and_response_events_when_enabled(self):
        class FakeAgent:
            def process(self, text, visual_context=""):
                return AgentReply(
                    text="OpenClaw 当前状态已同步。",
                    emotion="happy",
                    action="execute_task",
                    agent_name="executor_openclaw",
                    metadata={
                        "response_kind": "execution_result",
                        "execution": {
                            "target": "openclaw",
                            "operation": "status",
                            "data": {
                                "state": "idle",
                                "position": {"x": 1.0, "y": 2.0, "z": 3.0},
                                "gripper_opened": True,
                                "last_command": "status",
                                "transport": "in_memory",
                            },
                            "metadata": {"target": "openclaw", "operation": "status"},
                        },
                        "task": {
                            "task_id": "task_finalizer_demo",
                            "finalizer": {
                                "decision": "commit",
                                "next_status": "COMMITTED",
                                "score": 0.94,
                                "verified": True,
                                "reasons": ["completed_successfully"],
                                "source": "scheduler_task",
                            },
                        },
                    },
                )

        emitted_events = []

        def fake_dispatch(url, event):
            emitted_events.append((url, event))
            return True

        with patch("backend.app.runtime.dispatch_runtime_event", side_effect=fake_dispatch):
            runtime = SpiritKinRuntime(
                agent=FakeAgent(),
                emit_runtime_events=True,
                event_sink_url="ws://example.test/events",
            )
            runtime.handle_text_input("查询 openclaw 状态")

        work_events = [event for event in emitted_events if event[1]["type"] == "assistant.work_updated"]
        main_events = [event for event in emitted_events if event[1]["type"] not in {"assistant.work_updated", "speech.phoneme", "model.interaction", "runtime.aggregated_state", "presence.updated", "memory.updated", "personality.updated"}]
        self.assertTrue(work_events)
        self.assert_work_trace_v1(work_events)
        work_payloads = [event[1]["payload"] for event in work_events]
        finalizer_payload = next(payload for payload in work_payloads if (payload["detail"].get("scheduler") or {}).get("task_id") == "task_finalizer_demo")
        terminal_payload = next(payload for payload in work_payloads if payload["is_terminal"])
        self.assertLess(finalizer_payload["seq"], terminal_payload["seq"])
        self.assertEqual(finalizer_payload["detail"]["result"]["decision"], "commit")
        self.assertEqual([event[1]["type"] for event in main_events], [
            "user_input",
            "assistant.message",
            "assistant.task_updated",
            "assistant.execution_updated",
            "device.openclaw_state_updated",
            "avatar.state",
        ])
        self.assertTrue(all(url == "ws://example.test/events" for url, _ in emitted_events))

    def test_runtime_run_reuses_calibrated_recognizer_for_hotword_listen(self):
        calls = {"ambient": 0, "listen": 0}

        class FakeRecognizer:
            energy_threshold = 1234.0

            def adjust_for_ambient_noise(self, source, duration=2):
                calls["ambient"] += 1

            def listen(self, source, timeout=2, phrase_time_limit=3):
                calls["listen"] += 1
                return object()

        class FakeMicrophone:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSpeechRecognition:
            WaitTimeoutError = TimeoutError
            Microphone = FakeMicrophone

            @staticmethod
            def Recognizer():
                raise AssertionError("run() should reuse the calibrated recognizer")

        fake_recognizer = FakeRecognizer()

        with patch.object(
            SpiritKinRuntime,
            "_load_runtime_dependencies",
            return_value={
                "sr": FakeSpeechRecognition,
                "trigger_emotion": lambda *args, **kwargs: None,
                "speak": lambda *args, **kwargs: None,
                "analyze_gesture": lambda: "",
                "calibrate_microphone": lambda duration=2: fake_recognizer,
                "listen_from_microphone": lambda timeout=8, phrase_time_limit=12: "退出",
                "detect_hotword": lambda audio, hotword: True,
            },
        ), patch("backend.app.runtime.time.sleep", lambda _: None):
            runtime = SpiritKinRuntime(agent=object())
            runtime.run()

        self.assertEqual(calls["listen"], 1)

    def test_runtime_preloads_text_model_before_hotword_loop(self):
        preload_calls = []

        class FakeRecognizer:
            energy_threshold = 800.0

            def listen(self, source, timeout=2, phrase_time_limit=3):
                raise KeyboardInterrupt()

        class FakeMicrophone:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSpeechRecognition:
            WaitTimeoutError = TimeoutError
            Microphone = FakeMicrophone

            @staticmethod
            def Recognizer():
                return FakeRecognizer()

        with patch.object(
            SpiritKinRuntime,
            "_load_runtime_dependencies",
            return_value={
                "sr": FakeSpeechRecognition,
                "trigger_emotion": lambda *args, **kwargs: None,
                "speak": lambda *args, **kwargs: None,
                "analyze_gesture": lambda: "",
                "calibrate_microphone": lambda duration=2: FakeRecognizer(),
                "preload_asr_model": lambda: preload_calls.append("asr"),
                "preload_hotword_model": lambda: preload_calls.append("hotword"),
                "preload_text_model": lambda: preload_calls.append("text") or False,
                "listen_from_microphone": lambda timeout=8, phrase_time_limit=12: "退出",
                "detect_hotword": lambda audio, hotword: True,
            },
        ):
            runtime = SpiritKinRuntime(agent=object())
            runtime.run()

        self.assertEqual(preload_calls, ["asr", "hotword", "text"])

    def test_runtime_ignores_hotword_only_followup_input(self):
        class FakeRecognizer:
            energy_threshold = 800.0

            def __init__(self):
                self.listen_calls = 0

            def listen(self, source, timeout=2, phrase_time_limit=3):
                self.listen_calls += 1
                if self.listen_calls > 1:
                    raise KeyboardInterrupt()
                return object()

        class FakeMicrophone:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSpeechRecognition:
            WaitTimeoutError = TimeoutError
            Microphone = FakeMicrophone

            @staticmethod
            def Recognizer():
                return FakeRecognizer()

        fake_recognizer = FakeRecognizer()

        with patch.object(
            SpiritKinRuntime,
            "_load_runtime_dependencies",
            return_value={
                "sr": FakeSpeechRecognition,
                "trigger_emotion": lambda *args, **kwargs: None,
                "speak": lambda *args, **kwargs: None,
                "analyze_gesture": lambda: "",
                "calibrate_microphone": lambda duration=2: fake_recognizer,
                "listen_from_microphone": lambda timeout=8, phrase_time_limit=12: "Spirit",
                "detect_hotword": lambda audio, hotword: True,
            },
        ), patch.object(SpiritKinRuntime, "handle_voice_input", side_effect=AssertionError("should ignore hotword-only follow-up")), \
             patch("backend.app.runtime.time.sleep", lambda _: None):
            runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")
            runtime.run()

    def test_runtime_accepts_second_followup_without_requiring_new_hotword(self):
        class FakeRecognizer:
            energy_threshold = 800.0

            def __init__(self):
                self.listen_calls = 0

            def listen(self, source, timeout=2, phrase_time_limit=3):
                self.listen_calls += 1
                if self.listen_calls > 1:
                    raise KeyboardInterrupt()
                return object()

        class FakeMicrophone:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSpeechRecognition:
            WaitTimeoutError = TimeoutError
            Microphone = FakeMicrophone

            @staticmethod
            def Recognizer():
                return FakeRecognizer()

        fake_recognizer = FakeRecognizer()
        followups = iter(["Spirit", "机械臂现在什么状态"])
        handled_inputs = []

        def fake_listen_from_microphone(timeout=8, phrase_time_limit=12):
            try:
                return next(followups)
            except StopIteration:
                raise KeyboardInterrupt() from None

        def fake_handle_voice_input(text, visual_context=""):
            handled_inputs.append((text, visual_context))
            return AgentReply(text="OpenClaw 当前状态：idle。")

        with patch.object(
            SpiritKinRuntime,
            "_load_runtime_dependencies",
            return_value={
                "sr": FakeSpeechRecognition,
                "trigger_emotion": lambda *args, **kwargs: None,
                "speak": lambda *args, **kwargs: None,
                "analyze_gesture": lambda: "",
                "calibrate_microphone": lambda duration=2: fake_recognizer,
                "listen_from_microphone": fake_listen_from_microphone,
                "detect_hotword": lambda audio, hotword: True,
            },
        ), patch.object(SpiritKinRuntime, "handle_voice_input", side_effect=fake_handle_voice_input), \
             patch("backend.app.runtime.time.sleep", lambda _: None):
            runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")
            runtime.run()

        self.assertEqual(handled_inputs, [("机械臂现在什么状态", "")])

    def test_runtime_continues_voice_session_after_successful_command(self):
        class FakeRecognizer:
            energy_threshold = 800.0

            def listen(self, source, timeout=2, phrase_time_limit=3):
                return object()

        class FakeMicrophone:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeSpeechRecognition:
            WaitTimeoutError = TimeoutError
            Microphone = FakeMicrophone

            @staticmethod
            def Recognizer():
                return FakeRecognizer()

        fake_recognizer = FakeRecognizer()
        followups = iter(["机械臂现在什么状态", "打开夹爪"])
        handled_inputs = []

        def fake_listen_from_microphone(timeout=8, phrase_time_limit=12):
            try:
                return next(followups)
            except StopIteration:
                raise KeyboardInterrupt() from None

        def fake_handle_voice_input(text, visual_context=""):
            handled_inputs.append(text)
            return AgentReply(text=f"已处理：{text}")

        with patch.object(
            SpiritKinRuntime,
            "_load_runtime_dependencies",
            return_value={
                "sr": FakeSpeechRecognition,
                "trigger_emotion": lambda *args, **kwargs: None,
                "speak": lambda *args, **kwargs: None,
                "analyze_gesture": lambda: "",
                "calibrate_microphone": lambda duration=2: fake_recognizer,
                "listen_from_microphone": fake_listen_from_microphone,
                "detect_hotword": lambda audio, hotword: True,
            },
        ), patch.object(SpiritKinRuntime, "handle_voice_input", side_effect=fake_handle_voice_input), \
             patch("backend.app.runtime.time.sleep", lambda _: None), \
             patch.dict("os.environ", {"SPIRITKIN_VOICE_SESSION_MAX_TURNS": "3"}, clear=False):
            runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")
            runtime.run()

        self.assertEqual(handled_inputs, ["机械臂现在什么状态", "打开夹爪"])

    def test_runtime_recognizes_hotword_only_followup_after_normalization(self):
        runtime = SpiritKinRuntime(agent=object(), hotword="Spirit")

        self.assertTrue(runtime._should_ignore_followup_input("Spirit!!!"))
        self.assertTrue(runtime._should_ignore_followup_input("  spirit  "))
        self.assertFalse(runtime._should_ignore_followup_input("机械臂现在什么状态"))

    def test_runtime_strips_hotword_prefix_from_voice_input(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context=""):
                self.calls.append(text)
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent, hotword="Spirit")

        reply = runtime.handle_voice_input("SPIRIT 打开浏览器。")

        self.assertEqual(fake_agent.calls, ["打开浏览器。"])
        self.assertEqual(reply.metadata["input_channel"], "voice")

    def test_runtime_corrects_cantonese_voice_command_words(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context="", channel=None, input_metadata=None):
                self.calls.append((text, input_metadata or {}))
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent, hotword="Spirit")

        runtime.handle_voice_input("Spirit 幫我打開飛書")

        self.assertEqual(fake_agent.calls[0][0], "帮我打开飞书")
        self.assertEqual(fake_agent.calls[0][1]["hotword_stripped_text"], "幫我打開飛書")
        self.assertEqual(fake_agent.calls[0][1]["asr_corrected_text"], "帮我打开飞书")

    def test_runtime_corrects_browser_asr_alias_for_voice_input(self):
        class FakeAgent:
            def __init__(self):
                self.calls = []

            def process(self, text, visual_context=""):
                self.calls.append(text)
                return AgentReply(text=f"收到：{text}")

        fake_agent = FakeAgent()
        runtime = SpiritKinRuntime(agent=fake_agent)

        runtime.handle_voice_input("打开默认游览器")

        self.assertEqual(fake_agent.calls, ["打开默认浏览器"])

    def test_runtime_voice_visual_context_is_opt_in(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(SpiritKinRuntime._voice_visual_context_enabled())

        with patch.dict("os.environ", {"SPIRITKIN_ENABLE_VOICE_VISION_CONTEXT": "1"}, clear=True):
            self.assertTrue(SpiritKinRuntime._voice_visual_context_enabled())


if __name__ == "__main__":
    unittest.main()
