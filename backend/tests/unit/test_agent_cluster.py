import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.agents.base import AgentContext, parse_emotion_action_response
from backend.app.agent_cluster_ports import DefaultAgentClusterAppPort
from backend.app.evolution_management import handle_evolution_management_action
from backend.devices import InMemoryOpenClawClient
from backend.executors import ExecutorRemoteNodeClient, NodeRegistry, OpenClawExecutor, RemoteNode
from backend.executors.base import ExecutionRequest, ExecutionResult
from backend.knowledge import InMemoryKnowledgeStore, SimpleKnowledgeRetriever, ingest_text_document
from backend.memory.long_term import LongTermMemoryStore
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.repair import BaseRepairAdvisor, RepairAdvice
from backend.orchestrator.resource_budget import ResourceBudgetGate
from backend.orchestrator.resource_registry import ResourceRecord, ResourceRegistry, save_resource_registry
from backend.security.policy import PolicyEngine, PolicyRule
from backend.security.safety_control import SafetyDecision
from backend.services.feishu import FeishuSendResult
from backend.skills import SkillRegistry, SkillSpec, SkillStepSpec
from backend.tools import ExecutionTool, ToolCall, ToolRegistry, ToolResult, ToolSpec
from backend.tools.base import BaseTool


class FakeDeviceBackend:
    name = "fake"

    def __init__(self):
        self.closed_apps = []
        self.closed_windows = []
        self.clipboard_text = "剪贴板内容"

    def get_screen_size(self):
        return (1920, 1080)

    def move_to(self, x, y):
        return None

    def click(self, x, y):
        return None

    def double_click(self, x, y):
        return None

    def type_text(self, text):
        return None

    def press_key(self, key):
        return None

    def hotkey(self, *keys):
        return None

    def extract_text(self, region=None, lang="chi_sim+eng"):
        return "登录按钮\n用户名输入框"

    def understand_screen(self, query: str, region=None):
        return f"视觉结果：{query}"

    def list_installed_apps(self, limit=80):
        return [{"name": "Microsoft Edge", "version": "1.0", "publisher": "Microsoft"}]

    def list_hardware_devices(self, limit=80):
        return [{"Class": "Camera", "FriendlyName": "Integrated Camera", "Status": "OK"}]

    def close_app(self, app_name, force=False):
        self.closed_apps.append((app_name, force))
        return {"app_name": app_name, "display_name": app_name, "closed_count": 1, "closed": [{"pid": 123, "name": app_name}]}

    def open_url(self, url):
        return {"url": url, "opened": True}

    def search_web(self, query, engine="bing"):
        return {"url": f"https://search.example/?q={query}", "query": query, "engine": engine, "opened": True}

    def read_clipboard(self):
        return {"text": self.clipboard_text, "length": len(self.clipboard_text)}

    def write_clipboard(self, text):
        self.clipboard_text = text
        return {"length": len(text)}

    def capture_screen(self, output_path=None):
        return {"path": output_path or "screen.png", "width": 1920, "height": 1080}

    def list_windows(self, limit=40):
        return [{"pid": 1, "process_name": "Code", "title": "SpiritKinAI - VSCode"}]

    def activate_window(self, title):
        return {"title": title, "matched_count": 1, "windows": [{"title": title, "ok": True}]}

    def close_window(self, title, force=False):
        self.closed_windows.append((title, force))
        return {"title": title, "matched_count": 1, "windows": [{"title": title, "ok": True}], "force": force}

    def search_files(self, query, root=None, limit=20):
        return {"query": query, "root": root or ".", "matches": [{"name": "tmp_agent_handoff_2026-04-17.md", "path": "docs/archive/tmp_agent_handoff_2026-04-17.md", "is_dir": False, "suffix": ".md"}]}

    def read_file_text(self, path, max_chars=4000):
        return {"path": path, "content": "handoff content", "truncated": False, "length": 15}

    def open_file(self, path):
        return {"path": path, "opened": True}


