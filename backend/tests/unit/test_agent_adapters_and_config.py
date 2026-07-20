import json
import os
import unittest
from unittest.mock import patch

from backend.agents.base import AgentContext, AgentReply
from backend.app.agent_management import default_managed_agents
from backend.orchestrator.agent_adapters import CrewAIAdapter, LangGraphAdapter, build_agent_adapter
from backend.services.feishu import FeishuClient, FeishuConfig, load_feishu_config


class _Agent:
    def handle(self, context):
        return AgentReply(text=f"handled:{context.user_input}", agent_name="fake")


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AgentAdaptersAndConfigTests(unittest.TestCase):
    def test_external_reviewer_is_config_driven_and_defaults_off(self):
        with patch.dict(os.environ, {}, clear=True):
            default = next(agent for agent in default_managed_agents() if agent.agent_id == "external_reviewer")
        with patch.dict(os.environ, {"SPIRITKIN_EXTERNAL_REVIEWER_ENABLED": "1"}, clear=True):
            enabled = next(agent for agent in default_managed_agents() if agent.agent_id == "external_reviewer")

        self.assertFalse(default.enabled)
        self.assertTrue(enabled.enabled)

    def test_persona_agents_have_real_tool_scopes(self):
        agents = {agent.agent_id: agent for agent in default_managed_agents()}

        self.assertIn("python.run_script", agents["game_development"].allowed_tools)
        self.assertIn("ffmpeg.transcode", agents["video_animation"].allowed_tools)
        self.assertTrue(agents["game_development"].snapshot()["allowed_tools"])

    def test_langgraph_minimal_executes_agent_task_node(self):
        adapter = build_agent_adapter(
            "video_animation",
            {"framework": "langgraph", "graph": {"nodes": [{"id": "draft", "type": "agent_task"}]}},
        )

        reply = adapter.run(_Agent(), AgentContext("make a storyboard"))

        self.assertIsInstance(adapter, LangGraphAdapter)
        self.assertEqual(reply.metadata["graph_execution"]["executed_node_ids"], ["draft"])
        self.assertEqual(reply.metadata["framework"], "langgraph")

    def test_crewai_adapter_has_explicit_native_fallback_error(self):
        adapter = build_agent_adapter("game_development", {"framework": "crewai"})

        self.assertIsInstance(adapter, CrewAIAdapter)
        with self.assertRaisesRegex(NotImplementedError, "native fallback"):
            adapter.run(_Agent(), AgentContext("build a game"))

    def test_feishu_defaults_to_dry_run_without_credentials(self):
        self.assertTrue(load_feishu_config({}).dry_run)

    def test_feishu_real_request_retries_once(self):
        client = FeishuClient(FeishuConfig(app_id="id", app_secret="secret", dry_run=False))
        client._tenant_access_token = "token"
        with patch("backend.services.feishu.urllib.request.urlopen", side_effect=[OSError("temporary"), _Response({"code": 0, "data": {"message_id": "m-1"}})]) as urlopen:
            result = client.send_text_message("user-1", "hello")

        self.assertFalse(result.dry_run)
        self.assertEqual(result.message_id, "m-1")
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
