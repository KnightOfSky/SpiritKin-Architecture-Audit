from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "collaboration_agent_worker.py"
MAILBOX_SCRIPT_PATH = ROOT / "scripts" / "collaboration_mailbox.py"


def load_worker_module():
    spec = importlib.util.spec_from_file_location("collaboration_agent_worker", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load collaboration_agent_worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_mailbox_module():
    spec = importlib.util.spec_from_file_location("collaboration_mailbox", MAILBOX_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load collaboration_mailbox")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollaborationAgentWorkerScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()
        cls.mailbox = load_mailbox_module()

    def test_route_bus_message_helpers_read_agent_envelope_snapshot(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-1",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "question",
            "content": "请回复。",
            "context_id": "route-thread",
            "task_id": "task-1",
            "artifacts": [{"kind": "context_pack", "path": "state/collaboration/context_packs/pack.json"}],
            "metadata": {"thread_id": "route-thread"},
        }

        self.assertEqual(self.worker.message_sender(message), "codex")
        self.assertEqual(self.worker.message_type(message), "question")
        self.assertEqual(self.worker.message_content(message), "请回复。")
        self.assertEqual(self.worker.message_thread_id(message), "route-thread")
        self.assertEqual(self.worker.message_task_id(message), "task-1")
        self.assertEqual(self.worker.message_context_pack_path(message), "state/collaboration/context_packs/pack.json")
        self.assertEqual(self.mailbox.message_sender(message), "codex")
        self.assertEqual(self.mailbox.message_recipient_text(message), "claude_code")
        self.assertEqual(self.mailbox.message_thread_id(message), "route-thread")
        self.assertEqual(self.mailbox.message_context_pack_path(message), "state/collaboration/context_packs/pack.json")

    def test_legacy_collaboration_message_helpers_still_work(self):
        message = {
            "message_id": "msg-1",
            "from_agent": "claude_code",
            "to_agents": ["codex"],
            "role": "answer",
            "content": "legacy answer",
            "thread_id": "legacy-thread",
            "task_id": "task-legacy",
            "context_pack_path": "state/collaboration/context_packs/legacy.json",
        }

        self.assertEqual(self.worker.message_sender(message), "claude_code")
        self.assertEqual(self.worker.message_type(message), "answer")
        self.assertEqual(self.worker.message_content(message), "legacy answer")
        self.assertEqual(self.worker.message_thread_id(message), "legacy-thread")
        self.assertEqual(self.worker.message_task_id(message), "task-legacy")
        self.assertEqual(self.worker.message_context_pack_path(message), "state/collaboration/context_packs/legacy.json")
        self.assertEqual(self.mailbox.message_sender(message), "claude_code")
        self.assertEqual(self.mailbox.message_recipient_text(message), "codex")
        self.assertEqual(self.mailbox.message_thread_id(message), "legacy-thread")
        self.assertEqual(self.mailbox.message_context_pack_path(message), "state/collaboration/context_packs/legacy.json")

    def test_message_id_conflict_is_idempotent_terminal(self):
        self.assertTrue(self.worker.is_idempotent_message_conflict(RuntimeError("HTTP 400: message_id_conflict:reply-1")))
        self.assertFalse(self.worker.is_idempotent_message_conflict(RuntimeError("provider timeout")))

    def test_worker_consumes_message_id_conflict_without_invoking_failure_reply(self):
        message = {
            "message_id": "agentmsg-conflict",
            "sender": "human_desktop",
            "recipient": "model_deepseek",
            "message_type": "question",
            "content": "你好",
            "context_id": "thread-conflict",
            "created_at": time.time(),
        }
        with patch.object(
            self.worker,
            "load_external_assistant",
            return_value={"assistant_id": "model_deepseek", "kind": "api", "enabled": True},
        ), patch.object(self.worker, "list_worker_messages", return_value=[message]), \
            patch.object(self.worker, "preempt_human_messages"), \
            patch.object(self.worker, "try_acquire_worker_message_claim", return_value=Path("conflict.claim")), \
            patch.object(self.worker, "release_worker_message_claim"), \
            patch.object(self.worker, "reply_already_persisted", return_value=False), \
            patch.object(self.worker, "record_worker_event"), \
            patch.object(
                self.worker,
                "process_worker_message_with_retry",
                side_effect=RuntimeError("message_id_conflict:reply-model_deepseek-agentmsg-conflict"),
            ) as process, patch.object(self.worker, "mark_consumed") as mark_consumed, \
            patch.object(self.worker, "post_worker_failure_reply") as failure_reply:
            code = self.worker.main(["--agent", "model_deepseek", "--once", "--no-push"])

        self.assertEqual(code, 0)
        process.assert_called_once()
        mark_consumed.assert_called_once()
        failure_reply.assert_not_called()

    def test_existing_deterministic_reply_is_consumed_before_model_call(self):
        message = {
            "message_id": "agentmsg-recovered",
            "sender": "human_desktop",
            "recipient": "model_deepseek",
            "message_type": "question",
            "content": "不要重复调用",
            "context_id": "thread-recovered",
            "created_at": time.time(),
        }
        with patch.object(
            self.worker,
            "load_external_assistant",
            return_value={"assistant_id": "model_deepseek", "kind": "api", "enabled": True},
        ), patch.object(self.worker, "list_worker_messages", return_value=[message]), \
            patch.object(self.worker, "preempt_human_messages"), \
            patch.object(self.worker, "try_acquire_worker_message_claim", return_value=Path("recovered.claim")), \
            patch.object(self.worker, "release_worker_message_claim"), \
            patch.object(self.worker, "reply_already_persisted", return_value=True), \
            patch.object(self.worker, "record_worker_event"), \
            patch.object(self.worker, "process_worker_message_with_retry") as process, \
            patch.object(self.worker, "mark_consumed") as mark_consumed:
            code = self.worker.main(["--agent", "model_deepseek", "--once", "--no-push"])

        self.assertEqual(code, 0)
        process.assert_not_called()
        mark_consumed.assert_called_once()

    def test_existing_reply_lookup_uses_deterministic_reply_id(self):
        parent = {
            "message_id": "agentmsg-parent",
            "thread_id": "thread-parent",
        }
        with patch.object(
            self.worker,
            "request_json",
            return_value={"messages": [{"message_id": "reply-model_deepseek-agentmsg-parent"}]},
        ) as request:
            exists = self.worker.reply_already_persisted("api", "model_deepseek", parent)

        self.assertTrue(exists)
        self.assertEqual(request.call_args.args[2]["action"], "list_messages")

    def test_mailbox_route_bus_inbox_uses_agent_route_bus_query_by_default(self):
        route_message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-route",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "question",
            "content": "route bus question",
            "context_id": "route-thread",
            "task_id": "task-route",
        }

        with patch.object(
            self.mailbox,
            "post",
            return_value={"agent_route_bus_messages": {"messages": [route_message]}},
        ) as post:
            messages = self.mailbox.list_mailbox_messages(
                "http://127.0.0.1:8788",
                "claude_code",
                "task-route",
                "route-thread",
                transport="route_bus",
                include_consumed=False,
                limit=5,
            )

        self.assertEqual(messages, [route_message])
        post.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {
                "action": "list_agent_route_bus_messages",
                "to_agent": "claude_code",
                "consumer": "claude_code",
                "task_id": "task-route",
                "thread_id": "route-thread",
                "include_acked": False,
                "include_audit": False,
                "limit": 5,
            },
        )

    def test_mailbox_legacy_inbox_still_uses_collaboration_messages_query(self):
        legacy_message = {
            "message_id": "message-legacy",
            "from_agent": "codex",
            "to_agents": ["claude_code"],
            "role": "question",
            "content": "legacy question",
            "thread_id": "legacy-thread",
            "task_id": "task-legacy",
        }

        with patch.object(self.mailbox, "post", return_value={"messages": [legacy_message]}) as post:
            messages = self.mailbox.list_mailbox_messages(
                "http://127.0.0.1:8788",
                "claude_code",
                "task-legacy",
                "legacy-thread",
                transport="legacy_inbox",
                include_consumed=True,
                limit=7,
            )

        self.assertEqual(messages, [legacy_message])
        post.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {
                "action": "list_messages",
                "to_agent": "claude_code",
                "task_id": "task-legacy",
                "thread_id": "legacy-thread",
                "include_read": True,
                "limit": 7,
            },
        )

    def test_mailbox_route_bus_read_acks_message_for_consumer(self):
        ack_result = {
            "agent_route_bus_ack": {
                "acked": True,
                "ack": {"message_id": "agentmsg-route", "consumer": "claude_code"},
            }
        }

        with patch.object(self.mailbox, "post", return_value=ack_result) as post:
            result = self.mailbox.mark_message_consumed(
                "http://127.0.0.1:8788",
                "claude_code",
                "agentmsg-route",
                transport="route_bus",
            )

        self.assertEqual(result, ack_result)
        post.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {
                "action": "ack_agent_route_bus_message",
                "message_id": "agentmsg-route",
                "consumer": "claude_code",
                "note": "collaboration_mailbox_read",
            },
        )

    def test_mailbox_legacy_read_still_marks_collaboration_message_read(self):
        with patch.object(self.mailbox, "post", return_value={"message": {"message_id": "message-legacy"}}) as post:
            result = self.mailbox.mark_message_consumed(
                "http://127.0.0.1:8788",
                "claude_code",
                "message-legacy",
                transport="legacy_inbox",
            )

        self.assertEqual(result, {"message": {"message_id": "message-legacy"}})
        post.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {"action": "mark_message_read", "message_id": "message-legacy", "reader": "claude_code"},
        )

    def test_mailbox_status_command_uses_non_consuming_worker_status_action(self):
        status_result = {
            "agent_route_bus_worker_status": {
                "real_worker_status": "not_enabled",
                "pending_count": 1,
                "ack_count": 0,
                "dry_run_available": True,
                "agents": [],
            }
        }

        with patch.object(self.mailbox, "post", return_value=status_result) as post, patch.object(self.mailbox, "print_worker_status", return_value=0) as printer:
            code = self.mailbox.main(
                [
                    "--api",
                    "http://127.0.0.1:8788",
                    "status",
                    "--agent",
                    "claude_code",
                    "--thread-id",
                    "route-thread",
                    "--task-id",
                    "task-route",
                    "--limit",
                    "11",
                ]
            )

        self.assertEqual(code, 0)
        post.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {
                "action": "agent_route_bus_worker_status",
                "agents": ["claude_code"],
                "task_id": "task-route",
                "thread_id": "route-thread",
                "limit": 11,
            },
        )
        printer.assert_called_once_with(status_result, False)

    def test_missing_external_assistant_config_does_not_fallback_to_command_name(self):
        assistant = self.worker.load_external_assistant("does-not-exist-agent-config.json", "claude_code")

        self.assertFalse(assistant["enabled"])
        self.assertEqual(assistant["command"], "")
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            self.worker.assert_external_assistant_enabled(assistant)

    def test_external_assistant_must_be_enabled_and_have_command(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent_management.json"
            config_path.write_text(
                json.dumps(
                    {
                        "external_assistants": [
                            {"assistant_id": "disabled_cli", "command": "python", "enabled": False},
                            {"assistant_id": "missing_command", "command": "", "enabled": True},
                            {"assistant_id": "ready_cli", "command": "python", "enabled": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            disabled = self.worker.load_external_assistant(str(config_path), "disabled_cli")
            missing_command = self.worker.load_external_assistant(str(config_path), "missing_command")
            ready = self.worker.load_external_assistant(str(config_path), "ready_cli")

        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            self.worker.assert_external_assistant_enabled(disabled)
        with self.assertRaisesRegex(RuntimeError, "no command"):
            self.worker.assert_external_assistant_enabled(missing_command)
        self.worker.assert_external_assistant_enabled(ready)

    def test_legacy_codex_command_is_upgraded_to_structured_event_mode(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agent_management.json"
            config_path.write_text(
                json.dumps(
                    {
                        "external_assistants": [
                            {
                                "assistant_id": "codex_cli",
                                "command": "codex exec --skip-git-repo-check --color never -",
                                "enabled": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            assistant = self.worker.load_external_assistant(str(config_path), "codex_cli")

        self.assertIn("codex exec --json", assistant["command"])
        self.assertTrue(self.worker.structured_cli_output(assistant["command"]))

    def test_codex_command_resolves_configured_live_executable(self):
        with TemporaryDirectory() as tmp:
            executable = Path(tmp) / "current codex.exe"
            executable.write_bytes(b"test")
            with patch.dict(os.environ, {"SPIRITKIN_CODEX_EXECUTABLE": str(executable)}):
                command = self.worker.resolve_external_assistant_command(
                    "codex_cli",
                    "codex exec --skip-git-repo-check --color never -",
                )

        self.assertIn(str(executable.resolve()), command)
        self.assertIn("exec --json", command)
        self.assertNotRegex(command, r"^\s*codex(?:\.exe|\.cmd|\.ps1)?\s")

    def test_non_codex_command_keeps_operator_configuration(self):
        command = 'claude -p --output-format stream-json'

        self.assertEqual(
            command,
            self.worker.resolve_external_assistant_command("claude_code", command),
        )

    def test_build_prompt_includes_workspace_repository_snapshot(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("SpiritKin test repository", encoding="utf-8")
            (workspace / "backend").mkdir()
            (workspace / "backend" / "app.py").write_text("print('hello')", encoding="utf-8")
            (workspace / "node_modules").mkdir()
            (workspace / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-context",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请检查仓库。",
                "context_id": "route-thread",
            }

            prompt = self.worker.build_prompt(message)

        self.assertIn(f"Workspace: {workspace}", prompt)
        self.assertIn("Repository snapshot:", prompt)
        self.assertIn("- README.md", prompt)
        self.assertIn("- backend/app.py", prompt)
        self.assertIn("Excerpt: README.md", prompt)
        self.assertIn("SpiritKin test repository", prompt)
        self.assertNotIn("ignored.js", prompt)

    def test_build_prompt_injects_collaboration_context_brief(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-brief",
            "sender": "human_desktop",
            "recipient": "codex",
            "message_type": "question",
            "content": "hello",
            "context_id": "route-thread",
        }

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPIRITKIN_DISABLE_COLLABORATION_CONTEXT", None)
            prompt = self.worker.build_prompt(message)
        self.assertIn("docs/ai_collaboration_context.md", prompt)
        self.assertIn("verify any completion claim", prompt)

        with patch.dict(os.environ, {"SPIRITKIN_DISABLE_COLLABORATION_CONTEXT": "1"}, clear=False):
            disabled_prompt = self.worker.build_prompt(message)
        self.assertNotIn("Shared collaboration context", disabled_prompt)

    def test_build_prompt_locks_identity_and_injects_persona(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-identity",
            "sender": "human_desktop",
            "recipient": "model_deepseek",
            "message_type": "question",
            "content": "请作为正方参与这场辩论，保持你的立场。",
            "context_id": "route-thread",
        }
        assistant = {
            "assistant_id": "model_deepseek",
            "label": "DeepSeek 评审",
            "request_params": {"persona": "坚持低风险工程落地，少讲空泛口号。"},
        }

        with patch.dict(os.environ, {"SPIRITKIN_DISABLE_COLLABORATION_CONTEXT": "1"}, clear=False):
            prompt = self.worker.build_prompt(message, assistant)

        self.assertIn("你是 DeepSeek 评审（agent_id=model_deepseek）", prompt)
        self.assertIn("你只能以这个身份发言", prompt)
        self.assertIn("你的固定人设：坚持低风险工程落地，少讲空泛口号。", prompt)
        self.assertIn("你的立场自始至终不变", prompt)
        self.assertIn("不要输出对系统故障的诊断分析", prompt)

    def test_model_request_params_filters_worker_persona(self):
        assistant = {"request_params": {"persona": "只进提示词", "temperature": 0.2}}

        self.assertEqual(self.worker.model_request_params(assistant), {"temperature": 0.2})

    def test_run_external_assistant_prefers_message_workspace_as_cwd(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            configured = Path(tmp) / "configured"
            workspace.mkdir()
            configured.mkdir()
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-cwd",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请读取上下文。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": "codex",
                "working_directory": str(configured),
                "enabled": True,
            }

            with patch.object(
                self.worker.subprocess,
                "run",
                return_value=self.worker.subprocess.CompletedProcess(args="codex", returncode=0, stdout="ok", stderr=""),
            ) as run:
                reply = self.worker.run_external_assistant(assistant, message)

        self.assertEqual(reply, "ok")
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["cwd"], str(workspace))
        self.assertIn(f"Workspace: {workspace}", run.call_args.kwargs["input"])

    def test_run_external_assistant_streams_stdout_and_stderr_worker_events(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = "import sys; print('first token', flush=True); print('warn token', file=sys.stderr, flush=True); print('second token', flush=True)"
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-stream",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请流式回复。",
                "context_id": "route-thread",
                "task_id": "task-stream",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}"',
                "working_directory": "",
                "enabled": True,
            }
            events: list[dict] = []

            def fake_request_json(_api: str, _path: str, payload: dict):
                events.append(payload)
                return {"ok": True}

            with patch.object(self.worker, "request_json", side_effect=fake_request_json):
                reply = self.worker.run_external_assistant(
                    assistant,
                    message,
                    api="http://127.0.0.1:8788",
                    agent="codex",
                    transport="route_bus",
                    dry_run=False,
                )

        self.assertIn("first token", reply)
        self.assertIn("second token", reply)
        stream_events = [event for event in events if event.get("status") == "stream"]
        self.assertGreaterEqual(len(stream_events), 3)
        outputs = [event["metadata"]["output"] for event in stream_events]
        self.assertIn("first", outputs)
        self.assertIn("warn", outputs)
        self.assertIn("second", outputs)
        self.assertGreaterEqual(outputs.count("token"), 3)
        self.assertEqual({event["message_id"] for event in stream_events}, {"agentmsg-stream"})

    def test_run_external_assistant_streams_output_without_newlines(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = "import sys,time; sys.stdout.write('chunk-one '); sys.stdout.flush(); time.sleep(0.05); sys.stdout.write('chunk-two'); sys.stdout.flush()"
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-token-stream",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请流式回复。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}"',
                "working_directory": "",
                "enabled": True,
            }
            events: list[dict] = []

            with patch.object(self.worker, "request_json", side_effect=lambda _api, _path, payload: events.append(payload) or {"ok": True}):
                reply = self.worker.run_external_assistant(
                    assistant,
                    message,
                    api="http://127.0.0.1:8788",
                    agent="codex",
                    transport="route_bus",
                    dry_run=False,
                )

        self.assertEqual(reply, "chunk-one chunk-two")
        outputs = [event["metadata"]["output"] for event in events if event.get("status") == "stream"]
        self.assertTrue(any("chunk-one" in output for output in outputs))
        self.assertTrue(any("chunk-two" in output for output in outputs))

    def test_classify_structured_cli_line_codex_events(self):
        started = self.worker.classify_structured_cli_line(
            json.dumps({"type": "item.started", "item": {"id": "cmd-1", "item_type": "command_execution", "command": "pytest -q"}})
        )
        self.assertEqual(started[0][0], "command")
        self.assertIn("pytest -q", started[0][1])
        self.assertEqual(started[0][2]["tool_call_id"], "cmd-1")
        self.assertEqual(started[0][2]["lifecycle"], "tool_running")
        completed = self.worker.classify_structured_cli_line(
            json.dumps({"type": "item.completed", "item": {"id": "cmd-1", "item_type": "command_execution", "command": "pytest -q", "exit_code": 0, "aggregated_output": "3 passed"}})
        )
        self.assertEqual(completed[0][2]["tool_call_id"], "cmd-1")
        self.assertEqual(completed[0][2]["lifecycle"], "tool_completed")
        self.assertEqual(completed[0][2]["command_output"], "3 passed")
        self.assertIn("3 passed", completed[0][1])
        edit = self.worker.classify_structured_cli_line(
            json.dumps({"type": "item.completed", "item": {"item_type": "file_change", "changes": [{"path": "a.py", "kind": "add"}]}})
        )
        self.assertEqual(edit[0][0], "edit")
        self.assertIn("a.py", edit[0][1])
        message = self.worker.classify_structured_cli_line(
            json.dumps({"type": "item.completed", "item": {"item_type": "agent_message", "text": "最终回复"}})
        )
        self.assertEqual(message[0][0], "token")
        self.assertEqual(message[0][1], "最终回复")
        self.assertIsNone(self.worker.classify_structured_cli_line("plain text output"))
        self.assertEqual(self.worker.classify_structured_cli_line(json.dumps({"type": "turn.completed"})), [])

    def test_classify_structured_cli_line_claude_stream_json(self):
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "先看测试"},
                        {"type": "tool_use", "id": "toolu-1", "name": "Bash", "input": {"command": "pytest -q"}},
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/main.py"}},
                    ]
                },
            }
        )
        events = self.worker.classify_structured_cli_line(line)
        self.assertEqual([item[0] for item in events], ["token", "command", "edit"])
        self.assertIn("pytest -q", events[1][1])
        self.assertIn("src/main.py", events[2][1])
        self.assertEqual(events[1][2]["tool_call_id"], "toolu-1")
        self.assertEqual(events[1][2]["lifecycle"], "tool_running")

        result = self.worker.classify_structured_cli_line(json.dumps({
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu-1", "content": "3 passed"}]},
        }))
        self.assertEqual(result[0][2]["tool_call_id"], "toolu-1")
        self.assertEqual(result[0][2]["lifecycle"], "tool_completed")
        self.assertIn("3 passed", result[0][1])

    def test_strip_think_blocks_and_salvage_reasoning(self):
        self.assertEqual(self.worker.strip_think_blocks("<think>内部推理</think>最终答案").strip(), "最终答案")
        self.assertEqual(self.worker.strip_think_blocks("答案<think>未闭合的推理…").strip(), "答案")
        reasoning = "第一段思考。\n\n第二段推敲细节。\n\n最终我会回复：协作方案没问题，建议先补充单测再合并。"
        salvaged = self.worker.salvage_reply_from_reasoning(reasoning)
        self.assertIn("协作方案没问题", salvaged)
        self.assertNotIn("第一段思考", salvaged)
        self.assertEqual(self.worker.salvage_reply_from_reasoning(""), "")

    def test_extract_think_text_closed_and_unclosed_blocks(self):
        self.assertEqual(self.worker.extract_think_text("<think>内部推理</think>最终答案"), "内部推理")
        # 未闭合块：之后的全部内容都算思考文本（流式期间的常态）。
        self.assertEqual(self.worker.extract_think_text("答案<think>还在推理"), "还在推理")
        self.assertEqual(
            self.worker.extract_think_text("<think>先想A</think>正文<think>再想B"),
            "先想A再想B",
        )
        self.assertEqual(self.worker.extract_think_text("没有思考标签"), "")
        self.assertEqual(self.worker.extract_think_text(""), "")

    def test_stream_token_batcher_routes_think_to_reasoning(self):
        """本地推理模型把 <think> 混进 content 流：气泡只收干净正文，think 改道 reasoning（修撑大抽动）。"""
        events: list[tuple[str, str, dict]] = []
        batcher = self.worker.StreamTokenBatcher(
            lambda text, channel, meta: events.append((text, channel, dict(meta))),
            token_flush_chars=1,
        )
        try:
            # 标签跨 token 撕裂：分离基于累计全文，必须免疫。
            for piece in ["<thi", "nk>我在思", "考</thi", "nk>正式", "答案"]:
                batcher.add(piece, {"channel": "token"})
                batcher.flush()
        finally:
            batcher.close()
        token_events = [item for item in events if item[1] == "token"]
        reasoning_events = [item for item in events if item[1] == "reasoning"]
        # 正文气泡只出现干净正文，无任何 think 标签/内文。
        visible = "".join(text for text, _, _ in token_events)
        self.assertEqual(visible, "正式答案")
        self.assertTrue(all("<" not in text for text, _, _ in token_events))
        # think 内文改道 reasoning 泳道。
        self.assertEqual("".join(text for text, _, _ in reasoning_events), "我在思考")
        # accumulated 快照同样干净（桌面用它整体覆盖草稿）。
        self.assertEqual(token_events[-1][2].get("accumulated"), "正式答案")

    def test_stream_token_batcher_plain_text_passthrough(self):
        events: list[tuple[str, str, dict]] = []
        batcher = self.worker.StreamTokenBatcher(
            lambda text, channel, meta: events.append((text, channel, dict(meta))),
            token_flush_chars=1,
        )
        try:
            for piece in ["你好", "，世界"]:
                batcher.add(piece, {"channel": "token"})
                batcher.flush()
        finally:
            batcher.close()
        token_events = [item for item in events if item[1] == "token"]
        self.assertEqual("".join(text for text, _, _ in token_events), "你好，世界")
        self.assertEqual(token_events[-1][2].get("accumulated"), "你好，世界")
        self.assertFalse([item for item in events if item[1] == "reasoning"])

    def test_strip_markdown_decorations_cleans_common_markers(self):
        text = (
            "## 结论\n"
            "我认为 **人工智能** 不会取代 *人类*，理由如下：\n"
            "- 第一点\n"
            "* 第二点\n"
            "> 引用一句话\n"
            "行内 `code` 保内容。\n"
            "---\n"
            "1. 编号列表不动\n"
        )
        cleaned = self.worker.strip_markdown_decorations(text)
        self.assertNotIn("##", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertIn("结论", cleaned)
        self.assertIn("人工智能", cleaned)
        self.assertIn("人类", cleaned)
        self.assertIn("• 第一点", cleaned)
        self.assertIn("• 第二点", cleaned)
        self.assertIn("引用一句话", cleaned)
        self.assertIn("行内 code 保内容", cleaned)
        self.assertIn("1. 编号列表不动", cleaned)
        # 分隔线整行删除。
        self.assertNotIn("---", cleaned)

    def test_strip_markdown_decorations_preserves_code_fences(self):
        text = "说明 **要点**：\n```json\n{\"tool_call\": {\"target\": \"fs\", \"operation\": \"read\"}}\n```\n结尾 *备注*。"
        cleaned = self.worker.strip_markdown_decorations(text)
        self.assertIn('```json\n{"tool_call"', cleaned)
        self.assertIn("要点", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertIn("结尾 备注。", cleaned)
        # 数学乘号/无标记文本不受影响。
        self.assertEqual(self.worker.strip_markdown_decorations("3 * 4 = 12"), "3 * 4 = 12")
        self.assertEqual(self.worker.strip_markdown_decorations("纯文本发言"), "纯文本发言")

    def test_run_external_assistant_structured_json_stream(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = (
                "import json;"
                "print(json.dumps({'type': 'item.started', 'item': {'id': 'cmd-live-1', 'item_type': 'command_execution', 'command': 'ls -la'}}), flush=True);"
                "print(json.dumps({'type': 'item.completed', 'item': {'id': 'cmd-live-1', 'item_type': 'command_execution', 'command': 'ls -la', 'exit_code': 0, 'aggregated_output': 'total 0'}}), flush=True);"
                "print(json.dumps({'type': 'item.completed', 'item': {'item_type': 'file_change', 'changes': [{'path': 'src/app.py', 'kind': 'update'}]}}), flush=True);"
                "print(json.dumps({'type': 'item.completed', 'item': {'item_type': 'agent_message', 'text': 'Done: updated src/app.py'}}), flush=True)"
            )
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-structured",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请修改文件。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}" --json',
                "working_directory": "",
                "enabled": True,
            }
            events: list[dict] = []

            with patch.object(self.worker, "request_json", side_effect=lambda _api, _path, payload: events.append(payload) or {"ok": True}):
                reply = self.worker.run_external_assistant(
                    assistant,
                    message,
                    api="http://127.0.0.1:8788",
                    agent="codex",
                    transport="route_bus",
                    dry_run=False,
                )

        self.assertEqual(reply, "Done: updated src/app.py")
        streams = [
            ((event.get("metadata") or {}).get("stream"), (event.get("metadata") or {}).get("output"))
            for event in events
            if event.get("status") == "stream"
        ]
        self.assertTrue(any(stream == "command" and "$ ls -la" in (output or "") for stream, output in streams))
        self.assertTrue(any(stream == "edit" and "src/app.py" in (output or "") for stream, output in streams))
        self.assertFalse(any((output or "").lstrip().startswith("{") for stream, output in streams if stream == "stdout"))
        command_events = [
            event for event in events
            if (event.get("metadata") or {}).get("tool_call_id") == "cmd-live-1"
        ]
        self.assertEqual(
            [(event.get("metadata") or {}).get("lifecycle") for event in command_events],
            ["tool_running", "tool_completed"],
        )

    def test_run_external_assistant_streaming_times_out_and_returns_failure(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = "import time; time.sleep(5)"
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-timeout",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请流式回复。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}"',
                "working_directory": "",
                "enabled": True,
                "timeout_seconds": 1,
            }
            events: list[dict] = []

            with patch.object(self.worker, "request_json", side_effect=lambda _api, _path, payload: events.append(payload) or {"ok": True}):
                reply = self.worker.run_external_assistant(
                    assistant,
                    message,
                    api="http://127.0.0.1:8788",
                    agent="codex",
                    transport="route_bus",
                    dry_run=False,
                )

        self.assertIn("timed out", reply)
        failed_events = [event for event in events if event.get("status") == "failed"]
        self.assertTrue(failed_events)
        self.assertTrue(any("timed out" in event["error"] for event in failed_events))

    def test_run_external_assistant_streaming_handles_closed_stdin(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = "import sys; print('closed early', file=sys.stderr); sys.exit(7)"
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-closed-stdin",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请流式回复。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}"',
                "working_directory": "",
                "enabled": True,
                "timeout_seconds": 5,
            }
            events: list[dict] = []

            with patch.object(self.worker, "request_json", side_effect=lambda _api, _path, payload: events.append(payload) or {"ok": True}):
                reply = self.worker.run_external_assistant(
                    assistant,
                    message,
                    api="http://127.0.0.1:8788",
                    agent="codex",
                    transport="route_bus",
                    dry_run=False,
                )

        self.assertIn("exit code 7", reply)
        self.assertTrue("closed early" in reply or any("closed early" in event["metadata"]["output"] for event in events))

    def test_process_worker_message_does_not_post_or_ack_when_real_assistant_disabled(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-disabled",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "question",
            "content": "should not be consumed",
            "context_id": "route-thread",
        }
        assistant = {"assistant_id": "claude_code", "command": "python", "enabled": False}

        with patch.object(self.worker, "post_reply") as post_reply, patch.object(self.worker, "mark_consumed") as mark_consumed, patch.object(self.worker, "record_worker_event"):
            with self.assertRaisesRegex(RuntimeError, "not enabled"):
                self.worker.process_worker_message(
                    "http://127.0.0.1:8788",
                    "claude_code",
                    message,
                    assistant,
                    dry_run=False,
                    no_post=False,
                    transport="route_bus",
                )

        post_reply.assert_not_called()
        mark_consumed.assert_not_called()

    def test_process_worker_message_dry_run_posts_and_consumes_without_enabled_assistant(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-dry-run",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "question",
            "content": "dry-run only",
            "context_id": "route-thread",
        }
        assistant = {"assistant_id": "claude_code", "command": "", "enabled": False}

        events: list[dict] = []

        with patch.object(self.worker, "post_reply") as post_reply, \
            patch.object(self.worker, "mark_consumed") as mark_consumed, \
            patch.object(self.worker, "record_worker_event", side_effect=lambda *_args, **kwargs: events.append(kwargs)):
            reply = self.worker.process_worker_message(
                "http://127.0.0.1:8788",
                "claude_code",
                message,
                assistant,
                dry_run=True,
                no_post=False,
                transport="route_bus",
            )

        self.assertIn("[dry-run:claude_code]", reply)
        post_reply.assert_called_once()
        mark_consumed.assert_called_once()
        lifecycles = [event.get("metadata", {}).get("lifecycle") for event in events]
        self.assertIn("context_loaded", lifecycles)
        self.assertIn("prompt_ready", lifecycles)

    def test_process_worker_message_records_context_and_reply_lifecycle(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-lifecycle",
            "sender": "human_desktop",
            "recipient": "codex",
            "message_type": "question",
            "content": "@Codex ping",
            "context_id": "thread-lifecycle",
            "task_id": "task-lifecycle",
        }
        events: list[dict] = []

        with patch.object(self.worker, "run_external_assistant", return_value="pong"), \
            patch.object(self.worker, "post_reply") as post_reply, \
            patch.object(self.worker, "mark_consumed") as mark_consumed, \
            patch.object(self.worker, "request_json", side_effect=lambda _api, _path, payload: events.append(payload) or {"ok": True}):
            reply = self.worker.process_worker_message(
                "http://127.0.0.1:8788",
                "codex",
                message,
                {"assistant_id": "codex_cli", "command": "codex", "enabled": True},
                dry_run=False,
                no_post=False,
                transport="route_bus",
            )

        self.assertEqual(reply, "pong")
        post_reply.assert_called_once()
        mark_consumed.assert_called_once()
        lifecycles = [event.get("metadata", {}).get("lifecycle") for event in events]
        self.assertIn("context_loaded", lifecycles)
        self.assertIn("reply_posting", lifecycles)
        self.assertIn("acked", lifecycles)

    def test_process_worker_message_defers_visible_reply_until_tool_result(self):
        message = {
            "message_id": "agentmsg-tool-turn",
            "sender": "human_desktop",
            "recipient": "main_text",
            "message_type": "question",
            "content": "打开命令提示符",
            "context_id": "thread-tool-turn",
        }
        events: list[dict] = []

        with patch.object(self.worker, "run_external_assistant", return_value="tool request"), \
            patch.object(self.worker, "submit_tool_calls_from_reply", return_value=1), \
            patch.object(self.worker, "post_reply") as post_reply, \
            patch.object(self.worker, "mark_consumed") as mark_consumed, \
            patch.object(self.worker, "record_worker_event", side_effect=lambda *_args, **kwargs: events.append(kwargs)):
            self.worker.process_worker_message(
                "http://127.0.0.1:8788",
                "main_text",
                message,
                {"assistant_id": "main_text", "kind": "api", "enabled": True},
                dry_run=False,
                no_post=False,
                transport="route_bus",
            )

        post_reply.assert_not_called()
        mark_consumed.assert_called_once()
        self.assertTrue(message["_awaiting_tool_result"])
        lifecycles = [event.get("metadata", {}).get("lifecycle") for event in events]
        self.assertIn("awaiting_tool_result", lifecycles)
        self.assertNotIn("reply_posting", lifecycles)
        self.assertNotIn("acked", lifecycles)

    def test_local_managed_agent_synthesizes_api_assistant(self):
        assistant = self.worker.load_external_assistant("missing-agent-management.json", "programming")

        self.assertEqual(assistant["assistant_id"], "programming")
        self.assertEqual(assistant["kind"], "api")
        self.assertTrue(assistant["enabled"])
        self.assertTrue(assistant["local_agent"])
        self.assertEqual(assistant["domain"], "programming")
        self.assertIn("code_edit", assistant["capabilities"])

    def test_post_worker_failure_reply_marks_collaboration_failure_visible(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-failed",
            "sender": "human_desktop",
            "recipient": "codex",
            "message_type": "question",
            "content": "@Codex 你在吗",
            "context_id": "route-thread",
            "task_id": "task-route",
        }

        with patch.object(self.worker, "post_reply") as post_reply:
            self.worker.post_worker_failure_reply(
                "http://127.0.0.1:8788",
                "codex",
                message,
                "assistant command failed",
            )

        post_reply.assert_called_once()
        args = post_reply.call_args.args
        self.assertEqual(args[1], "codex")
        self.assertEqual(args[2], message)
        self.assertIn("codex 暂时无法处理这条协作消息", args[3])
        self.assertIn("assistant command failed", args[3])
        self.assertIn("不是主模型回复", args[3])

    def test_worker_records_route_bus_worker_event_payload(self):
        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-event",
            "sender": "codex",
            "recipient": "claude_code",
            "message_type": "question",
            "content": "event payload",
            "context_id": "route-thread",
            "task_id": "task-route",
        }

        with patch.object(self.worker, "request_json", return_value={"ok": True}) as request_json:
            self.worker.record_worker_event(
                "http://127.0.0.1:8788",
                "Claude Code",
                message,
                status="failed",
                transport="route_bus",
                dry_run=False,
                error="assistant is not enabled",
            )

        request_json.assert_called_once_with(
            "http://127.0.0.1:8788",
            "/desktop/collaboration",
            {
                "action": "record_agent_route_bus_worker_event",
                "agent": "claude_code",
                "status": "failed",
                "message_id": "agentmsg-event",
                "thread_id": "route-thread",
                "task_id": "task-route",
                "transport": "route_bus",
                "dry_run": False,
                "error": "assistant is not enabled",
                "metadata": {
                    "script": "collaboration_agent_worker",
                    "sender": "codex",
                    "message_type": "question",
                    "stream": "",
                    "output": "",
                },
            },
        )

    def test_worker_event_recording_is_best_effort(self):
        with patch.object(self.worker, "request_json", side_effect=RuntimeError("gateway unavailable")) as request_json, patch("sys.stderr"):
            self.worker.record_worker_event(
                "http://127.0.0.1:8788",
                "claude_code",
                {},
                status="idle",
                transport="route_bus",
                dry_run=True,
            )

        request_json.assert_called_once()

    def test_worker_event_recording_skips_legacy_transport(self):
        with patch.object(self.worker, "request_json") as request_json:
            self.worker.record_worker_event(
                "http://127.0.0.1:8788",
                "claude_code",
                {"message_id": "message-legacy"},
                status="processed",
                transport="legacy_inbox",
                dry_run=True,
            )

        request_json.assert_not_called()

    def test_retry_recovers_after_transient_failure(self):
        message = {"message_id": "agentmsg-retry", "sender": "codex"}
        calls: list[int] = []

        def flaky(*_args, **_kwargs):
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("transient boom")
            return "recovered"

        with patch.object(self.worker, "process_worker_message", side_effect=flaky), \
            patch.object(self.worker, "record_worker_event") as record:
            reply = self.worker.process_worker_message_with_retry(
                "http://127.0.0.1:8788",
                "claude_code",
                message,
                {"assistant_id": "claude_code", "command": "python", "enabled": True},
                dry_run=False,
                no_post=False,
                transport="route_bus",
                max_attempts=3,
                retry_backoff=0.0,
            )

        self.assertEqual(reply, "recovered")
        self.assertEqual(len(calls), 2)
        retry_events = [c for c in record.call_args_list if c.kwargs.get("metadata", {}).get("lifecycle") == "retry"]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(retry_events[0].kwargs["metadata"]["attempt"], 1)

    def test_retry_reraises_after_exhausting_attempts(self):
        message = {"message_id": "agentmsg-retry-fail", "sender": "codex"}

        with patch.object(self.worker, "process_worker_message", side_effect=RuntimeError("still broken")), \
            patch.object(self.worker, "record_worker_event"):
            with self.assertRaisesRegex(RuntimeError, "still broken"):
                self.worker.process_worker_message_with_retry(
                    "http://127.0.0.1:8788",
                    "claude_code",
                    message,
                    {"assistant_id": "claude_code", "command": "python", "enabled": True},
                    dry_run=False,
                    no_post=False,
                    transport="route_bus",
                    max_attempts=2,
                    retry_backoff=0.0,
                )

    def test_configuration_error_is_not_retried(self):
        message = {"message_id": "agentmsg-config", "sender": "codex"}

        with patch.object(
            self.worker,
            "process_worker_message",
            side_effect=self.worker.WorkerConfigurationError("assistant is not enabled"),
        ) as process, patch.object(self.worker, "record_worker_event") as record:
            with self.assertRaisesRegex(self.worker.WorkerConfigurationError, "not enabled"):
                self.worker.process_worker_message_with_retry(
                    "http://127.0.0.1:8788",
                    "claude_code",
                    message,
                    {"assistant_id": "claude_code", "command": "", "enabled": False},
                    dry_run=False,
                    no_post=False,
                    transport="route_bus",
                    max_attempts=5,
                    retry_backoff=0.0,
                )

        self.assertEqual(process.call_count, 1)
        retry_events = [c for c in record.call_args_list if c.kwargs.get("metadata", {}).get("lifecycle") == "retry"]
        self.assertEqual(retry_events, [])

    def test_should_skip_model_message_gates_on_auto_reply_switch(self):
        model_message = {"message_id": "agentmsg-model", "sender": "codex"}
        human_message = {"message_id": "agentmsg-human", "sender": "human_desktop"}
        tool_result = {
            "message_id": "agentmsg-tool-result",
            "sender": "executor_local_pc",
            "message_type": "event",
            "parent_message_id": "agentmsg-human",
        }

        self.assertTrue(self.worker.should_skip_model_message(model_message, auto_reply=False))
        self.assertFalse(self.worker.should_skip_model_message(model_message, auto_reply=True))
        self.assertFalse(self.worker.should_skip_model_message(human_message, auto_reply=False))
        self.assertFalse(self.worker.should_skip_model_message(tool_result, auto_reply=False))
        self.assertEqual(self.worker.tool_call_origin_message_id(tool_result), "agentmsg-human")

    def test_only_explicit_public_reasoning_contract_is_a_visible_process_stream(self):
        self.assertEqual(self.worker.provider_reasoning_visibility("lmstudio", "qwen3"), "private")
        self.assertEqual(self.worker.provider_reasoning_visibility("openai", "deepseek-reasoner"), "process")
        self.assertEqual(self.worker.provider_reasoning_visibility("openai", "gpt-5"), "private")

    def test_reasoning_visibility_uses_resolved_stream_provider(self):
        metadata = self.worker.stream_token_metadata(
            {
                "provider": "lmstudio",
                "model": "qwen/qwen3.6-35b-a3b",
                "source": "provider_stream",
                "channel": "reasoning",
            },
            "reasoning",
            "openai_compatible",
            "qwen/qwen3.6-35b-a3b",
        )

        self.assertEqual(metadata["provider"], "lmstudio")
        self.assertEqual(metadata["reasoning_visibility"], "private")

    def test_prioritize_worker_messages_processes_humans_before_auto_replies(self):
        messages = [
            {"message_id": "m-model-old", "sender": "model_deepseek", "created_at": 1.0},
            {"message_id": "m-human-new", "sender": "human_desktop", "created_at": 3.0},
            {"message_id": "m-human-old", "sender": "human_desktop", "created_at": 2.0},
            {"message_id": "m-model-new", "sender": "codex", "created_at": 4.0},
        ]

        ordered = self.worker.prioritize_worker_messages(messages)

        self.assertEqual([item["message_id"] for item in ordered], ["m-human-old", "m-human-new", "m-model-old", "m-model-new"])

    def test_preempt_inserts_new_human_at_front(self):
        from collections import deque

        pending = deque(
            [
                {"message_id": "m-model-a", "sender": "codex", "created_at": 1.0},
                {"message_id": "m-model-b", "sender": "model_deepseek", "created_at": 2.0},
            ]
        )
        latest = [
            {"message_id": "m-human-x", "sender": "human_desktop", "created_at": 3.0},
            {"message_id": "m-model-c", "sender": "codex", "created_at": 4.0},
        ]
        with patch.object(self.worker, "list_worker_messages", return_value=latest):
            self.worker.preempt_human_messages(
                "http://api", "main_text", "", "", transport="route_bus", limit=20, pending=pending, seen=set()
            )

        self.assertEqual(pending[0]["message_id"], "m-human-x")
        # 模型消息不插队；批内原有顺序不变。
        self.assertEqual([item["message_id"] for item in pending], ["m-human-x", "m-model-a", "m-model-b"])

    def test_preempt_orders_multiple_humans_first_come_first_served(self):
        from collections import deque

        pending = deque([{"message_id": "m-model-a", "sender": "codex", "created_at": 1.0}])
        latest = [
            {"message_id": "m-human-late", "sender": "human_desktop", "created_at": 5.0},
            {"message_id": "m-human-early", "sender": "human_desktop", "created_at": 3.0},
        ]
        with patch.object(self.worker, "list_worker_messages", return_value=latest):
            self.worker.preempt_human_messages(
                "http://api", "main_text", "", "", transport="route_bus", limit=20, pending=pending, seen=set()
            )

        self.assertEqual(
            [item["message_id"] for item in pending],
            ["m-human-early", "m-human-late", "m-model-a"],
        )

    def test_preempt_skips_seen_and_pending_duplicates(self):
        from collections import deque

        pending = deque(
            [
                {"message_id": "m-model-a", "sender": "codex", "created_at": 1.0},
                {"message_id": "m-human-inbatch", "sender": "human_desktop", "created_at": 2.0},
            ]
        )
        # 队首不是人类（人类消息在批内第 2 位——正常情况下 prioritize 已排前，这里故意构造）→ 会重拉。
        latest = [
            {"message_id": "m-human-seen", "sender": "human_desktop", "created_at": 3.0},
            {"message_id": "m-human-inbatch", "sender": "human_desktop", "created_at": 2.0},
            {"message_id": "", "sender": "human_desktop", "created_at": 4.0},
        ]
        with patch.object(self.worker, "list_worker_messages", return_value=latest):
            self.worker.preempt_human_messages(
                "http://api",
                "main_text",
                "",
                "",
                transport="route_bus",
                limit=20,
                pending=pending,
                seen={"m-human-seen"},
            )

        # 已处理过 / 已在批内 / 无 id 的都不插队。
        self.assertEqual(
            [item["message_id"] for item in pending],
            ["m-model-a", "m-human-inbatch"],
        )

    def test_preempt_noop_when_head_is_human(self):
        from collections import deque

        pending = deque(
            [
                {"message_id": "m-human-a", "sender": "human_desktop", "created_at": 1.0},
                {"message_id": "m-model-b", "sender": "codex", "created_at": 2.0},
            ]
        )
        with patch.object(self.worker, "list_worker_messages") as mock_list:
            self.worker.preempt_human_messages(
                "http://api", "main_text", "", "", transport="route_bus", limit=20, pending=pending, seen=set()
            )

        mock_list.assert_not_called()
        self.assertEqual([item["message_id"] for item in pending], ["m-human-a", "m-model-b"])

    def test_preempt_survives_list_failure(self):
        from collections import deque

        pending = deque([{"message_id": "m-model-a", "sender": "codex", "created_at": 1.0}])
        with patch.object(self.worker, "list_worker_messages", side_effect=RuntimeError("boom")):
            self.worker.preempt_human_messages(
                "http://api", "main_text", "", "", transport="route_bus", limit=20, pending=pending, seen=set()
            )

        self.assertEqual([item["message_id"] for item in pending], ["m-model-a"])

    def test_reply_recipients_fans_out_to_other_participants(self):
        parent = {
            "message_id": "agentmsg-1",
            "sender": "human_desktop",
            "to_agents": ["main_text", "model_deepseek"],
        }

        self.assertEqual(
            self.worker.reply_recipients("main_text", parent),
            ["human_desktop", "model_deepseek"],
        )

    def test_reply_recipients_excludes_self_and_all_and_defaults_to_human(self):
        parent = {"message_id": "agentmsg-2", "sender": "codex", "to_agents": ["codex", "all", "claude_code"]}
        self.assertEqual(self.worker.reply_recipients("claude_code", parent), ["codex"])

        self.assertEqual(self.worker.reply_recipients("main_text", {"message_id": "agentmsg-3"}), ["human_desktop"])

    def test_reply_recipients_reads_envelope_metadata_targets(self):
        parent = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-4",
            "sender": "human_desktop",
            "recipient": "main_text",
            "metadata": {"to_agents": ["main_text", "model_deepseek"]},
        }

        self.assertEqual(
            self.worker.reply_recipients("main_text", parent),
            ["human_desktop", "model_deepseek"],
        )

    def test_failure_reply_only_targets_human_never_models(self):
        # 失败回执若扇出给其他模型，双工开启时会形成“追问→再失败”的刷屏循环。
        from_human = {"message_id": "agentmsg-f1", "sender": "human_desktop", "to_agents": ["main_text", "model_deepseek"]}
        self.assertEqual(self.worker.failure_reply_recipients("model_deepseek", from_human), ["human_desktop"])

        from_model = {"message_id": "agentmsg-f2", "sender": "main_text", "to_agents": ["model_deepseek", "human_desktop"]}
        self.assertEqual(self.worker.failure_reply_recipients("model_deepseek", from_model), ["human_desktop"])

    def test_reply_recipients_broadcast_clamps_to_humans_only(self):
        # 13 收件人广播（实测事故）：逐一回敬会形成 N² 消息风暴，只回人类。
        parent = {
            "message_id": "agentmsg-b1",
            "sender": "human_desktop",
            "to_agents": [
                "claude_code", "codex", "skill_runner", "main_text", "game_development",
                "ecommerce", "programming", "vision_model", "video_animation",
            ],
        }
        self.assertEqual(self.worker.reply_recipients("main_text", parent), ["human_desktop"])

    def test_reply_recipients_prefers_current_session_participants(self):
        # 双工回复只发当前会话参与者：即使父消息是历史广播（13 收件人），
        # 线程最近一条人类消息的收件人才是会话成员的权威快照。
        parent = {
            "message_id": "agentmsg-s1",
            "sender": "model_deepseek",
            "to_agents": [
                "claude_code", "codex", "skill_runner", "main_text", "game_development",
                "ecommerce", "programming", "vision_model", "video_animation",
            ],
        }
        self.assertEqual(
            self.worker.reply_recipients("main_text", parent, ["main_text", "model_deepseek"]),
            ["model_deepseek"],
        )
        human_parent = {"message_id": "agentmsg-s2", "sender": "human_desktop", "to_agents": ["all"]}
        self.assertEqual(
            self.worker.reply_recipients("main_text", human_parent, ["main_text", "model_deepseek"]),
            ["human_desktop", "model_deepseek"],
        )

    def test_fetch_thread_participants_uses_latest_human_message(self):
        listing = {
            "ok": True,
            "messages": [
                {"message_id": "m1", "from_agent": "human_desktop", "to_agents": ["main_text", "model_deepseek", "programming"]},
                {"message_id": "m2", "from_agent": "model_deepseek", "to_agents": ["main_text", "codex", "vision_model", "ecommerce"]},
                {"message_id": "m3", "from_agent": "human_desktop", "to_agents": ["main_text", "model_deepseek"]},
                {"message_id": "m4", "from_agent": "main_text", "to_agents": ["human_desktop", "model_deepseek"]},
            ],
        }
        with patch.object(self.worker, "request_json", return_value=listing):
            self.assertEqual(
                self.worker.fetch_thread_participants("api", "session-x"),
                ["main_text", "model_deepseek"],
            )
        with patch.object(self.worker, "request_json", side_effect=RuntimeError("down")):
            self.assertEqual(self.worker.fetch_thread_participants("api", "session-x"), [])
        self.assertEqual(self.worker.fetch_thread_participants("api", ""), [])

    def test_fetch_thread_history_formats_recent_messages(self):
        listing = {
            "ok": True,
            "messages": [
                {"message_id": "m1", "from_agent": "human_desktop", "content": "请评估这个方案"},
                {"message_id": "m2", "from_agent": "model_deepseek", "content": "方案可行，但建议先补测试"},
                {"message_id": "m3", "from_agent": "main_text", "content": "当前这条来件"},
            ],
        }
        with patch.object(self.worker, "request_json", return_value=listing):
            history = self.worker.fetch_thread_history("api", "session-x", exclude_message_id="m3")
        self.assertIn("[human_desktop] 请评估这个方案", history)
        self.assertIn("[model_deepseek] 方案可行，但建议先补测试", history)
        self.assertNotIn("当前这条来件", history)

        with patch.object(self.worker, "request_json", side_effect=RuntimeError("down")):
            self.assertEqual(self.worker.fetch_thread_history("api", "session-x"), "")
        self.assertEqual(self.worker.fetch_thread_history("", "session-x"), "")
        self.assertEqual(self.worker.fetch_thread_history("api", ""), "")

    def test_build_prompt_includes_history_and_model_to_model_guidance(self):
        model_message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-duplex",
            "sender": "model_deepseek",
            "recipient": "main_text",
            "message_type": "answer",
            "content": "我认为应先修 worker 重试逻辑。",
            "context_id": "route-thread",
        }
        prompt = self.worker.build_prompt(model_message, history="[human_desktop] 请两位讨论 worker 重试策略")
        self.assertIn("another AI participant (model_deepseek)", prompt)
        self.assertIn("Do NOT reply with pleasantries", prompt)
        self.assertIn("Recent thread messages", prompt)
        self.assertIn("[human_desktop] 请两位讨论 worker 重试策略", prompt)
        self.assertIn("Latest incoming message you must respond to:", prompt)

        human_message = dict(model_message, sender="human_desktop", message_type="question")
        human_prompt = self.worker.build_prompt(human_message)
        self.assertNotIn("another AI participant", human_prompt)
        self.assertNotIn("Recent thread messages", human_prompt)

    def test_is_context_overflow_error_matches_known_markers(self):
        self.assertTrue(self.worker.is_context_overflow_error("n_keep: 9143 >= n_ctx: 4096"))
        self.assertTrue(self.worker.is_context_overflow_error("This model's maximum context length is 4096 tokens"))
        self.assertTrue(self.worker.is_context_overflow_error("Prompt is too long: exceeds context window"))
        self.assertFalse(self.worker.is_context_overflow_error("connection refused"))
        self.assertFalse(self.worker.is_context_overflow_error(""))
        self.assertFalse(self.worker.is_context_overflow_error(None))

    def test_build_compact_prompt_skips_brief_snapshot_and_trims_history(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("SpiritKin compact test", encoding="utf-8")
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-compact",
                "sender": "human_desktop",
                "recipient": "main_text",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请检查仓库。",
                "context_id": "route-thread",
            }
            long_history = "旧" * 2000 + "[human_desktop] 最新一段历史"

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SPIRITKIN_DISABLE_COLLABORATION_CONTEXT", None)
                full_prompt = self.worker.build_prompt(message, history=long_history)
                compact_prompt = self.worker.build_compact_prompt(message, None, long_history)

        self.assertIn("Repository snapshot:", full_prompt)
        self.assertIn("docs/ai_collaboration_context.md", full_prompt)
        self.assertNotIn("Repository snapshot:", compact_prompt)
        self.assertNotIn("docs/ai_collaboration_context.md", compact_prompt)
        # 历史被截尾保留最新 1500 字符：最新片段在，最早片段不在。
        self.assertIn("[human_desktop] 最新一段历史", compact_prompt)
        self.assertNotIn("旧" * 1600, compact_prompt)
        self.assertLess(len(compact_prompt), len(full_prompt))

    def test_reply_recipients_small_group_still_fans_out(self):
        parent = {
            "message_id": "agentmsg-b2",
            "sender": "human_desktop",
            "to_agents": ["main_text", "model_deepseek", "programming"],
        }
        self.assertEqual(
            self.worker.reply_recipients("main_text", parent),
            ["human_desktop", "model_deepseek", "programming"],
        )

    def test_post_reply_suppresses_gateway_policy_rejections(self):
        # turn_cap/auto-reply 闸门拒收是策略行为：静默丢弃，不能升级成失败回执。
        parent = {"message_id": "agentmsg-p1", "sender": "human_desktop"}
        with patch.object(
            self.worker,
            "request_json",
            side_effect=RuntimeError("HTTP 400: turn_cap_reached: automatic model-to-model reply paused"),
        ):
            self.worker.post_reply("api", "main_text", parent, "hello")  # 不应抛异常

        with patch.object(self.worker, "request_json", side_effect=RuntimeError("HTTP 500: boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.worker.post_reply("api", "main_text", parent, "hello")

    def test_post_reply_uses_predictable_message_id_for_streaming_draft_replacement(self):
        parent = {"message_id": "agentmsg-predict", "sender": "human_desktop", "thread_id": "thread-a"}
        with patch.object(self.worker, "request_json", return_value={"ok": True}) as request:
            self.worker.post_reply("api", "main_text", parent, "hello", recipients=["human_desktop"])

        payload = request.call_args.args[2]
        self.assertEqual(payload["message_id"], "reply-main_text-agentmsg-predict")
        self.assertEqual(payload["parent_message_id"], "agentmsg-predict")

    def test_tool_result_reply_reuses_origin_and_excludes_executor_recipient(self):
        parent = {
            "message_id": "tool-result-message",
            "sender": "executor_local_pc",
            "message_type": "event",
            "parent_message_id": "agentmsg-tool-origin",
            "thread_id": "thread-a",
        }
        with patch.object(self.worker, "request_json", return_value={"ok": True}) as request:
            self.worker.post_reply(
                "api",
                "main_text",
                parent,
                "done",
                recipients=["human_desktop", "executor_local_pc"],
            )

        payload = request.call_args.args[2]
        self.assertEqual(payload["message_id"], "reply-main_text-agentmsg-tool-origin")
        self.assertEqual(payload["parent_message_id"], "agentmsg-tool-origin")
        self.assertEqual(payload["to_agents"], ["human_desktop"])

    def test_tool_result_worker_event_reuses_origin_message_id(self):
        message = {
            "message_id": "tool-result-message",
            "sender": "executor_local_pc",
            "message_type": "event",
            "parent_message_id": "agentmsg-tool-origin",
            "thread_id": "thread-a",
        }
        with patch.object(self.worker, "request_json", return_value={"ok": True}) as request:
            self.worker.record_worker_event(
                "api",
                "main_text",
                message,
                status="started",
                transport="route_bus",
                dry_run=False,
            )

        payload = request.call_args.args[2]
        self.assertEqual(payload["message_id"], "agentmsg-tool-origin")

    def test_post_reply_filters_invalid_recipient_without_regenerating(self):
        parent = {"message_id": "agentmsg-filter", "sender": "human_desktop", "thread_id": "thread-a"}
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise RuntimeError("HTTP 400: recipient_not_allowed:executor_local_pc")
            return {"ok": True}

        with patch.object(self.worker, "request_json", side_effect=fake_request), \
            patch.object(self.worker, "record_worker_event"):
            self.worker.post_reply(
                "api",
                "main_text",
                parent,
                "hello",
                recipients=["human_desktop", "executor_local_pc"],
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["message_id"], calls[1]["message_id"])
        self.assertEqual(calls[1]["to_agents"], ["human_desktop"])

    def test_post_reply_retries_transport_with_same_message_id(self):
        parent = {"message_id": "agentmsg-transport", "sender": "human_desktop", "thread_id": "thread-a"}
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise urllib.error.URLError("response lost")
            return {"ok": True}

        with patch.object(self.worker, "request_json", side_effect=fake_request), \
            patch.object(self.worker.time, "sleep"):
            self.worker.post_reply("api", "main_text", parent, "hello", recipients=["human_desktop"])

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["message_id"], calls[1]["message_id"])

    def test_worker_message_claim_prevents_parallel_execution(self):
        with TemporaryDirectory() as tmp, patch.object(
            self.worker,
            "resolve_collaboration_root",
            return_value=Path(tmp),
        ):
            message = {"message_id": "agentmsg-claim"}
            first = self.worker.try_acquire_worker_message_claim("main_text", message)
            second = self.worker.try_acquire_worker_message_claim("main_text", message)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.worker.release_worker_message_claim(first)
            third = self.worker.try_acquire_worker_message_claim("main_text", message)
            self.assertIsNotNone(third)
            self.worker.release_worker_message_claim(third)

    def test_worker_message_claim_recovers_immediately_after_owner_exit(self):
        with TemporaryDirectory() as tmp, patch.object(
            self.worker,
            "resolve_collaboration_root",
            return_value=Path(tmp),
        ):
            message = {"message_id": "agentmsg-dead-claim"}
            abandoned = self.worker.try_acquire_worker_message_claim("main_text", message)
            self.assertIsNotNone(abandoned)
            with patch.object(self.worker, "process_id_is_running", return_value=False):
                replacement = self.worker.try_acquire_worker_message_claim("main_text", message)
            self.assertIsNotNone(replacement)
            self.worker.release_worker_message_claim(replacement)

    def test_read_only_tool_call_is_submitted_and_executed_immediately(self):
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            if payload["action"] == "request_tool_call":
                return {
                    "agent_route_bus_tool_call": {
                        "requires_review": False,
                        "tool_call": {
                            "tool_call_id": "tool-read-1",
                            "requires_review": False,
                            "status": "approved",
                        },
                    }
                }
            return {"ok": True}

        reply = '```json\n{"spiritkin_tool_call":{"target":"local_pc","operation":"screen_understand","params":{"query":"当前屏幕"}}}\n```'
        with patch.object(self.worker, "request_json", side_effect=fake_request):
            submitted = self.worker.submit_tool_calls_from_reply(
                "api", "main_text", {"message_id": "m-tool", "thread_id": "thread-tool"}, reply,
                transport="route_bus", dry_run=False,
            )

        self.assertEqual(submitted, 1)
        self.assertEqual([call["action"] for call in calls], ["request_tool_call", "execute_tool_call"])
        self.assertEqual(calls[1]["tool_call_id"], "tool-read-1")
        self.assertNotIn("spiritkin_tool_call", self.worker.strip_submitted_tool_call_payloads(reply, submitted))

    def test_tool_call_forwards_full_access_from_original_message(self):
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            if payload["action"] == "request_tool_call":
                return {
                    "agent_route_bus_tool_call": {
                        "requires_review": False,
                        "tool_call": {"tool_call_id": "tool-full-access", "requires_review": False},
                    }
                }
            return {"ok": True}

        message = {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": "agentmsg-full-access",
            "sender": "human_desktop",
            "recipient": "main_text",
            "message_type": "question",
            "content": "打开命令提示符",
            "context_id": "thread-full-access",
            "metadata": {"permission_mode": "full_access", "full_access_granted": True},
        }
        reply = '{"spiritkin_tool_call":{"target":"local_pc","operation":"launch_app","params":{"app_name":"cmd"}}}'
        with patch.object(self.worker, "request_json", side_effect=fake_request):
            submitted = self.worker.submit_tool_calls_from_reply(
                "api", "main_text", message, reply, transport="route_bus", dry_run=False
            )

        self.assertEqual(submitted, 1)
        self.assertEqual(calls[0]["metadata"]["permission_mode"], "full_access")
        self.assertTrue(calls[0]["metadata"]["full_access_granted"])
        self.assertEqual([call["action"] for call in calls], ["request_tool_call", "execute_tool_call"])

    def test_launch_app_normalizes_model_command_alias_to_app_name(self):
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            return {
                "agent_route_bus_tool_call": {
                    "requires_review": True,
                    "tool_call": {"tool_call_id": "tool-cmd", "requires_review": True},
                }
            }

        reply = '{"spiritkin_tool_call":{"target":"local_pc","operation":"launch_app","params":{"command":"cmd"}}}'
        with patch.object(self.worker, "request_json", side_effect=fake_request):
            submitted = self.worker.submit_tool_calls_from_reply(
                "api", "main_text", {"message_id": "m-cmd", "thread_id": "thread-cmd"}, reply,
                transport="route_bus", dry_run=False,
            )

        self.assertEqual(submitted, 1)
        self.assertEqual(calls[0]["params"]["command"], "cmd")
        self.assertEqual(calls[0]["params"]["app_name"], "cmd")

    def test_deduplicated_tool_result_continuation_does_not_execute_again(self):
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            return {
                "agent_route_bus_tool_call": {
                    "requested": False,
                    "deduplicated": True,
                    "requires_review": False,
                    "tool_call": {
                        "tool_call_id": "tool-read-1",
                        "requires_review": False,
                        "status": "completed",
                    },
                }
            }

        message = {
            "message_id": "tool-result-message",
            "sender": "executor_local_pc",
            "message_type": "event",
            "parent_message_id": "m-tool-root",
            "thread_id": "thread-tool",
        }
        reply = '{"spiritkin_tool_call":{"target":"local_pc","operation":"screen_understand","params":{"query":"当前屏幕"}}}'
        with patch.object(self.worker, "request_json", side_effect=fake_request):
            submitted = self.worker.submit_tool_calls_from_reply(
                "api", "main_text", message, reply, transport="route_bus", dry_run=False
            )

        self.assertEqual(submitted, 1)
        self.assertEqual([call["action"] for call in calls], ["request_tool_call"])
        self.assertEqual(calls[0]["message_id"], "m-tool-root")

    def test_inline_tool_call_after_text_is_extracted_and_removed(self):
        reply = (
            "收到，正在打开百度。\n"
            '{"spiritkin_tool_call":{"target":"local_pc","operation":"browser_open_url",'
            '"params":{"url":"https://www.baidu.com"},"reason":"打开百度"}}'
        )

        calls = self.worker.extract_tool_calls(reply)
        cleaned = self.worker.strip_submitted_tool_call_payloads(reply, 1)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["operation"], "browser_open_url")
        self.assertEqual(calls[0]["params"]["url"], "https://www.baidu.com")
        self.assertEqual(cleaned, "收到，正在打开百度。")


    def test_state_changing_tool_call_waits_for_human_approval(self):
        calls = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            return {
                "agent_route_bus_tool_call": {
                    "requires_review": True,
                    "tool_call": {
                        "tool_call_id": "tool-write-1",
                        "requires_review": True,
                        "status": "permission_required",
                    },
                }
            }

        reply = '{"spiritkin_tool_call":{"target":"local_pc","operation":"click_pointer","params":{"x":10,"y":20}}}'
        with patch.object(self.worker, "request_json", side_effect=fake_request):
            submitted = self.worker.submit_tool_calls_from_reply(
                "api", "main_text", {"message_id": "m-tool", "thread_id": "thread-tool"}, reply,
                transport="route_bus", dry_run=False,
            )

        self.assertEqual(submitted, 1)
        self.assertEqual([call["action"] for call in calls], ["request_tool_call"])

    def test_post_reply_records_lifecycle_when_gateway_rejects_route_bus_reply(self):
        parent = {"message_id": "agentmsg-p1", "sender": "model_deepseek", "thread_id": "thread-a", "task_id": "task-a"}
        def fake_request(_api, _path, payload):
            if payload["action"] == "post_message":
                raise RuntimeError("HTTP 400: auto_reply_disabled: automatic model-to-model replies are off")
            return {"ok": True}

        with patch.object(
            self.worker,
            "request_json",
            side_effect=fake_request,
        ) as request:
            self.worker.post_reply("api", "main_text", parent, "hello", recipients=["human_desktop"], transport="route_bus")

        self.assertEqual(request.call_count, 2)
        event_payload = request.call_args_list[1].args[2]
        self.assertEqual(event_payload["action"], "record_agent_route_bus_worker_event")
        self.assertEqual(event_payload["status"], "stream")
        self.assertEqual(event_payload["metadata"]["lifecycle"], "auto_reply_disabled")
        self.assertEqual(event_payload["metadata"]["stream"], "lifecycle")

    def test_post_reply_redelivers_to_human_when_model_only_recipients_rejected(self):
        """2026-07-09：双工关掉时纯 model→model 回复被拒，改投人类收件人重发（回复不能凭空消失）。"""
        parent = {"message_id": "agentmsg-p2", "sender": "model_deepseek", "thread_id": "thread-a", "task_id": "task-a"}
        calls: list[dict] = []

        def fake_request(_api, _path, payload):
            calls.append(payload)
            if payload["action"] == "post_message" and payload["to_agents"] == ["model_deepseek"]:
                raise RuntimeError("HTTP 400: auto_reply_disabled: automatic model-to-model replies are off")
            return {"ok": True}

        with patch.object(self.worker, "request_json", side_effect=fake_request):
            self.worker.post_reply(
                "api", "main_text", parent, "hello",
                recipients=["model_deepseek"], transport="route_bus",
            )

        posts = [item for item in calls if item["action"] == "post_message"]
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[1]["to_agents"], ["human_desktop"])
        self.assertEqual(posts[1]["content"], "hello")

    def test_stale_messages_are_skipped_not_replayed(self):
        now = 1_783_240_000.0
        fresh = {"message_id": "m1", "created_at": now - 60}
        stale = {"message_id": "m2", "created_at": now - 7200}
        missing = {"message_id": "m3"}

        self.assertFalse(self.worker.is_stale_message(fresh, now=now))
        self.assertTrue(self.worker.is_stale_message(stale, now=now))
        self.assertFalse(self.worker.is_stale_message(missing, now=now))

        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_MAX_MESSAGE_AGE": "30"}, clear=False):
            self.assertTrue(self.worker.is_stale_message(fresh, now=now))

    def test_self_heal_request_gated_by_env_switch_and_threshold(self):
        parent = {"message_id": "agentmsg-5", "sender": "human_desktop"}

        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SELF_HEAL": ""}, clear=False):
            with patch.object(self.worker, "request_json") as request:
                self.assertFalse(self.worker.maybe_post_self_heal_request("api", "main_text", parent, "boom", 5))
                request.assert_not_called()

        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SELF_HEAL": "1"}, clear=False):
            with patch.object(self.worker, "request_json") as request:
                self.assertFalse(self.worker.maybe_post_self_heal_request("api", "main_text", parent, "boom", 1))
                request.assert_not_called()

                self.assertTrue(self.worker.maybe_post_self_heal_request("api", "main_text", parent, "boom", 2))
                payload = request.call_args.args[2]
                self.assertEqual(payload["role"], "question")
                self.assertEqual(payload["from_agent"], "main_text")
                self.assertEqual(payload["to_agents"], ["programming"])
                self.assertIn("boom", payload["content"])

    def test_self_heal_diagnostic_agent_never_targets_self(self):
        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SELF_HEAL_AGENT": "codex"}, clear=False):
            self.assertEqual(self.worker.collaboration_self_heal_agent("main_text"), "codex")
            self.assertEqual(self.worker.collaboration_self_heal_agent("codex"), "main_text")
        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SELF_HEAL_AGENT": ""}, clear=False):
            self.assertEqual(self.worker.collaboration_self_heal_agent("codex"), "main_text")
            self.assertEqual(self.worker.collaboration_self_heal_agent("main_text"), "programming")

    def test_should_wake_for_event_matches_recipients(self):
        def event(payload):
            return {"type": "collaboration.message", "payload": payload}

        self.assertTrue(self.worker.should_wake_for_event(event({"from_agent": "codex", "to_agents": ["claude_code"]}), "claude_code"))
        self.assertTrue(self.worker.should_wake_for_event(event({"from_agent": "codex", "to_agents": ["all"]}), "claude_code"))
        self.assertTrue(self.worker.should_wake_for_event(event({"from_agent": "codex", "to_agents": "claude_code"}), "claude_code"))
        # Broadcast with no explicit recipients wakes everyone.
        self.assertTrue(self.worker.should_wake_for_event(event({"from_agent": "codex"}), "claude_code"))
        # Not addressed to this agent.
        self.assertFalse(self.worker.should_wake_for_event(event({"from_agent": "codex", "to_agents": ["gemini"]}), "claude_code"))
        # Own messages never wake the sender.
        self.assertFalse(self.worker.should_wake_for_event(event({"from_agent": "claude_code", "to_agents": ["claude_code"]}), "claude_code"))
        # Non-collaboration events are ignored.
        self.assertFalse(self.worker.should_wake_for_event({"type": "avatar.state", "payload": {}}, "claude_code"))
        self.assertFalse(self.worker.should_wake_for_event("not-a-dict", "claude_code"))

    def test_resolve_push_ws_url_prefers_explicit(self):
        self.assertEqual(self.worker.resolve_push_ws_url("ws://10.0.0.5:8765"), "ws://10.0.0.5:8765")


class CollaborationSpeakQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    def test_speak_queue_peers_excludes_self_humans_and_all(self):
        message = {
            "message_id": "msg-q1",
            "to_agents": ["main_text", "model_deepseek", "human_desktop", "all", "main_text"],
        }
        self.assertEqual(self.worker.speak_queue_peers("main_text", message), ["model_deepseek"])
        self.assertEqual(
            self.worker.speak_queue_peers("model_deepseek", message),
            ["main_text"],
        )
        # 单模型收件：没有同伴，不进入队列。
        self.assertEqual(self.worker.speak_queue_peers("main_text", {"to_agents": ["main_text", "human_desktop"]}), [])

    def test_speak_queue_registration_and_mark_posted(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.register_speak_queue_entry("main_text", "msg-q2")
                (queue_dir / "model_deepseek.json").write_text(
                    json.dumps({"agent": "model_deepseek", "enqueued_at": 1.0}),
                    encoding="utf-8",
                )
                self.worker.mark_speak_queue_posted(queue_dir, "model_deepseek")
                entries = {
                    item["agent"]: item for item in self.worker.load_speak_queue_entries(queue_dir)
                }
                self.assertGreater(entries["model_deepseek"]["posted_at"], 0.0)
                self.assertNotIn("posted_at", entries["main_text"])

    def test_withdraw_speak_queue_entry_unblocks_waiters(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.register_speak_queue_entry("model_deepseek", "msg-q2b")
                self.worker.register_speak_queue_entry("main_text", "msg-q2b")
                # 前位生成失败撤出队列后，等待方把该席位视为已完成，立即放行。
                self.worker.withdraw_speak_queue_entry(queue_dir, "model_deepseek")
                started = time.monotonic()
                with patch.object(self.worker, "speak_queue_timeout_seconds", return_value=3.0):
                    self.worker.wait_speak_queue_ahead_posted(queue_dir, ["model_deepseek"])
                self.assertLess(time.monotonic() - started, 1.0)

    def test_build_speak_after_message_embeds_prior_replies(self):
        message = {
            "message_id": "msg-q3",
            "content": "原始问题",
            "agent_envelope": {"content": "原始问题", "sender": "human_desktop"},
        }
        revised = self.worker.build_speak_after_message(
            message, [("model_deepseek", "DeepSeek 定稿")]
        )
        body = self.worker.message_content(revised)
        self.assertIn("原始问题", body)
        self.assertIn("DeepSeek 定稿", body)
        self.assertIn("发言顺序说明", body)
        # 原消息不被就地修改。
        self.assertEqual(self.worker.message_content(message), "原始问题")

    def test_build_speak_after_message_clips_long_replies(self):
        message = {"message_id": "msg-q3b", "content": "原始问题"}
        long_reply = "答" * 4000
        revised = self.worker.build_speak_after_message(
            message, [("model_deepseek", long_reply)]
        )
        body = self.worker.message_content(revised)
        self.assertIn("中间部分已截断", body)
        # 压缩传递：每份定稿保头 70%（1050 字）+ 保尾 30%（450 字），截中间。
        self.assertIn("答" * 1050, body)
        self.assertNotIn("答" * 1051, body)
        self.assertIn("答" * 450, body.split("中间部分已截断", 1)[1])

    def test_salvaged_reply_marking_and_filtering(self):
        message: dict[str, object] = {}
        reply = self.worker.mark_salvaged_reply(message, "思考通道兜底内容")
        self.assertTrue(message.get("_salvaged_reply"))
        self.assertTrue(reply.startswith(self.worker.SALVAGED_REPLY_PREFIX))
        self.assertTrue(self.worker.is_salvaged_reply_content(reply))
        self.assertFalse(self.worker.is_salvaged_reply_content("正常发言"))

    def test_finalize_reply_from_reasoning_regenerates_body(self):
        message = {"message_id": "msg-f1", "content": "请评估这个方案"}
        captured: dict[str, object] = {}

        def fake_review(problem, **kwargs):
            captured["problem"] = problem
            captured["kwargs"] = kwargs
            return SimpleNamespace(ok=True, response_text="<think>草稿</think>基于思考重新生成的正式回复")

        with patch.object(self.worker, "request_model_review", side_effect=fake_review):
            finalized = self.worker.finalize_reply_from_reasoning(
                message,
                "<think>先分析利弊……最终应当支持该方案</think>",
                provider="lmstudio",
                model="qwen3-4b",
                agent="main_text",
            )
        self.assertEqual(finalized, "基于思考重新生成的正式回复")
        # 未打抖救标记：该回复按正常回复扇出。
        self.assertNotIn("_salvaged_reply", message)
        problem = str(captured["problem"])
        self.assertIn("请评估这个方案", problem)
        self.assertIn("最终应当支持该方案", problem)
        self.assertNotIn("<think>", problem)
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["skill_name"], "collaboration:main_text:finalize")

    def test_finalize_reply_from_reasoning_returns_empty_on_failure(self):
        message = {"message_id": "msg-f2", "content": "问题"}
        with patch.object(
            self.worker,
            "request_model_review",
            return_value=SimpleNamespace(ok=False, response_text="", error="boom", status="failed"),
        ):
            self.assertEqual(
                self.worker.finalize_reply_from_reasoning(
                    message, "一些思考", provider="p", model="m", agent="main_text"
                ),
                "",
            )
        with patch.object(self.worker, "request_model_review", side_effect=RuntimeError("down")):
            self.assertEqual(
                self.worker.finalize_reply_from_reasoning(
                    message, "一些思考", provider="p", model="m", agent="main_text"
                ),
                "",
            )
        self.assertEqual(
            self.worker.finalize_reply_from_reasoning(
                message, "   ", provider="p", model="m", agent="main_text"
            ),
            "",
        )

    def test_fetch_round_replies_skips_salvaged_messages(self):
        salvage_body = f"{self.worker.SALVAGED_REPLY_PREFIX}\nCoT tail"

        def fake_request(api, path, payload=None):
            return {
                "messages": [
                    {
                        "message_id": "reply-s1",
                        "parent_message_id": "round-1",
                        "from_agent": "main_text",
                        "content": salvage_body,
                        "thread_id": "thread-s",
                    },
                    {
                        "message_id": "reply-s2",
                        "parent_message_id": "round-1",
                        "from_agent": "model_deepseek",
                        "content": "正式定稿",
                        "thread_id": "thread-s",
                    },
                ]
            }

        with patch.object(self.worker, "request_json", side_effect=fake_request):
            replies = self.worker.fetch_round_replies(
                "http://api", "thread-s", "round-1", ["main_text", "model_deepseek"]
            )
        self.assertEqual(replies, [("model_deepseek", "正式定稿")])

    def test_fetch_thread_history_skips_salvaged_messages(self):
        salvage_body = f"{self.worker.SALVAGED_REPLY_PREFIX}\nCoT tail"

        def fake_request(api, path, payload=None):
            return {
                "messages": [
                    {"message_id": "m1", "from_agent": "main_text", "content": salvage_body},
                    {"message_id": "m2", "from_agent": "human_desktop", "content": "正常提问"},
                ]
            }

        with patch.object(self.worker, "request_json", side_effect=fake_request):
            history = self.worker.fetch_thread_history("http://api", "thread-s")
        self.assertIn("正常提问", history)
        self.assertNotIn("CoT tail", history)

    def test_build_compact_prompt_minimal_drops_history_and_clips_content(self):
        long_content = "头" * 2000 + "【你的草稿】尾部内容" + "尾" * 900
        message = {
            "message_id": "agentmsg-minimal",
            "sender": "human_desktop",
            "recipient": "main_text",
            "message_type": "question",
            "content": long_content,
            "context_id": "route-thread",
        }
        prompt = self.worker.build_compact_prompt(
            message, None, "[human_desktop] 一些历史", minimal=True
        )
        self.assertNotIn("Recent thread messages", prompt)
        self.assertIn("中间部分已截断", prompt)
        self.assertNotIn("头" * 1700, prompt)
        self.assertIn("尾" * 800, prompt)
        # 原消息不被就地修改。
        self.assertEqual(self.worker.message_content(message), long_content)

    def test_local_prompt_soft_limit_env_override(self):
        with patch.dict(os.environ, {"SPIRITKIN_LOCAL_PROMPT_SOFT_LIMIT": "8000"}, clear=False):
            self.assertEqual(self.worker.local_prompt_soft_limit(), 8000)
        with patch.dict(os.environ, {"SPIRITKIN_LOCAL_PROMPT_SOFT_LIMIT": ""}, clear=False):
            self.assertEqual(self.worker.local_prompt_soft_limit(), 3200)

    def test_enter_speak_queue_registers_and_returns_peers(self):
        message = {
            "message_id": "msg-q4",
            "thread_id": "thread-q",
            "to_agents": ["main_text", "model_deepseek"],
            "content": "问题",
        }
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir, peers = self.worker.enter_speak_queue(
                    "http://api", "main_text", message, transport="route_bus"
                )
        self.assertIsNotNone(queue_dir)
        self.assertEqual(peers, ["model_deepseek"])

    def test_claim_speak_slot_first_visible_output_wins(self):
        """v4"谁先想完谁先发言"：先写 speaking_at 的抢到席位，后到者看到前位。"""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.speak_queue_dir("msg-q5")
                self.worker.register_speak_queue_entry("main_text", "msg-q5")
                self.worker.register_speak_queue_entry("model_deepseek", "msg-q5")
                # DS 先想完（先产出可见正文）→ 无前位，现场直播。
                self.assertEqual(
                    self.worker.claim_speak_slot(queue_dir, "model_deepseek", ["main_text"]), []
                )
                # 本地模型后想完 → DS 在前，转后台起草。
                self.assertEqual(
                    self.worker.claim_speak_slot(queue_dir, "main_text", ["model_deepseek"]),
                    ["model_deepseek"],
                )

    def test_claim_speak_slot_keeps_first_writer_when_wall_clock_ties(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.speak_queue_dir("msg-q5-tie")
                self.worker.register_speak_queue_entry("main_text", "msg-q5-tie")
                self.worker.register_speak_queue_entry("model_deepseek", "msg-q5-tie")
                with patch.object(self.worker.time, "time", return_value=123.0):
                    first = self.worker.claim_speak_slot(queue_dir, "model_deepseek", ["main_text"])
                    second = self.worker.claim_speak_slot(queue_dir, "main_text", ["model_deepseek"])

        self.assertEqual(first, [])
        self.assertEqual(second, ["model_deepseek"])

    def test_claim_speak_slot_counts_already_posted_peer_as_ahead(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.speak_queue_dir("msg-q5b")
                queue_dir.mkdir(parents=True, exist_ok=True)
                # 同伴已定稿（没写 speaking_at 的旧条目也算前位）。
                (queue_dir / "model_deepseek.json").write_text(
                    json.dumps({"agent": "model_deepseek", "enqueued_at": 1.0, "posted_at": 2.0}),
                    encoding="utf-8",
                )
                self.worker.register_speak_queue_entry("main_text", "msg-q5b")
                self.assertEqual(
                    self.worker.claim_speak_slot(queue_dir, "main_text", ["model_deepseek"]),
                    ["model_deepseek"],
                )

    def test_speak_slot_lane_routes_draft_when_peer_ahead(self):
        """2026-07-09 起草泳道：抢到席位 token 直播；没抢到走 draft（不再混进 reasoning 思考链）。"""
        message = {"message_id": "msg-q5c", "thread_id": "thread-q", "content": "问题"}
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                with patch.object(self.worker, "record_worker_event"):
                    queue_dir = self.worker.speak_queue_dir("msg-q5c")
                    self.worker.register_speak_queue_entry("model_deepseek", "msg-q5c")
                    self.worker.register_speak_queue_entry("main_text", "msg-q5c")
                    first = self.worker.SpeakSlot(
                        "http://api", "model_deepseek", message, queue_dir, ["main_text"], "model_api"
                    )
                    self.assertEqual(first.lane(), "token")
                    second = self.worker.SpeakSlot(
                        "http://api", "main_text", message, queue_dir, ["model_deepseek"], "local_process"
                    )
                    self.assertEqual(second.lane(), "draft")
                    # lane 幂等：同一轮后续 token 批次维持首判结果。
                    self.assertEqual(second.lane(), "draft")

    def test_revise_with_finalized_replies_queue_path_revises_draft(self):
        """草稿完成后等前位定稿，拿"草稿+定稿"修订一次成稿（修订稿才上屏）。"""
        message = {
            "message_id": "msg-q6",
            "thread_id": "thread-q",
            "to_agents": ["main_text", "model_deepseek"],
            "content": "问题",
        }
        captured: dict[str, object] = {}

        def fake_request(api, path, payload=None):
            if payload and payload.get("action") == "list_messages":
                return {
                    "messages": [
                        {
                            "message_id": "reply-1",
                            "parent_message_id": "msg-q6",
                            "from_agent": "model_deepseek",
                            "content": "DeepSeek 定稿",
                            "thread_id": "thread-q",
                        }
                    ]
                }
            return {"ok": True}

        def fake_assistant(assistant, generation_message, **kwargs):
            captured["content"] = self.worker.message_content(generation_message)
            return "修订后的正式发言"

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.speak_queue_dir("msg-q6")
                queue_dir.mkdir(parents=True, exist_ok=True)
                (queue_dir / "model_deepseek.json").write_text(
                    json.dumps({"agent": "model_deepseek", "enqueued_at": 1.0, "posted_at": 2.0}),
                    encoding="utf-8",
                )
                with patch.object(self.worker, "request_json", side_effect=fake_request), \
                        patch.object(self.worker, "run_external_assistant", side_effect=fake_assistant):
                    revised, turn_lock = self.worker.revise_with_finalized_replies(
                        "http://api",
                        "main_text",
                        message,
                        {"agent_id": "main_text"},
                        "我的并行草稿",
                        queue_dir=queue_dir,
                        queue_ahead=["model_deepseek"],
                        acquire_turn=False,
                        transport="route_bus",
                    )
        self.assertEqual(revised, "修订后的正式发言")
        self.assertIsNone(turn_lock)
        body = str(captured["content"])
        self.assertIn("DeepSeek 定稿", body)
        self.assertIn("我的并行草稿", body)

    def test_revise_with_finalized_replies_falls_back_to_draft_on_failure(self):
        message = {
            "message_id": "msg-q6b",
            "thread_id": "thread-q",
            "to_agents": ["main_text", "model_deepseek"],
            "content": "问题",
        }

        def fake_request(api, path, payload=None):
            if payload and payload.get("action") == "list_messages":
                return {
                    "messages": [
                        {
                            "message_id": "reply-1",
                            "parent_message_id": "msg-q6b",
                            "from_agent": "model_deepseek",
                            "content": "DeepSeek 定稿",
                            "thread_id": "thread-q",
                        }
                    ]
                }
            return {"ok": True}

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                queue_dir = self.worker.speak_queue_dir("msg-q6b")
                queue_dir.mkdir(parents=True, exist_ok=True)
                (queue_dir / "model_deepseek.json").write_text(
                    json.dumps({"agent": "model_deepseek", "enqueued_at": 1.0, "posted_at": 2.0}),
                    encoding="utf-8",
                )
                with patch.object(self.worker, "request_json", side_effect=fake_request), \
                        patch.object(
                            self.worker, "run_external_assistant", side_effect=RuntimeError("model down")
                        ):
                    revised, _ = self.worker.revise_with_finalized_replies(
                        "http://api",
                        "main_text",
                        message,
                        {"agent_id": "main_text"},
                        "我的并行草稿",
                        queue_dir=queue_dir,
                        queue_ahead=["model_deepseek"],
                        acquire_turn=False,
                        transport="route_bus",
                    )
        # 修订失败按草稿发布，不能吞回复。
        self.assertEqual(revised, "我的并行草稿")

    def test_build_speak_after_message_embeds_draft_block(self):
        message = {"message_id": "msg-q6c", "content": "原始问题"}
        revised = self.worker.build_speak_after_message(
            message, [("model_deepseek", "DeepSeek 定稿")], draft="我的思考草稿"
        )
        body = self.worker.message_content(revised)
        self.assertIn("我的思考草稿", body)
        self.assertIn("思考草稿", body)
        self.assertIn("全新的正式发言", body)
        # 不带草稿时无草稿块。
        plain = self.worker.build_speak_after_message(message, [("model_deepseek", "DeepSeek 定稿")])
        self.assertNotIn("思考草稿", self.worker.message_content(plain))

    def test_speak_queue_disabled_by_env_switch(self):
        message = {"message_id": "msg-q7", "to_agents": ["main_text", "model_deepseek"]}
        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SPEAK_QUEUE": "0"}, clear=False):
            queue_dir, ahead = self.worker.enter_speak_queue(
                "http://api", "main_text", message, transport="route_bus"
            )
        self.assertIsNone(queue_dir)
        self.assertEqual(ahead, [])


class CollaborationSpeakTurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    def test_speak_turn_lock_is_exclusive_and_released(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                lock = self.worker.acquire_speak_turn_lock("main_text", "thread-t1")
                self.assertIsNotNone(lock)
                # 已被持有：第二个抢锁者在超时后放行（返回 None，不死锁）。
                with patch.object(self.worker, "speak_queue_timeout_seconds", return_value=0.0):
                    self.assertIsNone(self.worker.acquire_speak_turn_lock("model_deepseek", "thread-t1"))
                self.worker.release_speak_turn_lock(lock)
                self.assertFalse(lock.exists())
                second = self.worker.acquire_speak_turn_lock("model_deepseek", "thread-t1")
                self.assertIsNotNone(second)
                self.worker.release_speak_turn_lock(second)

    def test_speak_turn_lock_takes_over_stale_holder(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                stale = self.worker.speak_turn_lock_path("thread-t2")
                stale.write_text(json.dumps({"agent": "model_deepseek", "acquired_at": 1.0}), encoding="utf-8")
                os.utime(stale, (1.0, 1.0))
                lock = self.worker.acquire_speak_turn_lock("main_text", "thread-t2")
                self.assertIsNotNone(lock)
                self.worker.release_speak_turn_lock(lock)

    def test_try_acquire_speak_turn_lock_is_nonblocking(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                lock = self.worker.try_acquire_speak_turn_lock("main_text", "thread-t2b")
                self.assertIsNotNone(lock)
                # 被持有时立即返回 None，不等待。
                started = time.monotonic()
                self.assertIsNone(self.worker.try_acquire_speak_turn_lock("model_deepseek", "thread-t2b"))
                self.assertLess(time.monotonic() - started, 1.0)
                self.worker.release_speak_turn_lock(lock)
                second = self.worker.try_acquire_speak_turn_lock("model_deepseek", "thread-t2b")
                self.assertIsNotNone(second)
                self.worker.release_speak_turn_lock(second)

    def test_try_acquire_speak_turn_lock_takes_over_stale_holder(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                stale = self.worker.speak_turn_lock_path("thread-t2c")
                stale.write_text(json.dumps({"agent": "model_deepseek", "acquired_at": 1.0}), encoding="utf-8")
                os.utime(stale, (1.0, 1.0))
                lock = self.worker.try_acquire_speak_turn_lock("main_text", "thread-t2c")
                self.assertIsNotNone(lock)
                self.worker.release_speak_turn_lock(lock)

    def test_fetch_thread_replies_since_filters_old_self_human_and_salvaged(self):
        def fake_request(api, path, payload=None):
            return {
                "messages": [
                    {"from_agent": "model_deepseek", "content": "太旧", "created_at": 10.0},
                    {"from_agent": "main_text", "content": "自己的", "created_at": 30.0},
                    {"from_agent": "human_desktop", "content": "人类插话", "created_at": 31.0},
                    {
                        "from_agent": "model_deepseek",
                        "content": self.worker.SALVAGED_REPLY_PREFIX + "抖救片段",
                        "created_at": 32.0,
                    },
                    {"from_agent": "model_deepseek", "content": "新定稿", "created_at": 33.0},
                ]
            }

        with patch.object(self.worker, "request_json", side_effect=fake_request):
            replies = self.worker.fetch_thread_replies_since(
                "http://api", "thread-t3", since=20.0, exclude_agent="main_text"
            )
        self.assertEqual(replies, [("model_deepseek", "新定稿")])

    def test_enter_speak_turn_returns_newer_replies_and_holds_lock(self):
        message = {
            "message_id": "reply-ds-1",
            "thread_id": "thread-t4",
            "from_agent": "model_deepseek",
            "to_agents": ["main_text", "human_desktop"],
            "content": "DeepSeek 的上一条发言",
            "created_at": 100.0,
        }

        def fake_request(api, path, payload=None):
            if payload and payload.get("action") == "list_messages":
                return {
                    "messages": [
                        {"from_agent": "model_deepseek", "content": "DeepSeek 更新的定稿", "created_at": 150.0},
                    ]
                }
            return {"ok": True}

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                with patch.object(self.worker, "request_json", side_effect=fake_request):
                    lock, replies, deferred = self.worker.enter_speak_turn(
                        "http://api", "main_text", message, transport="route_bus"
                    )
                self.assertIsNotNone(lock)
                self.assertTrue(lock.exists())
                self.assertFalse(deferred)
                self.assertEqual(replies, [("model_deepseek", "DeepSeek 更新的定稿")])
                # 更新定稿并入生成来件（一次成稿，不重修）。
                body = self.worker.message_content(self.worker.build_speak_after_message(message, replies))
                self.assertIn("DeepSeek 更新的定稿", body)
                self.worker.release_speak_turn_lock(lock)

    def test_enter_speak_turn_defers_when_lock_held(self):
        """v3：发言权被同伴持有时不等待，deferred=True 让调用方并行起草。"""
        message = {
            "message_id": "reply-ds-1b",
            "thread_id": "thread-t4b",
            "from_agent": "model_deepseek",
            "content": "DeepSeek 的上一条发言",
            "created_at": 100.0,
        }
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                holder = self.worker.try_acquire_speak_turn_lock("model_deepseek", "thread-t4b")
                self.assertIsNotNone(holder)
                started = time.monotonic()
                with patch.object(self.worker, "request_json", return_value={"ok": True}):
                    lock, replies, deferred = self.worker.enter_speak_turn(
                        "http://api", "main_text", message, transport="route_bus"
                    )
                self.assertIsNone(lock)
                self.assertEqual(replies, [])
                self.assertTrue(deferred)
                self.assertLess(time.monotonic() - started, 1.0)
                self.worker.release_speak_turn_lock(holder)

    def test_enter_speak_turn_no_newer_replies(self):
        message = {
            "message_id": "reply-ds-2",
            "thread_id": "thread-t5",
            "from_agent": "model_deepseek",
            "content": "上一条发言",
            "created_at": 100.0,
        }

        def fake_request(api, path, payload=None):
            if payload and payload.get("action") == "list_messages":
                return {"messages": []}
            return {"ok": True}

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_ROOT": tmp}, clear=False):
                with patch.object(self.worker, "request_json", side_effect=fake_request):
                    lock, replies, deferred = self.worker.enter_speak_turn(
                        "http://api", "main_text", message, transport="route_bus"
                    )
                self.assertIsNotNone(lock)
                self.assertEqual(replies, [])
                self.assertFalse(deferred)
                self.worker.release_speak_turn_lock(lock)

    def test_enter_speak_turn_disabled_by_env_switch(self):
        message = {"message_id": "reply-ds-3", "thread_id": "thread-t6", "created_at": 100.0}
        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_SPEAK_QUEUE": "0"}, clear=False):
            lock, replies, deferred = self.worker.enter_speak_turn(
                "http://api", "main_text", message, transport="route_bus"
            )
        self.assertIsNone(lock)
        self.assertEqual(replies, [])
        self.assertFalse(deferred)


class CollaborationTurnAllowanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    def test_turn_allowance_blocked_when_gateway_disallows(self):
        message = {"message_id": "m1", "thread_id": "thread-a"}
        with patch.object(
            self.worker,
            "request_json",
            return_value={"turn_guard": {"thread": {"allowed": False, "remaining": 0}}},
        ) as request:
            self.assertFalse(self.worker.turn_allowance_ok("http://api", message))
        payload = request.call_args[0][2]
        self.assertEqual(payload["action"], "turn_guard_status")
        self.assertEqual(payload["thread_id"], "thread-a")

    def test_turn_allowance_ok_when_within_cap(self):
        message = {"message_id": "m2", "thread_id": "thread-b"}
        with patch.object(
            self.worker,
            "request_json",
            return_value={"turn_guard": {"thread": {"allowed": True, "remaining": 3}}},
        ):
            self.assertTrue(self.worker.turn_allowance_ok("http://api", message))

    def test_turn_allowance_fails_open_on_gateway_error(self):
        message = {"message_id": "m3", "thread_id": "thread-c"}
        with patch.object(self.worker, "request_json", side_effect=RuntimeError("boom")):
            self.assertTrue(self.worker.turn_allowance_ok("http://api", message))

    def test_turn_allowance_returns_reason(self):
        # reason 决定处置：turn_paused 挂起不消费；缺省回退 turn_cap_reached 消费丢弃。
        message = {"message_id": "m4", "thread_id": "thread-d"}
        with patch.object(
            self.worker,
            "request_json",
            return_value={"turn_guard": {"thread": {"allowed": False, "reason": "turn_paused"}}},
        ):
            self.assertEqual(self.worker.turn_allowance("http://api", message), (False, "turn_paused"))
        with patch.object(
            self.worker,
            "request_json",
            return_value={"turn_guard": {"thread": {"allowed": False}}},
        ):
            self.assertEqual(self.worker.turn_allowance("http://api", message), (False, "turn_cap_reached"))
        with patch.object(self.worker, "request_json", side_effect=RuntimeError("boom")):
            self.assertEqual(self.worker.turn_allowance("http://api", message), (True, ""))


class CollaborationTurnPauseDeferTests(unittest.TestCase):
    """修H：人工暂停（turn_paused）= 挂起不消费；预算用尽（turn_cap_reached）维持消费丢弃。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    @staticmethod
    def _model_message(message_id: str) -> dict:
        return {
            "schema_version": "spiritkin.agent_protocol.v1",
            "message_id": message_id,
            "sender": "model_deepseek",
            "recipient": "main_text",
            "message_type": "reply",
            "content": "双工续聊",
            "context_id": "thread-pause-defer",
            "created_at": time.time(),
        }

    def _run_once(self, allowance_result: tuple[bool, str], messages: list[dict]):
        with patch.object(
            self.worker,
            "load_external_assistant",
            return_value={"assistant_id": "main_text", "command": "python", "enabled": True},
        ), patch.object(self.worker, "list_worker_messages", return_value=messages), \
            patch.object(self.worker, "preempt_human_messages"), \
            patch.object(self.worker, "record_worker_event"), \
            patch.object(self.worker, "turn_allowance", return_value=allowance_result) as allowance, \
            patch.object(self.worker, "mark_consumed") as mark_consumed, \
            patch.object(self.worker, "process_worker_message_with_retry") as process:
            code = self.worker.main(["--agent", "main_text", "--once", "--auto-reply", "--no-push"])
        return code, allowance, mark_consumed, process

    def test_turn_paused_message_not_consumed_not_seen(self):
        # 暂停是挂起不是丢弃：不 mark_consumed、不处理，消息留在 bus，恢复后下一轮轮询续上。
        messages = [self._model_message("agentmsg-paused-1"), self._model_message("agentmsg-paused-2")]
        code, allowance, mark_consumed, process = self._run_once((False, "turn_paused"), messages)
        self.assertEqual(code, 0)
        # 批内同 thread 的 guard 结果走缓存，两条消息只打一次 HTTP。
        allowance.assert_called_once()
        mark_consumed.assert_not_called()
        process.assert_not_called()

    def test_turn_cap_reached_still_consumed(self):
        # 预算用尽维持原语义：消费丢弃，等人工续杯后的新消息。
        code, _, mark_consumed, process = self._run_once(
            (False, "turn_cap_reached"), [self._model_message("agentmsg-cap-1")]
        )
        self.assertEqual(code, 0)
        mark_consumed.assert_called_once()
        process.assert_not_called()

    # 修O（批次十返工）：小模型偶发把"对提示词/自身故障的自我诊断"当正式回帖
    #（2026-07-09 实测：输出"问题归因/最小修复步骤"报告并回显提示词约束句）。
    def test_meta_derailed_reply_detection(self):
        derailed = (
            "问题归因\n模型在内部推理完成后未触发输出模式，自我检查标记（All good. Proceed.）被写入响应流。\n"
            "最小修复步骤：在系统提示词末尾追加 OUTPUT_START 分隔符。"
        )
        self.assertTrue(self.worker.looks_like_meta_derailed_reply(derailed))
        # 正常回帖只擦到一个标记（话题本身聊提示词工程）不判脱轨。
        normal = "我的观点：结果比过程更重要。即使系统提示词写得再好，最终也要看落地效果。"
        self.assertFalse(self.worker.looks_like_meta_derailed_reply(normal))
        self.assertFalse(self.worker.looks_like_meta_derailed_reply(""))

    def test_derail_regeneration_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPIRITKIN_COLLABORATION_DERAIL_RETRY", None)
            self.assertFalse(self.worker.collaboration_derail_retry_enabled())
        with patch.dict(os.environ, {"SPIRITKIN_COLLABORATION_DERAIL_RETRY": "1"}, clear=False):
            self.assertTrue(self.worker.collaboration_derail_retry_enabled())

    def test_build_prompt_injects_derail_retry_warning(self):
        message = {
            "message_id": "agentmsg-derail-1",
            "sender": "model_deepseek",
            "recipient": "main_text",
            "message_type": "answer",
            "content": "请继续辩论。",
            "metadata": {"thread_id": "thread-derail"},
        }
        base_prompt = self.worker.build_prompt(message)
        self.assertNotIn("上一次生成偏离了对话", base_prompt)
        retry_prompt = self.worker.build_prompt({**message, "_derail_retry": True})
        self.assertIn("上一次生成偏离了对话", retry_prompt)


if __name__ == "__main__":
    unittest.main()