class FakeWebSearchTool(BaseTool):
    def __init__(self):
        self.calls = []
        self.spec = ToolSpec(
            name="web.search",
            description="fake web search",
            target="web",
            operation="search",
            read_only=True,
            schema={"query": "str", "count": "int"},
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(dict(call.arguments))
        return ToolResult(
            success=True,
            message="ok",
            data=[
                {
                    "title": "SpiritKinAI release",
                    "url": "https://example.com/spiritkin",
                    "snippet": "SpiritKinAI latest status from the web.",
                    "provider": "fake",
                }
            ],
        )


class TaggedResponseParsingTest(unittest.TestCase):
    def test_combined_emotion_action_tag_is_removed_from_visible_text(self):
        text, emotion, action = parse_emotion_action_response("你好呀 <emotion:happy|wave_hand>", default_action="")

        self.assertEqual(text, "你好呀")
        self.assertEqual(emotion, "happy")
        self.assertEqual(action, "wave_hand")


class FakeExecutor:
    def __init__(self, name: str, supported_targets: set[str]):
        self.name = name
        self._supported_targets = supported_targets
        self.requests = []

    def supports(self, request):
        return request.target in self._supported_targets

    def execute(self, request):
        self.requests.append(request)
        return ExecutionResult(success=True, message=f"fake executed: {request.target}.{request.operation}")


class FakeOpenClawClient:
    def __init__(self):
        self.calls = []

    def home(self):
        self.calls.append(("home", {}))
        return "ok"

    def move_to(self, **kwargs):
        self.calls.append(("move_to", kwargs))
        return kwargs

    def set_gripper(self, opened: bool):
        self.calls.append(("set_gripper", {"opened": opened}))
        return opened

    def get_status(self):
        self.calls.append(("get_status", {}))
        return {"state": "idle"}


class FakeFeishuClient:
    def __init__(self):
        self.calls = []

    def send_text_message(self, recipient: str, text: str):
        self.calls.append((recipient, text))
        return FeishuSendResult(True, recipient, f"user_id:{recipient}", "user_id", text, message_id="test-dry-run")


class FakeRepairAdvisor(BaseRepairAdvisor):
    def __init__(self):
        self.failures = []

    def analyze(self, failure):
        self.failures.append(failure)
        return RepairAdvice(summary=f"建议排查：{failure.error_code}", suggested_actions=[failure.actor])


class AgentClusterTests(unittest.TestCase):
    def test_general_prompt_activates_long_term_memory(self):
        prompts = []
        memory = LongTermMemoryStore()
        memory.add("preference", "用户偏好简短直接的回答", importance=0.9)

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            return "我记得，会保持简短。<emotion:happy><action:idle>"

        cluster = AgentCluster(
            llm_client=fake_llm,
            long_term_memory=memory,
            device_backend=FakeDeviceBackend(),
        )

        cluster.process("回答继续简短一点")

        self.assertTrue(prompts)
        self.assertIn("已激活的长期记忆", prompts[-1])
        self.assertIn("用户偏好简短直接的回答", prompts[-1])
        self.assertEqual(cluster._active_input_metadata["long_term_memory_status"]["status"], "activated")

    def test_time_queries_use_builtin_tool_path(self):
        cluster = AgentCluster(llm_client=lambda _: self.fail("时间查询不应调用 LLM"))

        reply = cluster.process("现在几点了？")

        self.assertEqual(reply.emotion, "neutral")
        self.assertEqual(reply.action, "glance_clock")
        self.assertIn("现在是", reply.text)

    def test_math_queries_use_safe_calc_path(self):
        cluster = AgentCluster(llm_client=lambda _: self.fail("计算查询不应调用 LLM"))

        reply = cluster.process("2+3等于多少")

        self.assertEqual(reply.emotion, "happy")
        self.assertEqual(reply.action, "write_on_board")
        self.assertIn("5", reply.text)

    def test_programming_queries_route_to_programming_agent(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先看报错栈，再定位入口 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个 Python 报错应该怎么排查？")

        self.assertEqual(reply.agent_name, "programming")
        self.assertEqual(reply.emotion, "thinking")
        self.assertIn("编程助理", prompts[0])
        self.assertIn("当前输入：这个 Python 报错应该怎么排查？", prompts[0])

    def test_programming_agent_injects_git_workspace_context(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先看工作区变更再判断 <emotion:thinking>"

        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            repo = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
            (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")

            cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())
            reply = cluster.process(
                "这个 Python 代码改动怎么排查？",
                input_metadata={"code_workspace_repo_path": str(repo), "include_code_diff": True},
            )

        self.assertEqual(reply.agent_name, "programming")
        self.assertTrue(reply.metadata["code_workspace_context"]["success"])
        self.assertIn("代码工作区上下文", prompts[0])
        self.assertIn("git.status", prompts[0])
        self.assertIn("app.py", prompts[0])
        self.assertIn("git.diff", prompts[0])
        self.assertEqual(reply.metadata["code_workspace_context"]["sections"][0]["worker"]["worker_id"], "executor:git_worker")

    def test_programming_agent_code_context_can_be_disabled(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "只根据输入回答 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process(
            "这个 Python 报错应该怎么排查？",
            input_metadata={"include_code_workspace_context": False},
        )

        self.assertEqual(reply.agent_name, "programming")
        self.assertNotIn("code_workspace_context", reply.metadata)
        self.assertNotIn("代码工作区上下文", prompts[0])

    def test_agent_mention_status_returns_agent_snapshot(self):
        cluster = AgentCluster(llm_client=lambda _: self.fail("@Agent 状态查询不应调用 LLM"), device_backend=FakeDeviceBackend())

        reply = cluster.process("@programming 当前工作情况和状态")

        self.assertEqual(reply.agent_name, "agent_status")
        self.assertEqual(reply.metadata["response_kind"], "agent_status")
        self.assertEqual(reply.metadata["agent_mention"]["agent_id"], "programming")
        self.assertEqual(reply.metadata["agent_status"]["agent_id"], "programming")
        self.assertIn("framework", reply.text)

    def test_agent_mention_forces_specialist_route(self):
        prompts = []

        def fake_llm(prompt: str, **kwargs) -> str:
            prompts.append((prompt, kwargs))
            return "交给编程 Agent 处理 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("@programming 帮我梳理这个需求")

        self.assertEqual(reply.agent_name, "programming")
        self.assertEqual(reply.metadata["agent_mention"]["agent_id"], "programming")
        self.assertEqual(reply.metadata["scheduler"]["agent_id"], "programming")
        self.assertIn("当前输入：帮我梳理这个需求", prompts[0][0])

    def test_programming_agent_uses_managed_model_policy_and_metadata(self):
        calls = []

        def fake_llm(prompt: str, **kwargs) -> str:
            calls.append(kwargs)
            return "按最小复现定位 <emotion:thinking>"

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            managed_agents={
                "framework": "spiritkin_unified_agent_cluster",
                "agent_profiles_by_id": {
                    "programming": {
                        "agent_id": "programming",
                        "label": "编程 Agent",
                        "domain": "programming",
                        "provider": "ollama",
                        "model": "qwen-coder",
                        "model_id": "local-coder",
                        "framework": "codex_or_native",
                        "adapter": "code_agent_adapter",
                        "role": "specialist",
                        "capabilities": ["code_edit", "tests"],
                    }
                },
                "assistant_allowlist_by_agent": {"programming": ["codex_cli"]},
                "enabled_external_assistants_by_id": {
                    "codex_cli": {
                        "assistant_id": "codex_cli",
                        "label": "Codex CLI",
                        "kind": "cli",
                        "enabled": True,
                    }
                },
                "knowledge_base_by_agent": {
                    "programming": {
                        "knowledge_base_id": "kb_programming",
                        "label": "编程知识库",
                        "path": "state/knowledge_bases/agents/programming",
                        "enabled": True,
                    }
                },
            },
        )

        reply = cluster.process("这个 Python bug 怎么排查？")

        self.assertEqual(reply.agent_name, "programming")
        self.assertEqual(calls[-1]["agent_name"], "programming")
        self.assertEqual(calls[-1]["provider"], "ollama")
        self.assertEqual(calls[-1]["model_name"], "qwen-coder")
        runtime = reply.metadata["scheduler"]["agent_runtime"]
        self.assertEqual(runtime["framework"], "spiritkin_unified_agent_cluster")
        self.assertEqual(runtime["control_plane"]["runtime"], "SpiritKinRuntime")
        self.assertEqual(runtime["control_plane"]["orchestrator"], "AgentCluster")
        self.assertEqual(runtime["control_plane"]["llm_role"], "optional_route_and_plan_only")
        self.assertEqual(runtime["openclaw_layer"], "worker_executor")
        self.assertEqual(runtime["policy"]["model_id"], "local-coder")
        self.assertEqual(runtime["policy"]["framework"], "codex_or_native")
        self.assertEqual(runtime["policy"]["adapter"], "code_agent_adapter")
        self.assertEqual(runtime["policy"]["allowed_assistant_ids"], ["codex_cli"])
        self.assertEqual(runtime["policy"]["allowed_assistants"][0]["assistant_id"], "codex_cli")
        self.assertEqual(runtime["policy"]["knowledge_base"]["knowledge_base_id"], "kb_programming")
        self.assertEqual(runtime["brain_router"]["agent_id"], "programming")
        self.assertEqual(runtime["brain_router"]["provider"], "ollama")
        self.assertEqual(runtime["brain_router"]["model"], "qwen-coder")
        self.assertEqual(runtime["capability_container"]["agent_id"], "programming")
        self.assertIn("code_edit", runtime["capability_container"]["capabilities"])
        self.assertEqual(runtime["capability_container"]["brain_policy"]["provider"], "ollama")
        adapter = reply.metadata["agent_adapter"]
        self.assertEqual(adapter["framework"], "codex_or_native")
        self.assertEqual(adapter["adapter"], "code_agent_adapter")
        self.assertEqual(reply.metadata["framework"], "codex_or_native")
        self.assertEqual(reply.metadata["adapter"], "code_agent_adapter")

    def test_plan_mode_returns_plan_without_executor_side_effect(self):
        prompts = []
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})

        def fake_llm(prompt: str, **kwargs) -> str:
            prompts.append(prompt)
            return "1. 确认目标\n2. 检查环境\n3. 再执行打开操作 <emotion:thinking><action:write_plan>"

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("打开浏览器", channel="desktop", input_metadata={"plan_mode": True})

        self.assertEqual(reply.agent_name, "plan_mode")
        self.assertEqual(reply.metadata["response_kind"], "plan_mode")
        self.assertTrue(reply.metadata["execution_blocked"])
        self.assertEqual(reply.metadata["plan"]["mode"], "plan_only")
        self.assertGreaterEqual(len(reply.metadata["plan"]["steps"]), 3)
        self.assertTrue(reply.metadata["plan"]["requires_confirmation_before_execution"])
        self.assertEqual(executor.requests, [])
        self.assertIn("planning only", prompts[0])

    def test_pursue_goal_uses_active_goal_context(self):
        prompts = []

        def fake_llm(prompt: str, **kwargs) -> str:
            prompts.append(prompt)
            return "继续推进这个目标：先整理剩余任务。 <emotion:thinking><action:write_plan>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process(
            "继续",
            channel="desktop",
            input_metadata={
                "pursue_goal": True,
                "goal_text": "完善桌面端 + 号菜单",
                "project_title": "SpiritKinAI",
            },
        )

        self.assertEqual(reply.agent_name, "goal_pursuit")
        self.assertEqual(reply.metadata["response_kind"], "goal_pursuit")
        self.assertEqual(reply.metadata["goal"]["text"], "完善桌面端 + 号菜单")
        self.assertEqual(reply.metadata["goal"]["status"], "active")
        self.assertGreater(reply.metadata["goal"]["progress_percent"], 0)
        self.assertIn("next_action", reply.metadata["goal"])
        self.assertIn("持续目标：完善桌面端 + 号菜单", prompts[0])
        self.assertIn("Current user input: 继续", prompts[0])

    def test_agent_specific_knowledge_base_is_injected_into_prompt(self):
        with TemporaryDirectory() as tmp:
            kb_dir = Path(tmp) / "programming_kb"
            kb_dir.mkdir()
            (kb_dir / "debug.md").write_text("Python 调试流程：先看 traceback，再写最小复现。", encoding="utf-8")
            prompts = []

            def fake_llm(prompt: str, **kwargs) -> str:
                prompts.append(prompt)
                return "先按知识库流程排查 <emotion:thinking>"

            cluster = AgentCluster(
                llm_client=fake_llm,
                device_backend=FakeDeviceBackend(),
                managed_agents={
                    "agent_profiles_by_id": {
                        "programming": {
                            "agent_id": "programming",
                            "label": "编程 Agent",
                            "domain": "programming",
                            "provider": "openai_compatible",
                            "model": "coder",
                            "role": "specialist",
                        }
                    },
                    "knowledge_base_by_agent": {
                        "programming": {
                            "knowledge_base_id": "kb_programming",
                            "label": "编程知识库",
                            "path": str(kb_dir),
                            "enabled": True,
                        }
                    },
                },
            )

            reply = cluster.process("这个 Python traceback bug 怎么处理？")

            self.assertEqual(reply.agent_name, "programming")
            self.assertIn("知识检索结果", prompts[0])
            self.assertIn("Python 调试流程", prompts[0])
            self.assertEqual(reply.metadata["agent_knowledge_hits"][0]["source_title"], "debug")

    def test_ecommerce_queries_route_to_ecommerce_agent(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先优化主图和详情页，再看投放转化 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个商品详情页转化率低，电商运营上先怎么排查？")

        self.assertEqual(reply.agent_name, "ecommerce")
        self.assertEqual(reply.emotion, "thinking")
        self.assertIn("电商助理", prompts[0])
        self.assertIn("当前输入：这个商品详情页转化率低，电商运营上先怎么排查？", prompts[0])

    def test_video_queries_route_to_video_animation_agent(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先确定分镜、字幕和配音节奏，再组织 FFmpeg 合成 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("帮我规划一个产品宣传视频分镜和字幕节奏")

        self.assertEqual(reply.agent_name, "video_animation")
        self.assertEqual(reply.emotion, "thinking")
        self.assertIn("视频动画助理", prompts[0])
        self.assertEqual(reply.metadata["scheduler"]["domain"], "video_animation")

    def test_game_queries_route_to_game_development_agent(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先拆玩法循环、状态机和数值验证 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个游戏战斗系统和状态机应该怎么拆？")

        self.assertEqual(reply.agent_name, "game_development")
        self.assertEqual(reply.emotion, "thinking")
        self.assertIn("游戏制作助理", prompts[0])
        self.assertEqual(reply.metadata["scheduler"]["domain"], "game_development")

    def test_ecommerce_priority_wins_over_programming_for_mixed_query(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先看详情页转化，再决定脚本自动化 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个电商商品详情页和投放脚本应该先优化哪边？")

        self.assertEqual(reply.agent_name, "ecommerce")
        self.assertIn("电商助理", prompts[0])
        self.assertEqual(reply.metadata["scheduler"]["domain"], "ecommerce")

    def test_ecommerce_reply_includes_project_metadata(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "先补齐详情页卖点，再检查主图承接 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个电商商品详情页转化率低，先从哪里优化？")

        self.assertEqual(reply.agent_name, "ecommerce")
        self.assertEqual(reply.metadata["project"]["current_phase"], "listing")
        self.assertEqual(reply.metadata["project"]["project_type"], "growth_ops")
        self.assertTrue(reply.metadata["project"]["project_id"].startswith("ecom_"))
        self.assertIn("当前电商项目", prompts[0])

    def test_ecommerce_queued_task_attaches_project_metadata(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 0, "cpu_io": 2, "interactive": 1})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("GPU 通道被占满时不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        reply = cluster.process("新店起店时，电商应该先做选品还是先上架？")

        self.assertEqual(reply.agent_name, "scheduler")
        self.assertEqual(reply.metadata["task"]["status"], "queued")
        self.assertEqual(reply.metadata["project"]["project_type"], "store_launch")
        self.assertEqual(reply.metadata["project"]["current_phase"], "selection")
        self.assertEqual(reply.metadata["task"]["project_id"], reply.metadata["project"]["project_id"])

    def test_ecommerce_project_phase_moves_forward_without_regressing(self):
        cluster = AgentCluster(
            llm_client=lambda _: "先看当前阶段重点 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
        )

        first_reply = cluster.process("新店起店先做选品")
        second_reply = cluster.process("接下来把商品上架并优化详情页")
        third_reply = cluster.process("再回头看看选品池")

        self.assertEqual(first_reply.metadata["project"]["current_phase"], "selection")
        self.assertEqual(second_reply.metadata["project"]["current_phase"], "listing")
        self.assertEqual(third_reply.metadata["project"]["current_phase"], "listing")

    def test_cluster_can_query_active_ecommerce_projects(self):
        cluster = AgentCluster(
            llm_client=lambda _: "先补齐素材测试计划 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("这个电商素材点击率低，下一步怎么测素材？")
        project_id = reply.metadata["project"]["project_id"]

        active_projects = cluster.list_active_ecommerce_projects()
        project = cluster.get_ecommerce_project(project_id)

        self.assertEqual(len(active_projects), 1)
        self.assertEqual(active_projects[0]["project_id"], project_id)
        self.assertEqual(project["current_phase"], "creative")
        self.assertEqual(project["project_type"], "growth_ops")

    def test_resource_registry_snapshot_includes_runtime_assets(self):
        cluster = AgentCluster(
            llm_client=lambda _: "先补齐素材测试计划 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("这个电商素材点击率低，下一步怎么测素材？")
        snapshot = cluster.resource_registry_snapshot
        inventory_snapshot = cluster.capability_inventory_snapshot
        resources = {item["resource_id"]: item for item in snapshot["resources"]}
        commerce_resources = [
            item
            for item in snapshot["resources"]
            if item["resource_type"] == "commerce_project"
        ]

        self.assertIn("device:local_pc", resources)
        self.assertIn("worker:executor:local_pc", resources)
        self.assertTrue(any(item["resource_type"] == "repository" for item in snapshot["resources"]))
        self.assertEqual(commerce_resources[0]["owner_agent"], "ecommerce")
        self.assertEqual(commerce_resources[0]["state_ref"], f"ecommerce_project:{reply.metadata['project']['project_id']}")
        self.assertIn("resource_registry", reply.metadata)
        self.assertGreaterEqual(reply.metadata["resource_registry"]["total"], 1)
        self.assertEqual(inventory_snapshot["resource_registry"]["total"], snapshot["total"])

    def test_agent_cluster_loads_persistent_resource_registry(self):
        with TemporaryDirectory() as tmp:
            resource_path = Path(tmp) / "resources.json"
            save_resource_registry(
                ResourceRegistry(
                    [
                        ResourceRecord(
                            resource_id="shop:alpha",
                            label="Alpha Shop",
                            resource_type="shop",
                            platform="douyin",
                            owner_agent="ecommerce",
                            credential_ref="vault:alpha",
                            supported_capabilities=("commerce.product.publish",),
                            health_status="ready",
                        )
                    ]
                ),
                resource_path,
            )

            cluster = AgentCluster(
                llm_client=lambda _: "先检查店铺资源 <emotion:thinking>",
                device_backend=FakeDeviceBackend(),
                resource_registry_path=resource_path,
            )

            resources = {item["resource_id"]: item for item in cluster.resource_registry_snapshot["resources"]}

        self.assertIn("shop:alpha", resources)
        self.assertEqual(resources["shop:alpha"]["credential_ref"], "vault:alpha")
        self.assertIn("device:local_pc", resources)

    def test_vision_queries_route_to_device_backend(self):
        cluster = AgentCluster(llm_client=lambda _: "不应走 LLM", device_backend=FakeDeviceBackend())

        reply = cluster.process("帮我看看这个界面上有什么按钮")

        self.assertEqual(reply.agent_name, "vision")
        self.assertEqual(reply.action, "scan_screen")
        self.assertIn("视觉结果", reply.text)

    def test_general_path_uses_visual_context_and_caps_memory(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "收到啦 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, memory_limit=1, device_backend=FakeDeviceBackend())

        reply = cluster.process("帮我分析一下", visual_context="用户在挥手")

        self.assertEqual(reply.emotion, "thinking")
        self.assertEqual(reply.action, "tap_chin")
        self.assertEqual(reply.text, "收到啦")
        self.assertIn("[视觉提示：用户在挥手] 帮我分析一下", prompts[0])

        cluster.process("再来一次")
        self.assertEqual(len(cluster.memory), 2)
        self.assertEqual(cluster.memory[-2]["content"], "再来一次")

    def test_perception_context_is_opt_in_for_general_route(self):
        class CountingDevice(FakeDeviceBackend):
            def __init__(self):
                super().__init__()
                self.screen_calls = 0

            def understand_screen(self, query: str, region=None):
                self.screen_calls += 1
                return super().understand_screen(query, region=region)

        device = CountingDevice()
        cluster = AgentCluster(
            llm_client=lambda _: "普通回答 <emotion:neutral>",
            device_backend=device,
        )

        reply = cluster.process("帮我分析一下")

        self.assertEqual(device.screen_calls, 0)
        self.assertNotIn("perception_context", reply.metadata)

    def test_perception_context_enters_prompt_when_requested(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "我会结合当前屏幕判断。<emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process(
            "帮我分析一下下一步",
            input_metadata={"include_perception_context": True},
        )

        self.assertIn("屏幕理解：视觉结果：帮我分析一下下一步", prompts[0])
        self.assertIn("[视觉提示：屏幕理解：视觉结果：帮我分析一下下一步] 帮我分析一下下一步", prompts[0])
        self.assertEqual(reply.metadata["perception_context"]["operation"], "screen_understand")
        self.assertTrue(reply.metadata["perception_context"]["success"])
        self.assertEqual(reply.metadata["perception_context"]["worker"]["worker_id"], "executor:local_pc")

    def test_perception_context_can_use_ocr_mode(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "我看到了文字。<emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process(
            "帮我根据当前状态做判断",
            input_metadata={"include_screen_context": True, "perception_context_mode": "ocr"},
        )

        self.assertIn("屏幕文字：登录按钮", prompts[0])
        self.assertEqual(reply.metadata["perception_context"]["operation"], "screen_extract_text")
        self.assertTrue(reply.metadata["perception_context"]["success"])

    def test_perception_context_respects_policy_gate(self):
        class CountingDevice(FakeDeviceBackend):
            def __init__(self):
                super().__init__()
                self.screen_calls = 0

            def understand_screen(self, query: str, region=None):
                self.screen_calls += 1
                return super().understand_screen(query, region=region)

        prompts = []
        policy = PolicyEngine(
            [
                PolicyRule(
                    rule_id="deny-screen-context",
                    description="deny screen perception in tests",
                    target_pattern="screen",
                    operation_pattern="screen_understand",
                    allowed=False,
                    priority=1,
                )
            ],
            default_deny=False,
        )
        device = CountingDevice()
        cluster = AgentCluster(
            llm_client=lambda prompt: prompts.append(prompt) or "继续普通回答 <emotion:neutral>",
            device_backend=device,
            policy_engine=policy,
        )

        reply = cluster.process(
            "帮我分析一下",
            input_metadata={"include_perception_context": True},
        )

        self.assertEqual(device.screen_calls, 0)
        self.assertEqual(reply.metadata["perception_context"]["error_code"], "policy_denied")
        self.assertEqual(reply.metadata["perception_context"]["policy_decision"]["matched_rule_id"], "deny-screen-context")
        self.assertNotIn("屏幕理解：", prompts[0])

    def test_general_path_accepts_llm_action_metadata_tags(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "可以，我在这里。<emotion:happy><action:wave_hand>"

        cluster = AgentCluster(llm_client=fake_llm, memory_limit=1, device_backend=FakeDeviceBackend())

        reply = cluster.process("和我打个招呼")

        self.assertEqual(reply.text, "可以，我在这里。")
        self.assertEqual(reply.emotion, "happy")
        self.assertEqual(reply.action, "wave_hand")
        self.assertIn("<emotion:neutral|happy|thinking|confused|speechless|waiting|alert|error|surprised|sad>", prompts[0])
        self.assertIn("<action:idle|nod|shake|wave_hand|walk>", prompts[0])

    def test_general_path_supports_speechless_emotion_for_display(self):
        def fake_llm(prompt: str) -> str:
            return "人并不能飞呢。<emotion:speechless><action:shake>"

        cluster = AgentCluster(llm_client=fake_llm, memory_limit=1, device_backend=FakeDeviceBackend())

        reply = cluster.process("人能飞吗？")

        self.assertEqual(reply.text, "人并不能飞呢。")
        self.assertEqual(reply.emotion, "speechless")
        self.assertEqual(reply.action, "shake")

    def test_general_path_accepts_structured_emotion_action_json(self):
        def fake_llm(prompt: str) -> str:
            return '{"text":"我先想一下，再给你结论。","emotion":"thinking","action":"nod"}'

        cluster = AgentCluster(llm_client=fake_llm, memory_limit=1, device_backend=FakeDeviceBackend())

        reply = cluster.process("这个方案怎么判断？")

        self.assertEqual(reply.text, "我先想一下，再给你结论。")
        self.assertEqual(reply.emotion, "thinking")
        self.assertEqual(reply.action, "nod")

    def test_general_path_includes_attachment_document_preview(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "我看到了附件内容 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, memory_limit=1, device_backend=FakeDeviceBackend())

        reply = cluster.process(
            "帮我总结拖入文件",
            input_metadata={
                "attachment_documents": [
                    {"path": "notes.md", "text_preview": "这里是拖拽文件的核心内容。"}
                ]
            },
        )

        self.assertEqual(reply.text, "我看到了附件内容")
        self.assertIn("附件文本预览", prompts[0])
        self.assertIn("notes.md", prompts[0])
        self.assertIn("这里是拖拽文件的核心内容", prompts[0])

    def test_cluster_returns_scheduler_busy_reply_when_gpu_budget_is_exhausted(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 0, "cpu_io": 2, "interactive": 1})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("GPU 通道被占满时不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        reply = cluster.process("这个 Python 报错应该怎么排查？")

        self.assertEqual(reply.agent_name, "scheduler")
        self.assertEqual(reply.metadata["response_kind"], "scheduler_busy")
        self.assertEqual(reply.metadata["scheduler"]["resource_profile"], "gpu_heavy")
        self.assertEqual(reply.metadata["task"]["status"], "queued")
        self.assertEqual(reply.metadata["task"]["domain"], "programming")
        self.assertEqual(reply.metadata["task"]["stages"][1]["name"], "analyze")
        self.assertIn("3060", reply.text)

    def test_cluster_can_process_next_queued_task(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 1, "cpu_io": 2, "interactive": 1})
        reservation = budget.try_acquire("gpu_heavy")
        cluster = AgentCluster(
            llm_client=lambda prompt: "先检查 traceback，再定位异常来源 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        queued_reply = cluster.process("这个 Python 报错应该怎么排查？")
        budget.release(reservation)
        reply = cluster.process_next_queued_task()

        self.assertEqual(queued_reply.metadata["task"]["status"], "queued")
        self.assertIsNotNone(reply)
        self.assertEqual(reply.agent_name, "programming")
        self.assertEqual(reply.metadata["task"]["status"], "complete")
        self.assertEqual(reply.metadata["task"]["current_stage"], "validate")
        self.assertEqual(reply.metadata["task"]["finalizer"]["decision"], "commit")
        self.assertTrue(reply.metadata["task"]["finalizer"]["verified"])
        self.assertIn("traceback", reply.metadata["task"]["result_summary"])

    def test_cluster_queued_task_failure_snapshot_contains_retry_finalizer(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 1, "cpu_io": 2, "interactive": 1})
        reservation = budget.try_acquire("gpu_heavy")

        def failing_llm(prompt: str) -> str:
            raise RuntimeError("llm failed")

        cluster = AgentCluster(
            llm_client=failing_llm,
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        queued_reply = cluster.process("这个 Python 报错应该怎么排查？")
        budget.release(reservation)
        reply = cluster.process_next_queued_task()

        self.assertEqual(queued_reply.metadata["task"]["status"], "queued")
        self.assertIsNotNone(reply)
        self.assertEqual(reply.metadata["task"]["status"], "failed")
        self.assertEqual(reply.metadata["task"]["finalizer"]["decision"], "retry")
        self.assertFalse(reply.metadata["task"]["finalizer"]["verified"])

    def test_latest_wins_request_waits_for_resource_instead_of_queue_reply(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 1, "cpu_io": 2, "interactive": 1})
        reservation = budget.try_acquire("gpu_heavy")
        release_started = threading.Event()

        def release_resource():
            release_started.wait(1.0)
            budget.release(reservation)

        thread = threading.Thread(target=release_resource)
        thread.start()
        cluster = AgentCluster(
            llm_client=lambda prompt: "可以，今天云不多 <emotion:neutral>",
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        release_started.set()
        reply = cluster.process(
            "今天云多吗",
            channel="desktop",
            input_metadata={
                "client_id": "desktop",
                "session_id": "s1",
                "request_id": "r2",
                "interrupt_mode": "latest_wins",
                "supersedes_previous": True,
            },
        )
        thread.join(1.0)

        self.assertNotEqual(reply.metadata.get("response_kind"), "scheduler_busy")
        self.assertEqual(reply.agent_name, "general")
        self.assertIn("云", reply.text)

    def test_cancel_generation_supersedes_scope_without_calling_model(self):
        calls = []
        cluster = AgentCluster(
            llm_client=lambda prompt: calls.append(prompt) or "不应调用模型",
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process(
            "停止当前生成",
            channel="desktop",
            input_metadata={
                "client_id": "desktop",
                "session_id": "s1",
                "request_id": "cancel-r2",
                "cancelled_request_id": "r1",
                "control_action": "cancel_generation",
                "interrupt_mode": "latest_wins",
                "supersedes_previous": True,
            },
        )

        self.assertEqual(reply.metadata["response_kind"], "request_cancelled")
        self.assertEqual(reply.metadata["cancelled_request_id"], "r1")
        self.assertEqual(calls, [])

    def test_cluster_can_drain_multiple_queued_tasks(self):
        budget = ResourceBudgetGate(limits={"gpu_heavy": 1, "cpu_io": 2, "interactive": 1})
        reservation = budget.try_acquire("gpu_heavy")
        cluster = AgentCluster(
            llm_client=lambda prompt: "先定位问题，再给排查步骤 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
            resource_budget=budget,
        )

        first = cluster.process("这个 Python 报错应该怎么排查？")
        second = cluster.process("这个脚本异常应该怎么定位？")
        budget.release(reservation)

        replies = cluster.drain_queued_tasks(max_tasks=2)
        active_tasks = cluster.list_active_tasks()

        self.assertEqual(first.metadata["task"]["status"], "queued")
        self.assertEqual(second.metadata["task"]["status"], "queued")
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(reply.metadata["task"]["status"] == "complete" for reply in replies))
        self.assertEqual(active_tasks, [])

    def test_cluster_routes_pointer_actions_to_executor(self):
        executor = FakeExecutor(name="desktop", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("把鼠标移动到 128, 256")

        self.assertEqual(reply.agent_name, "executor_desktop")
        self.assertEqual(reply.emotion, "happy")
        self.assertEqual(reply.action, "execute_task")
        self.assertIn("fake executed", reply.text)
        self.assertEqual(executor.requests[0].operation, "move_pointer")
        self.assertEqual(executor.requests[0].params, {"x": 128, "y": 256})
        self.assertEqual(reply.metadata["execution"]["worker"]["worker_id"], "executor:desktop")
        self.assertEqual(reply.metadata["execution"]["worker_audit"]["capability_id"], "local_pc_move_pointer")
        self.assertEqual(reply.metadata["execution"]["metadata"]["capability_id"], "local_pc_move_pointer")
        self.assertEqual(reply.metadata["scheduler"]["hybrid_planner"]["task_plan"]["route"], "executor")
        self.assertIn("local_pc_move_pointer", reply.metadata["scheduler"]["hybrid_planner"]["analysis"]["required_capabilities"])
        recommendation = reply.metadata["scheduler"]["hybrid_planner"]["capability_recommendation"]
        self.assertEqual(recommendation["top_capability_id"], "local_pc_move_pointer")
        self.assertTrue(recommendation["candidates"][0]["schedulable"])

    def test_execution_retries_after_model_fixes_params(self):
        class FlakyExecutor:
            name = "flaky"

            def __init__(self):
                self.requests = []

            def supports(self, request):
                return request.target == "local_pc"

            def execute(self, request):
                self.requests.append(ExecutionRequest(request.target, request.operation, dict(request.params)))
                if request.params.get("x") == 128:
                    return ExecutionResult(
                        success=False,
                        message="坐标越界",
                        error_code="out_of_bounds",
                        data={"stderr": "x=128 exceeds screen width"},
                    )
                return ExecutionResult(success=True, message="fake executed after fix")

        executor = FlakyExecutor()
        retry_json = '{"action": "retry", "params": {"x": 64, "y": 256}, "reason": "缩小 x 坐标"}'
        cluster = AgentCluster(
            llm_client=lambda _prompt, **_kw: retry_json,
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("把鼠标移动到 128, 256")

        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertEqual(len(executor.requests), 2)
        self.assertEqual(executor.requests[0].params["x"], 128)
        self.assertEqual(executor.requests[1].params["x"], 64)
        retry_trace = reply.metadata.get("retry_trace")
        self.assertEqual(len(retry_trace), 1)
        self.assertEqual(retry_trace[0]["status"], "retry")

    def test_missing_dependency_repair_requires_confirmation_then_resumes_original_request(self):
        class RepairingExecutor:
            name = "repairing"

            def __init__(self):
                self.requests = []
                self.original_calls = 0

            def supports(self, request):
                return request.target in {"local_pc", "python"}

            def execute(self, request):
                self.requests.append(ExecutionRequest(request.target, request.operation, dict(request.params)))
                if request.target == "python":
                    return ExecutionResult(True, "dependency installed")
                self.original_calls += 1
                if self.original_calls == 1:
                    return ExecutionResult(
                        False,
                        "script failed",
                        error_code="module_not_found",
                        data={"stderr": "ModuleNotFoundError: No module named 'requests'"},
                    )
                return ExecutionResult(True, "original request recovered")

        repair_json = json.dumps(
            {
                "action": "retry",
                "params": {"x": 128, "y": 256},
                "repair_tool": {
                    "name": "python.install_package",
                    "arguments": {"package": "requests==2.32.3"},
                },
                "reason": "安装缺失依赖后重试",
            }
        )
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool-authz.json"),
                "SPIRITKIN_SELF_HEAL_LOG": str(Path(tmp) / "self-heal.jsonl"),
            },
            clear=False,
        ):
            executor = RepairingExecutor()
            cluster = AgentCluster(
                llm_client=lambda _prompt, **_kw: repair_json,
                device_backend=FakeDeviceBackend(),
                executors=[executor],
                pending_execution_path=str(Path(tmp) / "pending.json"),
            )

            confirmation = cluster.process("把鼠标移动到 128, 256")

            self.assertTrue(confirmation.requires_confirmation)
            self.assertEqual(confirmation.metadata["pending_target"], "python")
            self.assertEqual(confirmation.metadata["pending_operation"], "python.install_package")
            self.assertEqual(len(executor.requests), 1)
            self.assertEqual(cluster.pending_execution.continuation_request.operation, "move_pointer")

            reply = cluster.process("确认执行")

        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertEqual(reply.text, "original request recovered")
        self.assertEqual(
            [(item.target, item.operation) for item in executor.requests],
            [
                ("local_pc", "move_pointer"),
                ("python", "python.install_package"),
                ("local_pc", "move_pointer"),
            ],
        )
        self.assertTrue(reply.metadata["repair_execution"]["success"])
        self.assertIsNone(cluster.pending_execution)

    def test_full_access_missing_dependency_repair_runs_and_retries_without_second_confirmation(self):
        class RepairingExecutor:
            name = "repairing_full_access"

            def __init__(self):
                self.requests = []
                self.original_calls = 0

            def supports(self, request):
                return request.target in {"local_pc", "python"}

            def execute(self, request):
                self.requests.append((request.target, request.operation))
                if request.target == "python":
                    return ExecutionResult(True, "dependency installed")
                self.original_calls += 1
                if self.original_calls == 1:
                    return ExecutionResult(
                        False,
                        "script failed",
                        error_code="module_not_found",
                        data={"stderr": "No module named 'requests'"},
                    )
                return ExecutionResult(True, "recovered with full access")

        repair_json = (
            '{"action":"retry","params":{"x":128,"y":256},'
            '"repair_tool":{"name":"python.install_package","arguments":{"package":"requests==2.32.3"}},'
            '"reason":"install dependency"}'
        )
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool-authz.json"),
                "SPIRITKIN_SELF_HEAL_LOG": str(Path(tmp) / "self-heal.jsonl"),
            },
            clear=False,
        ):
            executor = RepairingExecutor()
            cluster = AgentCluster(
                llm_client=lambda _prompt, **_kw: repair_json,
                device_backend=FakeDeviceBackend(),
                executors=[executor],
            )

            reply = cluster.process(
                "把鼠标移动到 128, 256",
                input_metadata={"permission_mode": "full_access", "full_access_granted": True},
            )

        self.assertFalse(reply.requires_confirmation)
        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertEqual(
            executor.requests,
            [
                ("local_pc", "move_pointer"),
                ("python", "python.install_package"),
                ("local_pc", "move_pointer"),
            ],
        )
        self.assertEqual(reply.metadata["retry_trace"][0]["status"], "repair_succeeded")

    def test_missing_dependency_repair_still_passes_execution_safety(self):
        class MissingDependencyExecutor:
            name = "missing_dependency"

            def __init__(self):
                self.requests = []

            def supports(self, request):
                return request.target in {"local_pc", "python"}

            def execute(self, request):
                self.requests.append((request.target, request.operation))
                return ExecutionResult(
                    False,
                    "script failed",
                    error_code="module_not_found",
                    data={"stderr": "ModuleNotFoundError: No module named 'requests'"},
                )

        repair_json = (
            '{"action":"retry","params":{"x":128,"y":256},'
            '"repair_tool":{"name":"python.install_package","arguments":{"package":"requests==2.32.3"}}}'
        )
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"SPIRITKIN_TOOL_AUTHZ_PATH": str(Path(tmp) / "tool-authz.json")},
            clear=False,
        ), patch(
            "backend.tools.registry.evaluate_execution_safety",
            side_effect=[
                SafetyDecision(True),
                SafetyDecision(False, "safety_hard_stop_active", "hard stop"),
            ],
        ) as safety:
            executor = MissingDependencyExecutor()
            cluster = AgentCluster(
                llm_client=lambda _prompt, **_kw: repair_json,
                device_backend=FakeDeviceBackend(),
                executors=[executor],
            )

            reply = cluster.process(
                "把鼠标移动到 128, 256",
                input_metadata={"permission_mode": "full_access", "full_access_granted": True},
            )

        self.assertFalse(reply.metadata["execution"]["success"])
        self.assertEqual(reply.metadata["retry_trace"][0]["status"], "repair_rejected")
        self.assertEqual(reply.metadata["retry_trace"][0]["reason"], "safety_hard_stop_active")
        self.assertEqual(executor.requests, [("local_pc", "move_pointer")])
        self.assertEqual(safety.call_count, 2)

    def test_execution_stops_when_model_aborts_retry(self):
        class AlwaysFailExecutor:
            name = "always_fail"

            def __init__(self):
                self.calls = 0

            def supports(self, request):
                return request.target == "local_pc"

            def execute(self, request):
                self.calls += 1
                return ExecutionResult(
                    success=False,
                    message="设备缺失",
                    error_code="device_missing",
                    data={"stderr": "no such device"},
                )

        executor = AlwaysFailExecutor()
        abort_json = '{"action": "abort", "params": {}, "reason": "环境缺失无法改参"}'
        cluster = AgentCluster(
            llm_client=lambda _prompt, **_kw: abort_json,
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("把鼠标移动到 128, 256")

        self.assertFalse(reply.metadata["execution"]["success"])
        self.assertEqual(executor.calls, 1)
        self.assertEqual(reply.metadata["retry_trace"][0]["status"], "abort")

    def test_transient_execution_retries_without_llm_and_logs_self_heal(self):
        class TransientExecutor:
            name = "transient"

            def __init__(self):
                self.calls = 0

            def supports(self, request):
                return request.target == "local_pc"

            def execute(self, request):
                self.calls += 1
                if self.calls == 1:
                    return ExecutionResult(False, "connection reset", error_code="network_error", data={"stderr": "connection reset by peer"})
                return ExecutionResult(True, "recovered")

        with TemporaryDirectory() as tmp:
            executor = TransientExecutor()
            env = {
                "SPIRITKIN_EXECUTION_RETRY_BACKOFF_SECONDS": "0",
                "SPIRITKIN_SELF_HEAL_LOG": str(Path(tmp) / "self-heal.jsonl"),
            }
            with patch.dict(os.environ, env, clear=False):
                cluster = AgentCluster(
                    llm_client=lambda *_args, **_kwargs: self.fail("transient retry should not call LLM"),
                    device_backend=FakeDeviceBackend(),
                    executors=[executor],
                )
                reply = cluster.process("把鼠标移动到 128, 256")
                events = [json.loads(line) for line in Path(env["SPIRITKIN_SELF_HEAL_LOG"]).read_text(encoding="utf-8").splitlines()]

        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertEqual(executor.calls, 2)
        self.assertEqual(reply.metadata["retry_trace"][0]["kind"], "transient")
        self.assertEqual(events[0]["action"], "retry_scheduled")

    def test_cluster_persists_successful_execution_trajectory(self):
        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            with patch.dict("os.environ", {"SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path)}, clear=False):
                executor = FakeExecutor(name="desktop", supported_targets={"local_pc"})
                cluster = AgentCluster(
                    llm_client=lambda _: self.fail("动作执行不应调用 LLM"),
                    device_backend=FakeDeviceBackend(),
                    executors=[executor],
                )

                reply = cluster.process("把鼠标移动到 128, 256")

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(reply.metadata["execution"]["success"])
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["overall_success"])
            self.assertEqual(records[0]["metadata"]["source"], "agent_cluster.execution")
            self.assertEqual(records[0]["metadata"]["operation"], "move_pointer")
            self.assertEqual(records[0]["steps"][0]["metadata"]["worker_id"], "executor:desktop")

    def test_cluster_failure_trajectory_feeds_evolution_dataset_export(self):
        with TemporaryDirectory() as tmp:
            trajectory_path = Path(tmp) / "trajectories.jsonl"
            eval_cases_path = Path(tmp) / "eval_cases.jsonl"
            dataset_path = Path(tmp) / "evolution_dataset.jsonl"
            registry_path = Path(tmp) / "datasets.jsonl"
            with patch.dict(
                "os.environ",
                {
                    "SPIRITKIN_TRAJECTORY_LOG": str(trajectory_path),
                    "SPIRITKIN_EVOLUTION_EVAL_CASES": str(eval_cases_path),
                    "SPIRITKIN_EVOLUTION_DATASET": str(dataset_path),
                    "SPIRITKIN_DATASET_REGISTRY_PATH": str(registry_path),
                },
                clear=False,
            ):
                cluster = AgentCluster(
                    llm_client=lambda _: self.fail("缺执行器时不应调用 LLM"),
                    device_backend=FakeDeviceBackend(),
                )

                reply = cluster.process("让机械臂回零")
                eval_export = handle_evolution_management_action({"action": "export_eval_cases"})
                export = handle_evolution_management_action({"action": "export_self_training_dataset"})

            records = [json.loads(line) for line in trajectory_path.read_text(encoding="utf-8").splitlines()]
            eval_records = [json.loads(line) for line in eval_cases_path.read_text(encoding="utf-8").splitlines()]
            dataset_records = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(reply.agent_name, "executor_missing")
            self.assertEqual(records[0]["metadata"]["source"], "agent_cluster.failure")
            self.assertFalse(records[0]["overall_success"])
            self.assertEqual(records[0]["steps"][0]["error_code"], "executor_not_found")
            self.assertGreaterEqual(eval_export["eval_cases"]["count"], 1)
            self.assertEqual(eval_records[0]["source"], "trajectory")
            self.assertIn("让机械臂回零", eval_records[0]["user_input"])
            self.assertGreaterEqual(export["dataset"]["count"], 1)
            self.assertEqual(Path(export["dataset_card"]["linked_eval_report"]).resolve(), eval_cases_path.resolve())
            self.assertIn("让机械臂回零", dataset_records[0]["messages"][1]["content"])
            self.assertEqual(dataset_records[0]["metadata"]["source"], "trajectory")

    def test_cluster_routes_colloquial_app_launch_to_software_executor(self):
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("软件动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("帮我打开飞书")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(executor.requests[0].params, {"app_name": "Feishu"})

    def test_cluster_routes_cantonese_app_launch_to_software_executor(self):
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("粤语软件动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("幫我打開飛書")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(executor.requests[0].params, {"app_name": "Feishu"})

    def test_cluster_routes_default_browser_launch_to_software_executor(self):
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("浏览器动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
            voice_intent_mode="fallback",
        )

        reply = cluster.process("打开默认浏览器", channel="voice")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(reply.metadata["scheduler"]["route"], "executor")
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(executor.requests[0].params, {"app_name": "browser"})

    def test_cluster_routes_mixed_language_edge_browser_launch(self):
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("Edge 浏览器动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("打开 Edge 浏览器")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(executor.requests[0].params, {"app_name": "msedge"})

    def test_cluster_routes_other_mixed_language_browser_launches(self):
        cases = [
            ("打开 Firefox 浏览器", "firefox"),
            ("打开 Chrome 浏览器", "chrome"),
            ("打开 Brave 浏览器", "brave"),
            ("打开 360 浏览器", "360浏览器"),
        ]
        for command, expected_app in cases:
            with self.subTest(command=command):
                executor = FakeExecutor(name="software", supported_targets={"local_pc"})
                cluster = AgentCluster(
                    llm_client=lambda _: self.fail("浏览器动作执行不应调用 LLM"),
                    device_backend=FakeDeviceBackend(),
                    executors=[executor],
                )

                reply = cluster.process(command)

                self.assertEqual(reply.agent_name, "executor_software")
                self.assertEqual(executor.requests[0].operation, "launch_app")
                self.assertEqual(executor.requests[0].params, {"app_name": expected_app})

    def test_cluster_routes_asr_new_browser_error_to_edge(self):
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("浏览器动作执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("打开新的浏览器")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(executor.requests[0].params, {"app_name": "msedge"})

    def test_cluster_routes_local_software_inventory_scan(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("扫描本机软件不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("扫描本机软件")

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(reply.metadata["execution"]["operation"], "list_installed_apps")
        self.assertTrue(reply.metadata["execution"]["success"])
        self.assertIn("发现 1 条记录", reply.text)

    def test_cluster_confirms_then_closes_app_with_asr_alias(self):
        device_backend = FakeDeviceBackend()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("关闭应用不应调用 LLM"),
            device_backend=device_backend,
        )

        confirmation = cluster.process("关闭火爆浏览器")
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_operation"], "close_app")
        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertIn("已请求关闭 火豹浏览器", reply.text)
        self.assertEqual(device_backend.closed_apps, [("火豹浏览器", False)])

    def test_cluster_can_confirm_pending_execution_across_runtime_instances(self):
        with TemporaryDirectory() as temp_dir:
            pending_path = Path(temp_dir) / "pending_execution.json"
            first_backend = FakeDeviceBackend()
            second_backend = FakeDeviceBackend()
            first_cluster = AgentCluster(
                llm_client=lambda _: self.fail("关闭应用不应调用 LLM"),
                device_backend=first_backend,
                pending_execution_path=pending_path,
            )
            second_cluster = AgentCluster(
                llm_client=lambda _: self.fail("确认执行不应调用 LLM"),
                device_backend=second_backend,
                pending_execution_path=pending_path,
            )

            confirmation = first_cluster.process("关闭火爆浏览器", channel="voice")
            reply = second_cluster.process("确认执行", channel="avatar_3d")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertIn("已请求关闭 火豹浏览器", reply.text)
        self.assertEqual(first_backend.closed_apps, [])
        self.assertEqual(second_backend.closed_apps, [("火豹浏览器", False)])
        self.assertFalse(pending_path.exists())

    def test_cluster_does_not_treat_orphan_confirmation_as_general_command(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("没有 pending 时确认控制词不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("确认执行", channel="desktop")

        self.assertEqual(reply.agent_name, "execution_guard")
        self.assertIn("当前没有等待确认", reply.text)
        self.assertFalse(reply.requires_confirmation)

    def test_cluster_falls_back_when_intent_close_app_lacks_app_name(self):
        cluster = AgentCluster(
            llm_client=lambda _: '{"intent":"execute","tool_name":"app.close","params":{},"confidence":0.92}',
            device_backend=FakeDeviceBackend(),
        )

        confirmation = cluster.process("关闭火豹浏览器", channel="desktop")
        reply = cluster.process("确认执行", channel="desktop")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_operation"], "close_app")
        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertIn("火豹浏览器", reply.text)

    def test_cluster_clears_in_memory_pending_when_shared_pending_file_is_consumed(self):
        with TemporaryDirectory() as temp_dir:
            pending_path = Path(temp_dir) / "pending_execution.json"
            first_cluster = AgentCluster(
                llm_client=lambda _: self.fail("关闭应用不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                pending_execution_path=pending_path,
            )
            second_cluster = AgentCluster(
                llm_client=lambda _: self.fail("确认执行不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                pending_execution_path=pending_path,
            )

            first_cluster.process("关闭火爆浏览器", channel="voice")
            self.assertIsNotNone(first_cluster.pending_execution)
            second_cluster.process("确认执行", channel="avatar_3d")

            self.assertIsNone(first_cluster.pending_execution)

    def test_cluster_routes_local_hardware_inventory_scan(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("扫描本机硬件设备不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("列出本机硬件设备")

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(reply.metadata["execution"]["operation"], "list_hardware_devices")

    def test_cluster_uses_intent_resolver_for_unmatched_action_request(self):
        prompts = []
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"execute","tool_name":"app.launch","params":{"app_name":"Feishu"},"confidence":0.92,"reason":"用户想打开飞书"}'

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("麻烦把飞书开起来")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(executor.requests[0].params, {"app_name": "Feishu"})
        self.assertIn("You are a voice assistant", prompts[0])
        self.assertEqual(reply.metadata["intent_resolution"]["source"], "llm_fallback")

    def test_cluster_intent_resolver_keeps_confirmation_for_high_risk_feishu_send(self):
        prompts = []
        feishu_client = FakeFeishuClient()

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"execute","tool_name":"feishu.message.send","params":{"recipient":"运营群","text":"下午三点发版"},"confidence":0.88,"reason":"同步消息"}'

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            feishu_client=feishu_client,
        )

        confirmation = cluster.process("帮我把这件事同步到运营群：下午三点发版")
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["intent_resolution"]["source"], "llm_fallback")
        self.assertEqual(reply.agent_name, "executor_feishu")
        self.assertEqual(feishu_client.calls, [("运营群", "下午三点发版")])

    def test_cluster_intent_resolver_can_ask_for_clarification(self):
        def fake_llm(prompt: str) -> str:
            return '{"intent":"clarify","confidence":0.44,"message":"你想把消息发给谁？","reason":"缺少接收人"}'

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("帮我发个消息说下午三点发版")

        self.assertEqual(reply.agent_name, "intent_resolver")
        self.assertEqual(reply.metadata["response_kind"], "intent_clarification")
        self.assertIn("发给谁", reply.text)

    def test_cluster_can_understand_current_screen_as_read_only_action(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("屏幕理解动作不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("看一下屏幕上有什么")

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertIn("已完成当前屏幕理解", reply.text)
        self.assertEqual(reply.metadata["execution"]["operation"], "screen_understand")

    def test_cluster_routes_browser_search_to_desktop_executor(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("浏览器搜索不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("搜索 SpiritKinAI 项目")

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(reply.metadata["execution"]["operation"], "browser_search")
        self.assertEqual(reply.metadata["execution"]["data"]["query"], "SpiritKinAI 项目")

    def test_cluster_routes_backend_web_search_to_tool(self):
        prompts = []
        web_tool = FakeWebSearchTool()
        registry = ToolRegistry([web_tool])

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "网上结果显示 SpiritKinAI 有最新状态 <emotion:thinking>"

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            tool_registry=registry,
        )

        reply = cluster.process("联网搜索 SpiritKinAI 最新状态")

        self.assertEqual(reply.agent_name, "tool_web_search")
        self.assertEqual(web_tool.calls[0]["query"], "SpiritKinAI 最新状态")
        self.assertIn("联网搜索结果", prompts[0])
        self.assertIn("https://example.com/spiritkin", prompts[0])
        self.assertEqual(reply.metadata["web_search"]["results"][0]["provider"], "fake")

    def test_cluster_routes_web_search_when_desktop_toggle_enabled(self):
        web_tool = FakeWebSearchTool()
        registry = ToolRegistry([web_tool])
        cluster = AgentCluster(
            llm_client=lambda _: "基于联网结果回答 <emotion:thinking>",
            device_backend=FakeDeviceBackend(),
            tool_registry=registry,
        )

        reply = cluster.process("SpiritKinAI 状态", channel="desktop", input_metadata={"web_search_enabled": True})

        self.assertEqual(reply.agent_name, "tool_web_search")
        self.assertEqual(web_tool.calls[0]["query"], "SpiritKinAI 状态")

    def test_cluster_does_not_auto_web_search_when_desktop_toggle_disabled(self):
        web_tool = FakeWebSearchTool()
        registry = ToolRegistry([web_tool])
        cluster = AgentCluster(
            llm_client=lambda _: "普通回答 <emotion:neutral>",
            device_backend=FakeDeviceBackend(),
            tool_registry=registry,
        )

        reply = cluster.process("SpiritKinAI 最新状态", channel="desktop", input_metadata={"web_search_enabled": False})

        self.assertNotEqual(reply.agent_name, "tool_web_search")
        self.assertEqual(web_tool.calls, [])

    def test_cluster_prefers_rule_browser_search_over_desktop_intent_first(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("明确浏览器搜索不应被 desktop intent-first 覆盖"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("打开浏览器并搜索AI相关论文", channel="desktop")

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(reply.metadata["execution"]["operation"], "browser_search")
        self.assertEqual(reply.metadata["execution"]["data"]["query"], "AI相关论文")
        self.assertEqual(reply.metadata["execution"]["data"]["engine"], "bing")

    def test_cluster_persists_browser_search_pending_params(self):
        with TemporaryDirectory() as temp_dir:
            pending_path = Path(temp_dir) / "pending_execution.json"
            registry = ToolRegistry()
            registry.register(
                ExecutionTool(
                    ToolSpec(
                        "browser.search",
                        "search",
                        "local_pc",
                        "browser_search",
                        risk_level="high",
                        schema={"query": "str", "engine": "str"},
                    )
                )
            )
            executor = FakeExecutor(name="local_pc", supported_targets={"local_pc"})
            first_cluster = AgentCluster(
                llm_client=lambda _: self.fail("明确浏览器搜索不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                executors=[executor],
                tool_registry=registry,
                pending_execution_path=pending_path,
            )
            second_cluster = AgentCluster(
                llm_client=lambda _: self.fail("确认执行不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                executors=[executor],
                tool_registry=registry,
                pending_execution_path=pending_path,
            )

            confirmation = first_cluster.process("打开浏览器并搜索AI相关论文", channel="desktop")
            pending = first_cluster.pending_execution
            reply = second_cluster.process("确认执行", channel="desktop")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.request.operation, "browser_search")
        self.assertEqual(pending.request.params, {"query": "AI相关论文", "engine": "bing"})
        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(executor.requests[0].params, {"query": "AI相关论文", "engine": "bing"})

    def test_cluster_confirms_before_reading_clipboard(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("剪贴板读取不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        confirmation = cluster.process("读取剪贴板")
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_operation"], "clipboard_read")
        self.assertEqual(reply.metadata["execution"]["operation"], "clipboard_read")
        self.assertIn("已读取剪贴板", reply.text)

    def test_cluster_confirms_before_closing_window(self):
        device_backend = FakeDeviceBackend()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("关闭窗口不应调用 LLM"),
            device_backend=device_backend,
        )

        confirmation = cluster.process("关闭 SpiritKinAI 窗口")
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_operation"], "window_close")
        self.assertEqual(device_backend.closed_windows, [("SpiritKinAI", False)])
        self.assertIn("已请求关闭", reply.text)

    def test_cluster_routes_file_search_as_read_only_action(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("文件搜索不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process('搜索文件 "handoff"')

        self.assertEqual(reply.agent_name, "executor_local_pc")
        self.assertEqual(reply.metadata["execution"]["operation"], "file_search")
        self.assertIn("发现 1 个匹配项", reply.text)

    def test_cluster_confirms_before_file_read(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("文件读取不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        confirmation = cluster.process('读取文件 "docs/archive/tmp_agent_handoff_2026-04-17.md"')
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_operation"], "file_read")
        self.assertEqual(reply.metadata["execution"]["operation"], "file_read")
        self.assertIn("已读取文件", reply.text)

    def test_cluster_reports_missing_executor_for_openclaw_actions(self):
        cluster = AgentCluster(llm_client=lambda _: self.fail("缺执行器时不应调用 LLM"), device_backend=FakeDeviceBackend())

        reply = cluster.process("让机械臂回零")

        self.assertEqual(reply.agent_name, "executor_missing")
        self.assertEqual(reply.emotion, "confused")
        self.assertIn("当前没有可用执行器", reply.text)

    def test_cluster_records_structured_failure_and_repair_advice(self):
        advisor = FakeRepairAdvisor()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("缺执行器时不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            repair_advisor=advisor,
        )

        cluster.process("让机械臂回零")

        self.assertEqual(len(cluster.recent_failures), 1)
        failure = cluster.recent_failures[0]
        self.assertEqual(failure.stage, "executor")
        self.assertEqual(failure.error_code, "executor_not_found")
        self.assertEqual(failure.execution_target, "openclaw")
        self.assertEqual(failure.execution_operation, "home")
        self.assertEqual(failure.user_input, "让机械臂回零")
        self.assertEqual(advisor.failures[0].error_code, "executor_not_found")
        self.assertIsNotNone(cluster.last_repair_advice)
        self.assertIn("executor_not_found", cluster.last_repair_advice.summary)
        report = cluster.build_self_improvement_report()
        self.assertEqual(report.trajectory["failure_count"], 1)
        self.assertGreaterEqual(report.trajectory["eval_cases_available"], 1)

    def test_cluster_can_build_rule_based_repair_plan_from_last_failure(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("缺执行器时不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        cluster.process("让机械臂回零")
        repair_plan = cluster.build_repair_plan()

        self.assertIsNotNone(repair_plan)
        self.assertEqual(repair_plan.failure.error_code, "executor_not_found")
        self.assertTrue(repair_plan.requires_human_review)
        self.assertIn("backend/orchestrator/agent_cluster.py", repair_plan.candidate_files)
        self.assertIn("backend/orchestrator/planner.py", repair_plan.candidate_files)
        self.assertIn("python -m unittest backend.tests.unit.test_agent_cluster -v", repair_plan.suggested_test_commands)

    def test_cluster_can_build_development_plan_for_feishu_integration(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("构建开发计划不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        plan = cluster.build_development_plan("请新增飞书消息和审批 API 接入，先给开发计划并人工审核")

        self.assertIn("feishu", plan.target_integrations)
        self.assertTrue(plan.requires_human_review)
        self.assertFalse(plan.safe_to_auto_apply)
        self.assertIn("backend/tools/feishu_tools.py", plan.candidate_files)
        self.assertIn("backend/services/feishu_client.py", plan.candidate_files)
        self.assertIn("backend/tools/registry.py", plan.candidate_files)
        self.assertEqual(plan.metadata["delivery_mode"], "human_reviewed_self_development")

    def test_cluster_can_build_development_plan_for_code_editor_with_api_first_strategy(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("构建开发计划不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        plan = cluster.build_development_plan("接入代码编辑器，要求优先扩展或 API，界面点击只做兜底")

        self.assertIn("code_editor", plan.target_integrations)
        self.assertIn("backend/tools/editor_tools.py", plan.candidate_files)
        self.assertIn("backend/tools/desktop_tools.py", plan.candidate_files)
        self.assertTrue(any("API / SDK / 扩展桥接" in action for action in plan.suggested_actions))
        self.assertTrue(any("显式确认" in check or "确认" in check for check in plan.acceptance_checks))

    def test_cluster_can_return_structured_development_plan_reply_from_process(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("开发计划请求不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
        )

        reply = cluster.process("请给我一个飞书接入开发计划，要求优先 API 并人工审核")

        self.assertEqual(reply.agent_name, "development_planner")
        self.assertEqual(reply.action, "plan_development")
        self.assertEqual(reply.metadata["response_kind"], "development_plan")
        self.assertIn("feishu", reply.metadata["development_plan"]["target_integrations"])
        self.assertIn("backend/tools/feishu_tools.py", reply.text)
        self.assertIn("人工审核", reply.spoken_text)

    def test_cluster_can_attach_openclaw_executor_from_client_factory(self):
        client = FakeOpenClawClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: client,
        )

        reply = cluster.process("让机械臂回零")

        self.assertEqual(reply.agent_name, "execution_guard")
        self.assertTrue(reply.requires_confirmation)
        self.assertEqual(reply.action, "await_confirmation")
        self.assertEqual(reply.metadata["response_kind"], "confirmation_request")
        self.assertIn("确认执行", reply.text)
        self.assertEqual(client.calls, [])

    def test_cluster_executes_high_risk_action_after_confirmation(self):
        client = FakeOpenClawClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: client,
        )

        cluster.process("让机械臂回零")
        reply = cluster.process("确认执行")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(reply.emotion, "happy")
        self.assertEqual(reply.action, "execute_task")
        self.assertEqual(reply.text, "OpenClaw 已回零。")
        self.assertEqual(client.calls[-1], ("home", {}))
        self.assertIsNone(cluster.pending_execution)

    def test_cluster_full_access_executes_without_second_confirmation(self):
        client = FakeOpenClawClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: client,
        )

        reply = cluster.process(
            "让机械臂回零",
            channel="desktop",
            input_metadata={"permission_mode": "full_access", "full_access_granted": True},
        )

        self.assertFalse(reply.requires_confirmation)
        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(client.calls, [("home", {})])
        self.assertIsNone(cluster.pending_execution)

    def test_confirmation_control_rejects_mismatched_pending_context(self):
        client = FakeOpenClawClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: client,
        )

        cluster.process("让机械臂回零")
        reply = cluster.process(
            "确认执行",
            channel="desktop",
            input_metadata={
                "confirmation_control": True,
                "pending_target": "local_pc",
                "pending_operation": "close_app",
            },
        )

        self.assertEqual(reply.metadata["response_kind"], "confirmation_mismatch")
        self.assertTrue(reply.requires_confirmation is False)
        self.assertEqual(client.calls, [])
        self.assertIsNotNone(cluster.pending_execution)

    def test_confirmation_control_blocks_duplicate_confirmation_reply(self):
        class ReconfirmingCluster(AgentCluster):
            def _handle_execution(self, request: ExecutionRequest, user_input: str = "", skip_confirmation: bool = False):
                return self._build_confirmation_reply(
                    self._execution_guard.build_pending_execution(
                        request=request,
                        available_tools=self.available_tools,
                        original_user_input=user_input,
                    )
                )

        registry = ToolRegistry()
        registry.register(ExecutionTool(ToolSpec("danger.do", "danger", "danger", "do", risk_level="high")))
        cluster = ReconfirmingCluster(
            llm_client=lambda _: self.fail("确认流程不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            executors=[FakeExecutor(name="danger", supported_targets={"danger"})],
            tool_registry=registry,
        )
        cluster._pending_execution = cluster._execution_guard.build_pending_execution(
            request=ExecutionRequest("danger", "do", {}),
            available_tools=cluster.available_tools,
            original_user_input="执行危险操作",
        )

        reply = cluster.process(
            "确认执行",
            channel="desktop",
            input_metadata={
                "confirmation_control": True,
                "pending_target": "danger",
                "pending_operation": "do",
            },
        )

        self.assertEqual(reply.metadata["response_kind"], "confirmation_failed")
        self.assertFalse(reply.requires_confirmation)
        self.assertIsNone(cluster.pending_execution)

    def test_cluster_can_cancel_high_risk_action(self):
        client = FakeOpenClawClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: client,
        )

        cluster.process("让机械臂回零")
        reply = cluster.process("取消")

        self.assertEqual(reply.agent_name, "execution_guard")
        self.assertEqual(reply.action, "cancel_execution")
        self.assertIn("已取消", reply.text)
        self.assertEqual(client.calls, [])
        self.assertIsNone(cluster.pending_execution)

    def test_cluster_keeps_running_when_openclaw_executor_init_fails(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("缺执行器时不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client_factory=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        reply = cluster.process("让机械臂回零")

        self.assertEqual(reply.agent_name, "executor_missing")
        self.assertIn("当前没有可用执行器", reply.text)

    def test_cluster_exposes_default_tool_specs(self):
        cluster = AgentCluster(llm_client=lambda _: "ok <emotion:neutral>", device_backend=FakeDeviceBackend())

        tool_names = {tool.name for tool in cluster.available_tools}

        self.assertIn("feishu.message.send", tool_names)
        self.assertIn("arm.status", tool_names)
        self.assertIn("app.launch", tool_names)
        self.assertIn("screen.ask", tool_names)
        self.assertIn("screen.capture", tool_names)
        self.assertIn("clipboard.read", tool_names)
        self.assertIn("clipboard.write", tool_names)
        self.assertIn("browser.open_url", tool_names)
        self.assertIn("browser.search", tool_names)
        self.assertIn("file.search", tool_names)
        self.assertIn("file.read", tool_names)
        self.assertIn("file.open", tool_names)
        self.assertIn("window.list", tool_names)
        self.assertIn("window.activate", tool_names)
        self.assertIn("window.close", tool_names)
        self.assertIn("pointer.move", tool_names)
        self.assertIn("arm.home", tool_names)
        self.assertIn("kb.search", tool_names)
        self.assertIn("software.list_installed", tool_names)
        self.assertIn("hardware.list_devices", tool_names)

        tool_by_name = {tool.name: tool for tool in cluster.available_tools}
        self.assertTrue(tool_by_name["software.list_installed"].read_only)
        self.assertEqual(tool_by_name["hardware.list_devices"].operation, "list_hardware_devices")
        self.assertEqual(tool_by_name["clipboard.read"].risk_level, "high")
        self.assertEqual(tool_by_name["file.read"].risk_level, "high")
        self.assertEqual(tool_by_name["window.close"].risk_level, "high")

    def test_cluster_exposes_injected_skill_specs(self):
        cluster = AgentCluster(
            llm_client=lambda _: "ok <emotion:neutral>",
            device_backend=FakeDeviceBackend(),
            skill_registry=SkillRegistry([SkillSpec(name="browser.search", description="打开浏览器并搜索")]),
        )

        self.assertEqual([skill.name for skill in cluster.available_skills], ["browser.search"])

    def test_skill_assist_policy_can_block_skill_before_run(self):
        with TemporaryDirectory() as tmp:
            previous = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            try:
                from backend.app.agent_management import save_agent_management_state

                save_agent_management_state(
                    {
                        "skill_assist": {
                            "enabled": True,
                            "mode": "human_review",
                            "require_before_run": True,
                            "require_on_failure": True,
                        }
                    }
                )
                skill = SkillSpec(name="workflow.demo", description="demo")
                cluster = AgentCluster(
                    llm_client=lambda _: "ok <emotion:neutral>",
                    device_backend=FakeDeviceBackend(),
                    skill_registry=SkillRegistry([skill]),
                    app_port=DefaultAgentClusterAppPort(),
                )
                reply = cluster._handle_skill(skill, AgentContext(user_input="执行 demo skill"))
            finally:
                if previous is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous

        self.assertTrue(reply.requires_confirmation)
        self.assertEqual(reply.agent_name, "skill_assist")
        self.assertEqual(reply.metadata["response_kind"], "skill_assist_required")
        self.assertEqual(reply.metadata["skill_assist"]["skill_name"], "workflow.demo")

    def test_skill_failure_assist_uses_multi_model_review_when_enabled(self):
        with TemporaryDirectory() as tmp:
            previous_agent = os.environ.get("SPIRITKIN_AGENT_MANAGEMENT_PATH")
            previous_learning = os.environ.get("SPIRITKIN_LEARNING_LOG")
            previous_dataset = os.environ.get("SPIRITKIN_LEARNING_DATASET")
            os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = str(Path(tmp) / "agent_management.json")
            os.environ["SPIRITKIN_LEARNING_LOG"] = str(Path(tmp) / "learning.jsonl")
            os.environ["SPIRITKIN_LEARNING_DATASET"] = str(Path(tmp) / "dataset.jsonl")
            try:
                from backend.app.agent_management import save_agent_management_state
                from backend.app.learning_workflow import ModelReviewResult, MultiModelReviewResult

                save_agent_management_state(
                    {
                        "skill_assist": {
                            "enabled": True,
                            "mode": "cloud_model_review",
                            "require_before_run": False,
                            "require_on_failure": True,
                            "allow_external_model": True,
                        }
                    }
                )
                multi = MultiModelReviewResult(
                    ok=True,
                    prompt="review",
                    reviews=(
                        ModelReviewResult(True, "openai_compatible", "gpt", "review", "gpt fix"),
                        ModelReviewResult(True, "anthropic", "opus", "review", "opus fix"),
                    ),
                )
                skill = SkillSpec(
                    name="workflow.fail",
                    description="fail",
                    steps=(SkillStepSpec("file.delete", {"path": "demo.txt"}),),
                    tool_allowlist=("allowed.tool",),
                )
                cluster = AgentCluster(
                    llm_client=lambda _: "ok <emotion:neutral>",
                    device_backend=FakeDeviceBackend(),
                    skill_registry=SkillRegistry([skill]),
                    app_port=DefaultAgentClusterAppPort(),
                )
                with patch("backend.app.learning_workflow.request_multi_model_review", return_value=multi) as request_multi:
                    reply = cluster._handle_skill(skill, AgentContext(user_input="执行失败 skill"))
            finally:
                if previous_agent is None:
                    os.environ.pop("SPIRITKIN_AGENT_MANAGEMENT_PATH", None)
                else:
                    os.environ["SPIRITKIN_AGENT_MANAGEMENT_PATH"] = previous_agent
                if previous_learning is None:
                    os.environ.pop("SPIRITKIN_LEARNING_LOG", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_LOG"] = previous_learning
                if previous_dataset is None:
                    os.environ.pop("SPIRITKIN_LEARNING_DATASET", None)
                else:
                    os.environ["SPIRITKIN_LEARNING_DATASET"] = previous_dataset

        assist = reply.metadata["skill_assist"]
        self.assertEqual(reply.metadata["response_kind"], "skill_failed_with_assist")
        self.assertEqual(assist["multi_model_review"]["success_count"], 2)
        self.assertEqual(assist["model_review"]["response_text"], "gpt fix")
        request_multi.assert_called_once()

    def test_cluster_can_read_openclaw_status_without_confirmation(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂状态查询不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
        )

        reply = cluster.process("看一下机械臂当前状态")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertFalse(reply.requires_confirmation)
        self.assertEqual(reply.metadata["response_kind"], "execution_result")
        self.assertEqual(reply.metadata["execution"]["operation"], "status")
        self.assertIn("OpenClaw 当前状态", reply.text)

    def test_cluster_can_attach_openclaw_executor_from_http_env(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"state":"idle","position":{"x":0,"y":0,"z":0},"gripper_opened":true}'

        with patch.dict("os.environ", {"SPIRITKIN_OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:9000"}, clear=False), patch(
            "backend.devices.openclaw.request.urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            cluster = AgentCluster(
                llm_client=lambda _: self.fail("机械臂状态查询不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
            )
            reply = cluster.process("看一下机械臂当前状态")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(reply.metadata["execution"]["operation"], "status")
        self.assertEqual(reply.metadata["execution"]["data"]["transport"], "http")
        self.assertEqual(urlopen.call_args[0][0].full_url, "http://127.0.0.1:9000/status")

    def test_cluster_routes_noisy_asr_openclaw_status_without_llm(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("ASR 错字的机械臂状态查询不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
        )

        reply = cluster.process("機械B現在從狀態")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(reply.metadata["execution"]["operation"], "status")
        self.assertIn("OpenClaw 当前状态", reply.text)

    def test_cluster_routes_noisy_asr_openclaw_status_typo_without_llm(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂状态 ASR 错字不应掉到 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
        )

        reply = cluster.process("機械B現在怎麼裝它")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(reply.metadata["execution"]["operation"], "status")

    def test_voice_channel_can_prefer_intent_resolver_for_asr_correction(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"execute","tool_name":"arm.status","params":{},"corrected_text":"机械臂现在什么状态","confidence":0.93,"reason":"语音纠错后查询机械臂状态"}'

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
            voice_intent_mode="always",
        )

        reply = cluster.process("機械B現在怎麼裝它", channel="voice")

        self.assertEqual(reply.agent_name, "executor_openclaw")
        self.assertEqual(reply.metadata["execution"]["operation"], "status")
        self.assertEqual(reply.metadata["intent_resolution"]["source"], "llm_voice_first")
        self.assertEqual(reply.metadata["intent_resolution"]["corrected_text"], "机械臂现在什么状态")
        self.assertIn("correct ASR errors first", prompts[0])
        self.assertIn("機械B", prompts[0])

    def test_voice_channel_defaults_to_intent_resolver_for_action_correction(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"execute","tool_name":"app.launch","params":{"app_name":"火豹浏览器"},"corrected_text":"打开火豹浏览器","confidence":0.92,"reason":"语音纠错后打开应用"}'

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            executors=[FakeExecutor(name="software", supported_targets={"local_pc"})],
        )

        reply = cluster.process("打开火爆浏览器", channel="voice")

        self.assertEqual(reply.metadata["intent_resolution"]["source"], "llm_voice_first")
        self.assertEqual(reply.metadata["execution"]["operation"], "launch_app")
        self.assertEqual(reply.metadata["execution"]["success"], True)
        self.assertTrue(prompts, "voice 输入应触发 IntentResolver prompt")
        self.assertIn("打开火爆浏览器", prompts[0])

    def test_mobile_channel_prefers_intent_resolver_before_planner_regex(self):
        prompts = []
        executor = FakeExecutor(name="software", supported_targets={"local_pc"})

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"execute","tool_name":"app.launch","params":{"app_name":"Feishu"},"confidence":0.93,"reason":"移动端自然语言先交给意图调度"}'

        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            executors=[executor],
        )

        reply = cluster.process("打开飞书", channel="mobile")

        self.assertEqual(reply.agent_name, "executor_software")
        self.assertEqual(executor.requests[0].operation, "launch_app")
        self.assertEqual(reply.metadata["intent_resolution"]["source"], "llm_mobile_first")
        self.assertEqual(reply.metadata["scheduler"]["route"], "intent")
        self.assertTrue(prompts)

    def test_inventory_scan_is_injected_into_later_intent_resolver_prompt(self):
        prompts = []

        class InventoryDeviceBackend(FakeDeviceBackend):
            def list_installed_apps(self, limit=80):
                return [{"name": "DaVinci Resolve", "version": "19", "publisher": "Blackmagic", "can_launch": True}]

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"none","confidence":0.1,"reason":"只检查库存上下文"}'

        cluster = AgentCluster(llm_client=fake_llm, device_backend=InventoryDeviceBackend(), voice_intent_mode="fallback")
        scan_reply = cluster.process("扫描本机软件")
        cluster.process("麻烦把剪辑工具开起来")

        self.assertEqual(scan_reply.metadata["inventory_update"]["kind"], "software")
        self.assertEqual(scan_reply.metadata["inventory_update"]["count"], 1)
        self.assertEqual(scan_reply.metadata["inventory_update"]["scope"], "local_pc")
        self.assertIn("DaVinci Resolve(可启动)", prompts[0])

    def test_remote_inventory_scan_is_grouped_by_node_in_later_prompt(self):
        prompts = []

        class RemoteInventoryExecutor(FakeExecutor):
            def execute(self, request):
                self.requests.append(request)
                return ExecutionResult(
                    success=True,
                    message="remote software scanned",
                    data=[{"name": "火豹浏览器", "version": "1.0", "can_launch": True}],
                )

        remote_executor = RemoteInventoryExecutor(name="remote_software", supported_targets={"local_pc"})
        node_registry = NodeRegistry(
            [
                RemoteNode(
                    node_id="office-pc",
                    client=ExecutorRemoteNodeClient([remote_executor]),
                    targets={"local_pc"},
                    aliases={"公司电脑"},
                )
            ]
        )

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return '{"intent":"none","confidence":0.1,"reason":"只检查远端库存上下文"}'

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend(), node_registry=node_registry, voice_intent_mode="fallback")
        scan_reply = cluster.process("在公司电脑上扫描软件")
        cluster.process("麻烦把公司电脑上的火爆浏览器开起来")

        self.assertEqual(scan_reply.agent_name, "executor_remote")
        self.assertEqual(scan_reply.metadata["inventory_update"]["scope"], "remote:office-pc:local_pc")
        self.assertEqual(scan_reply.metadata["inventory_update"]["node_id"], "office-pc")
        self.assertIn("[office-pc] 软件=火豹浏览器(可启动)", prompts[0])

    def test_cluster_clarifies_ambiguous_openclaw_voice_without_llm(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("疑似机械臂语音不应掉到 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
        )

        reply = cluster.process("机械臂那个怎么弄")

        self.assertEqual(reply.agent_name, "openclaw_intent_clarifier")
        self.assertEqual(reply.metadata["response_kind"], "intent_clarification")

    def test_cluster_routes_noisy_asr_gripper_commands_without_llm(self):
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("夹爪 ASR 错字不应掉到 LLM"),
            device_backend=FakeDeviceBackend(),
            openclaw_client=InMemoryOpenClawClient(),
        )

        open_reply = cluster.process("戴开,夹爪。")
        self.assertEqual(open_reply.agent_name, "executor_openclaw")
        self.assertEqual(open_reply.metadata["execution"]["operation"], "open_gripper")

        close_reply = cluster.process("关闭,移动。")
        self.assertTrue(close_reply.requires_confirmation)
        self.assertEqual(close_reply.metadata["pending_operation"], "close_gripper")

    def test_cluster_can_restore_openclaw_state_from_state_path(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "openclaw" / "state.json"

            first_cluster = AgentCluster(
                llm_client=lambda _: self.fail("机械臂执行不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                openclaw_state_path=str(state_path),
            )
            confirmation = first_cluster.process("关闭夹爪")
            self.assertTrue(confirmation.requires_confirmation)
            execution = first_cluster.process("确认执行")

            second_cluster = AgentCluster(
                llm_client=lambda _: self.fail("机械臂状态查询不应调用 LLM"),
                device_backend=FakeDeviceBackend(),
                openclaw_state_path=str(state_path),
            )
            status_reply = second_cluster.process("看一下机械臂当前状态")

            self.assertEqual(execution.agent_name, "executor_openclaw")
            self.assertTrue(state_path.exists())
            self.assertEqual(status_reply.agent_name, "executor_openclaw")
            self.assertIn("OpenClaw 当前状态", status_reply.text)
            self.assertIn("夹爪关闭", status_reply.text)

    def test_cluster_can_route_openclaw_status_to_remote_node(self):
        node_registry = NodeRegistry(
            [
                RemoteNode(
                    node_id="lab-arm",
                    client=ExecutorRemoteNodeClient([OpenClawExecutor(client=InMemoryOpenClawClient())]),
                    targets={"openclaw"},
                )
            ]
        )
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("机械臂状态查询不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            node_registry=node_registry,
        )

        reply = cluster.process("看一下机械臂当前状态")

        self.assertEqual(reply.agent_name, "executor_remote")
        self.assertFalse(reply.requires_confirmation)
        self.assertIn("OpenClaw 当前状态", reply.text)
        self.assertEqual(reply.metadata["execution"]["metadata"]["node_id"], "lab-arm")
        self.assertEqual(reply.metadata["execution"]["metadata"]["remote_target"], "openclaw")

    def test_cluster_routes_software_action_to_remote_device_alias(self):
        software_executor = FakeExecutor(name="remote_software", supported_targets={"local_pc"})
        node_registry = NodeRegistry(
            [
                RemoteNode(
                    node_id="office-pc",
                    client=ExecutorRemoteNodeClient([software_executor]),
                    targets={"local_pc"},
                    aliases={"公司电脑"},
                )
            ]
        )
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("远端软件动作不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            node_registry=node_registry,
        )

        reply = cluster.process("在公司电脑上打开飞书")

        self.assertEqual(reply.agent_name, "executor_remote")
        self.assertEqual(reply.metadata["execution"]["metadata"]["node_id"], "office-pc")
        self.assertEqual(reply.metadata["execution"]["metadata"]["remote_target"], "local_pc")
        self.assertEqual(software_executor.requests[0].operation, "launch_app")
        self.assertEqual(software_executor.requests[0].params, {"app_name": "Feishu"})

    def test_cluster_confirms_then_executes_feishu_message_send(self):
        feishu_client = FakeFeishuClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("飞书发送动作不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            feishu_client=feishu_client,
        )

        confirmation = cluster.process("给张三发飞书，说会议改到三点")
        reply = cluster.process("确认执行")

        self.assertTrue(confirmation.requires_confirmation)
        self.assertEqual(confirmation.metadata["pending_target"], "feishu")
        self.assertEqual(confirmation.metadata["pending_operation"], "send_message")
        self.assertEqual(reply.agent_name, "executor_feishu")
        self.assertIn("dry-run", reply.text)
        self.assertEqual(feishu_client.calls, [("张三", "会议改到三点")])

    def test_cluster_understands_feishu_first_colloquial_send_message(self):
        feishu_client = FakeFeishuClient()
        cluster = AgentCluster(
            llm_client=lambda _: self.fail("飞书口语发送动作不应调用 LLM"),
            device_backend=FakeDeviceBackend(),
            feishu_client=feishu_client,
        )

        cluster.process("飞书给李四说我晚点到")
        reply = cluster.process("确认执行")

        self.assertEqual(reply.agent_name, "executor_feishu")
        self.assertEqual(feishu_client.calls, [("李四", "我晚点到")])

    def test_cluster_understands_more_colloquial_feishu_send_messages(self):
        cases = [
            ("帮我跟张三说会议改到三点", "张三", "会议改到三点"),
            ("用飞书通知张三，说会议改到三点", "张三", "会议改到三点"),
            ("发消息给张三，内容是会议改到三点", "张三", "会议改到三点"),
            ("在飞书上提醒李四明天早上九点开会", "李四", "明天早上九点开会"),
        ]
        for phrase, recipient, text in cases:
            with self.subTest(phrase=phrase):
                feishu_client = FakeFeishuClient()
                cluster = AgentCluster(
                    llm_client=lambda _: self.fail("飞书口语发送动作不应调用 LLM"),
                    device_backend=FakeDeviceBackend(),
                    feishu_client=feishu_client,
                )

                confirmation = cluster.process(phrase)
                reply = cluster.process("确认执行")

                self.assertTrue(confirmation.requires_confirmation)
                self.assertEqual(reply.agent_name, "executor_feishu")
                self.assertEqual(feishu_client.calls, [(recipient, text)])

    def test_cluster_uses_kb_search_for_docs_style_query(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "开发路线里先做 tools 和 knowledge 骨架 <emotion:thinking>"

        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "开发路线", "开发路线建议先做 tools、knowledge 与 remote 骨架。")
        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            knowledge_retriever=SimpleKnowledgeRetriever(store),
        )

        reply = cluster.process("开发路线文档里怎么规划的？")

        self.assertEqual(reply.agent_name, "tool_kb_search")
        self.assertIn("知识检索结果", prompts[0])
        self.assertIn("tools、knowledge 与 remote 骨架", prompts[0])

    def test_cluster_auto_loads_project_docs_retriever_when_not_provided(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "项目当前有 tool、knowledge 和 executor 骨架 <emotion:thinking>"

        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "项目架构", "项目当前已经有 tool、knowledge 和 executor 骨架。")

        with patch(
            "backend.orchestrator.agent_cluster.build_project_docs_retriever",
            return_value=SimpleKnowledgeRetriever(store),
        ):
            cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend())

        reply = cluster.process("项目架构文档怎么说？")

        self.assertEqual(reply.agent_name, "tool_kb_search")
        self.assertIn("知识检索结果", prompts[0])
        self.assertIn("tool、knowledge 和 executor 骨架", prompts[0])

    def test_cluster_can_auto_load_embedding_docs_retriever(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "项目知识库后续可切 embedding 检索 <emotion:thinking>"

        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "知识路线", "项目知识库后续可切 embedding 检索。")

        with patch(
            "backend.orchestrator.agent_cluster.build_project_docs_embedding_retriever",
            return_value=SimpleKnowledgeRetriever(store),
        ):
            cluster = AgentCluster(
                llm_client=fake_llm,
                device_backend=FakeDeviceBackend(),
                knowledge_backend="embedding",
            )

        reply = cluster.process("知识路线文档怎么写的？")

        self.assertEqual(reply.agent_name, "tool_kb_search")
        self.assertIn("知识检索结果", prompts[0])
        self.assertIn("embedding 检索", prompts[0])


if __name__ == "__main__":
    unittest.main()
