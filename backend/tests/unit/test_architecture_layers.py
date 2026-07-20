import re
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.action.atomic_operations import list_default_atomic_operations
from backend.agents.programming_agent import ProgrammingAgent
from backend.app.agent_management import save_agent_management_state
from backend.app.local_model_policy import (
    build_local_model_policy_snapshot,
    default_scheduler_benchmark_cases,
    evaluate_scheduler_benchmark_case,
    evaluate_scheduler_benchmark_suite,
    load_scheduler_benchmark_history,
    record_scheduler_benchmark_result,
)
from backend.app.model_catalog import bundled_model_catalog
from backend.app.replaceable_brain import (
    build_brain_replacement_snapshot,
    evaluate_brain_replacement,
    normalize_brain_adapter,
)
from backend.code_jury import (
    build_code_review_package,
    build_jury_report,
    parse_model_jury_review,
    synthesize_patch_plan,
)
from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult
from backend.knowledge import InMemoryKnowledgeStore, SimpleKnowledgeRetriever, ingest_text_document
from backend.orchestrator.agent_cluster import AgentCluster
from backend.orchestrator.agent_container import build_agent_capability_container, evaluate_agent_container_scope
from backend.orchestrator.android_worker_registry import android_worker_capability_records, android_worker_descriptor
from backend.orchestrator.brain_router import BrainRouter
from backend.orchestrator.capability_graph import build_capability_registry
from backend.orchestrator.hybrid_planner import HybridPlannerPipeline
from backend.orchestrator.planner import Planner
from backend.orchestrator.session_manager import SessionManager
from backend.orchestrator.worker_pool import (
    WorkerDescriptor,
    WorkerPool,
    WorkerRequirement,
    planned_worker_seed_descriptors,
)
from backend.security.safety_control import set_safety_stop
from backend.skills import SkillSpec, SkillStepSpec
from backend.tools import build_default_tool_registry


class FakeDeviceBackend:
    name = "fake"

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
        return "检测到文本"

    def understand_screen(self, query: str, region=None):
        return f"视觉结果：{query}"


class FakeStatusExecutor(BaseExecutor):
    name = "fake_status"

    def __init__(self):
        self.requests = []

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target == "local_pc" and request.operation == "browser_open_url"

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(True, "opened ok", data={"url": request.params.get("url")}, metadata={"executor": self.name})


class ArchitectureLayerTests(unittest.TestCase):
    def test_frozen_backend_ownership_has_no_legacy_top_level_packages(self):
        backend_root = Path(__file__).resolve().parents[2]
        forbidden_packages = {"growth", "scheduler", "events", "training", "eval"}

        present = sorted(name for name in forbidden_packages if (backend_root / name).is_dir())
        self.assertEqual(present, [])

        forbidden_import = re.compile(r"backend\.(?:growth|scheduler|events|training|eval)(?:\.|\b)")
        violations: list[str] = []
        for path in backend_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if forbidden_import.search(text):
                violations.append(str(path.relative_to(backend_root)))
        self.assertEqual(sorted(violations), [])

    def test_default_atomic_operations_include_inventory_metadata(self):
        operations = {operation.name: operation for operation in list_default_atomic_operations()}

        self.assertIn("software.list_installed", operations)
        self.assertIn("hardware.list_devices", operations)
        self.assertTrue(operations["software.list_installed"].read_only)
        self.assertEqual(operations["hardware.list_devices"].confirmation_policy, "never")
        self.assertTrue(operations["software.list_installed"].eval_cases)

    def test_session_manager_builds_summary_and_recent_history(self):
        session = SessionManager(memory_limit=5, context_window=2)
        session.record_user_turn("第一问")
        session.record_agent_turn(type("Reply", (), {"text": "第一答", "agent_name": "general"})())
        session.record_user_turn("第二问")
        session.record_agent_turn(type("Reply", (), {"text": "第二答", "agent_name": "general"})())
        session.record_user_turn("第三问")
        session.record_agent_turn(type("Reply", (), {"text": "第三答", "agent_name": "general"})())

        context = session.build_context("第四问", visual_context="桌面上有代码编辑器", device_name="local_pc")

        self.assertIn("第一问", context.session_summary)
        self.assertEqual(len(context.recent_history), 2)
        self.assertIn("当前输入：[视觉提示：桌面上有代码编辑器] 第四问", context.prompt_context)

    def test_planner_prefers_builtin_before_agents(self):
        planner = Planner()
        context = SessionManager().build_context("2+3等于多少", visual_context="", device_name="local_pc")
        programming_agent = ProgrammingAgent(lambda _: "ok <emotion:happy>")

        plan = planner.plan(context, [programming_agent])

        self.assertEqual(plan.route, "builtin")
        self.assertEqual(plan.builtin_name, "calc")

    def test_planner_can_generate_executor_request_for_pointer_move(self):
        planner = Planner()
        context = SessionManager().build_context("把鼠标移动到 100, 200", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.target, "local_pc")
        self.assertEqual(plan.execution_request.operation, "move_pointer")
        self.assertEqual(plan.execution_request.params, {"x": 100, "y": 200})

    def test_planner_can_generate_executor_request_for_window_resize(self):
        planner = Planner()
        context = SessionManager().build_context("调整 VSCode 窗口大小到 1200x800", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.operation, "window_resize")
        self.assertEqual(plan.execution_request.params, {"title": "VSCode", "width": 1200, "height": 800})

    def test_planner_can_generate_executor_request_for_window_move(self):
        planner = Planner()
        context = SessionManager().build_context("移动浏览器窗口到 100, 200", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.operation, "window_move")
        self.assertEqual(plan.execution_request.params, {"title": "浏览器", "x": 100, "y": 200})

    def test_planner_can_generate_executor_request_for_notification_send(self):
        planner = Planner()
        context = SessionManager().build_context('发送通知："SpiritKin" "执行完成"', visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.operation, "notification_send")
        self.assertEqual(plan.execution_request.params, {"title": "SpiritKin", "text": "执行完成"})

    def test_planner_prefers_feishu_message_over_desktop_notification(self):
        planner = Planner()
        context = SessionManager().build_context("用飞书通知张三，说会议改到三点", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.target, "feishu")
        self.assertEqual(plan.execution_request.operation, "send_message")
        self.assertEqual(plan.execution_request.params, {"recipient": "张三", "text": "会议改到三点"})

    def test_planner_can_generate_executor_request_for_browser_tab_list(self):
        planner = Planner()
        context = SessionManager().build_context("列出当前浏览器标签页", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.operation, "browser_tab_list")

    def test_planner_can_generate_executor_request_for_file_write(self):
        planner = Planner()
        context = SessionManager().build_context('写入文件 "demo.txt"，内容 "hello spirit"', visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.operation, "file_write")
        self.assertEqual(plan.execution_request.params, {"path": "demo.txt", "text": "hello spirit"})

    def test_planner_can_generate_executor_request_for_openclaw_home(self):
        planner = Planner()
        context = SessionManager().build_context("让机械臂回零", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertIsNotNone(plan.execution_request)
        self.assertEqual(plan.execution_request.target, "openclaw")
        self.assertEqual(plan.execution_request.operation, "home")

    def test_planner_can_generate_executor_request_for_openclaw_status(self):
        planner = Planner()
        context = SessionManager().build_context("看一下机械臂当前状态", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "executor")
        self.assertEqual(plan.execution_request.target, "openclaw")
        self.assertEqual(plan.execution_request.operation, "status")

    def test_planner_can_generate_kb_search_tool_call(self):
        planner = Planner()
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "项目架构", "项目架构包含 tools、knowledge 和 executors 分层。")
        registry = build_default_tool_registry(knowledge_retriever=SimpleKnowledgeRetriever(store))
        context = SessionManager().build_context("项目架构文档怎么说？", visual_context="", device_name="local_pc")

        plan = planner.plan(context, [], registry.list_specs())

        self.assertEqual(plan.route, "tool")
        self.assertIsNotNone(plan.tool_call)
        self.assertEqual(plan.tool_call.name, "kb.search")
        self.assertEqual(plan.tool_call.arguments["query"], "项目架构文档怎么说？")

    def test_planner_can_route_integration_requests_to_development_plan(self):
        planner = Planner()
        context = SessionManager().build_context(
            "请给我一个飞书接入开发计划，要求优先 API 并人工审核",
            visual_context="",
            device_name="local_pc",
        )

        plan = planner.plan(context, [])

        self.assertEqual(plan.route, "development_plan")
        self.assertEqual(plan.development_request, "请给我一个飞书接入开发计划，要求优先 API 并人工审核")

    def test_capability_graph_links_tools_skills_agents_and_workers(self):
        registry = build_default_tool_registry()
        skill = SkillSpec(
            name="publish.product",
            description="发布商品",
            trigger_intents=("publish_product",),
            steps=(SkillStepSpec(tool_name="browser.open_url", arguments={"url": "{{url}}"}),),
            tool_allowlist=("browser.open_url",),
            metadata={"capability_id": "publish_product", "domain": "ecommerce", "status": "active"},
        )
        agent = type("Agent", (), {"name": "ecommerce", "domain": "ecommerce", "capabilities": ("publish_product",)})()

        graph = build_capability_registry(
            tools=registry.list_specs(),
            skills=[skill],
            agents=[agent],
            executors=[FakeStatusExecutor()],
        )
        snapshot = graph.snapshot()
        capabilities = {item["capability_id"]: item for item in snapshot["capabilities"]}

        self.assertIn("publish_product", capabilities)
        self.assertIn("local_pc_browser_open_url", capabilities)
        self.assertIn("browser_browser_open_url", capabilities)
        self.assertEqual(graph.resolve_execution_request(ExecutionRequest("local_pc", "browser_open_url")).capability_id, "local_pc_browser_open_url")
        self.assertEqual(graph.resolve_execution_request(ExecutionRequest("browser", "browser_open_url")).capability_id, "browser_browser_open_url")
        self.assertEqual(
            graph.resolve_execution_request(ExecutionRequest("remote:office-pc", "browser_open_url", {"remote_target": "browser"})).capability_id,
            "browser_browser_open_url",
        )
        publish = capabilities["publish_product"]
        self.assertIn("ecommerce", publish["owner_agents"])
        self.assertIn("publish.product", publish["skill_refs"])
        self.assertIn("browser.open_url", publish["tool_refs"])
        self.assertTrue(any(edge["type"] == "uses_skill" for edge in snapshot["edges"]))

    def test_worker_pool_executes_executor_with_audit_and_capability(self):
        tool_registry = build_default_tool_registry()
        capability_registry = build_capability_registry(tools=tool_registry.list_specs())
        executor = FakeStatusExecutor()
        pool = WorkerPool([executor], capability_registry=capability_registry)

        execution = pool.execute(ExecutionRequest("local_pc", "browser_open_url", {"url": "https://example.com"}), actor="unit-test")
        snapshot = pool.snapshot()

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.worker.worker_id, "executor:fake_status")
        self.assertEqual(execution.audit_event.status, "succeeded")
        self.assertEqual(execution.audit_event.capability_id, "local_pc_browser_open_url")
        self.assertEqual(execution.result.metadata["worker_id"], "executor:fake_status")
        self.assertEqual(snapshot["audit"][0]["capability_id"], "local_pc_browser_open_url")

    def test_worker_pool_exposes_android_control_worker_as_external_worker(self):
        worker = {
            "worker_id": "android_control_worker",
            "label": "Android Control Worker",
            "status": "ready",
            "capabilities": ["pdd.launch", "android.ui_snapshot"],
            "queue": {"pending": 2},
            "workspace_ids": ["local-ecommerce"],
        }
        descriptor = android_worker_descriptor(worker, companion={"device_count": 1, "pending_command_count": 2})
        pool = WorkerPool([FakeStatusExecutor()], external_workers=[descriptor])
        snapshot = pool.snapshot()
        workers = {item["worker_id"]: item for item in snapshot["workers"]}

        self.assertEqual(snapshot["external_total"], 1)
        self.assertEqual(snapshot["total"], 2)
        self.assertIn("android_control_worker", workers)
        self.assertEqual(workers["android_control_worker"]["kind"], "android_control_worker")
        self.assertEqual(workers["android_control_worker"]["worker_type"], "device_worker")
        self.assertEqual(workers["android_control_worker"]["worker_subtype"], "android_device_worker")
        self.assertIn("Android Bridge", workers["android_control_worker"]["legacy_names"])
        self.assertIn("android", workers["android_control_worker"]["capability_namespaces"])
        self.assertEqual(workers["android_control_worker"]["queue_depth"], 2)
        self.assertIn("pdd.launch", workers["android_control_worker"]["operations"])
        self.assertEqual(snapshot["taxonomy"]["type_counts"]["device_worker"], 1)
        self.assertIn("android_control_worker", snapshot["taxonomy"]["workers_by_type"]["device_worker"])

    def test_android_worker_capabilities_register_with_capability_graph(self):
        worker = {"capabilities": ["pdd.launch", "android.ui_snapshot"], "status": "ready"}
        graph = build_capability_registry(tools=build_default_tool_registry().list_specs())
        for record in android_worker_capability_records(worker, companion={}):
            graph.register(record)
        snapshot = graph.snapshot()
        capabilities = {item["capability_id"]: item for item in snapshot["capabilities"]}

        self.assertIn("android_device_pdd_launch", capabilities)
        self.assertEqual(capabilities["android_device_pdd_launch"]["worker_requirements"], ["android_control_worker"])
        self.assertEqual(capabilities["android_device_pdd_launch"]["bindings"][0]["binding_type"], "android_worker")

    def test_worker_pool_taxonomy_maps_old_runtime_names_to_worker_responsibilities(self):
        capability_registry = build_capability_registry(tools=build_default_tool_registry().list_specs())
        pool = WorkerPool(
            [FakeStatusExecutor()],
            capability_registry=capability_registry,
            external_workers=[
                WorkerDescriptor(
                    worker_id="android_phone_a",
                    label="Android Phone A",
                    kind="android_control_worker",
                    worker_type="device_worker",
                    worker_subtype="android_device_worker",
                    capabilities=("adb.tap", "adb.swipe", "pdd.launch"),
                    capability_namespaces=("adb", "android", "pdd"),
                    targets=("android_device",),
                    operations=("adb.tap", "adb.swipe", "pdd.launch"),
                    legacy_names=("Android Bridge", "ADB"),
                ),
                WorkerDescriptor(
                    worker_id="remote-office-pc",
                    label="Remote Office PC",
                    kind="remote_runtime",
                    worker_type="generic_remote_worker",
                    worker_subtype="remote_runtime_worker",
                    capabilities=("browser", "python", "ffmpeg", "git"),
                    capability_namespaces=("browser", "python", "ffmpeg", "git"),
                    targets=("desktop", "browser"),
                    operations=("browser.open_url", "python.run"),
                    legacy_names=("Remote Worker",),
                ),
                WorkerDescriptor(
                    worker_id="rag-worker",
                    label="RAG Worker",
                    kind="service",
                    worker_type="service_worker",
                    worker_subtype="rag_worker",
                    capabilities=("rag.search", "embedding.create"),
                    capability_namespaces=("rag", "embedding"),
                    targets=("knowledge",),
                    operations=("rag.search",),
                ),
            ],
        )

        snapshot = pool.snapshot()
        workers = {item["worker_id"]: item for item in snapshot["workers"]}
        taxonomy = snapshot["taxonomy"]
        legacy = {item["old_name"]: item for item in taxonomy["legacy_positioning"]}

        self.assertEqual(workers["executor:fake_status"]["worker_type"], "device_worker")
        self.assertEqual(workers["executor:fake_status"]["worker_subtype"], "desktop_device_worker")
        self.assertEqual(taxonomy["type_counts"]["device_worker"], 2)
        self.assertEqual(taxonomy["type_counts"]["generic_remote_worker"], 1)
        self.assertEqual(taxonomy["type_counts"]["service_worker"], 1)
        self.assertIn("android_phone_a", taxonomy["capability_namespaces"]["adb"])
        self.assertIn("remote-office-pc", taxonomy["capability_namespaces"]["browser"])
        self.assertIn("rag-worker", taxonomy["capability_namespaces"]["embedding"])
        self.assertEqual(legacy["Android Bridge"]["new_positioning"], "Android Device Worker")
        self.assertEqual(legacy["Remote Worker"]["new_positioning"], "Generic Remote Worker")
        self.assertEqual(legacy["OpenClaw"]["worker_type"], "device_worker")

    def test_worker_pool_scheduler_selects_by_capability_needs_health_and_queue(self):
        pool = WorkerPool(
            external_workers=[
                WorkerDescriptor(
                    worker_id="local-browser",
                    label="Local Browser",
                    kind="browser",
                    worker_type="browser_worker",
                    worker_subtype="local_browser_worker",
                    capabilities=("browser.open_url",),
                    capability_namespaces=("browser",),
                    targets=("browser",),
                    operations=("browser.open_url",),
                    health_status="ready",
                    queue_depth=3,
                ),
                WorkerDescriptor(
                    worker_id="remote-browser",
                    label="Remote Browser",
                    kind="remote_runtime",
                    worker_type="generic_remote_worker",
                    worker_subtype="remote_runtime_worker",
                    capabilities=("browser.open_url", "python.run"),
                    capability_namespaces=("browser", "python"),
                    targets=("browser", "desktop"),
                    operations=("browser.open_url",),
                    permission_scope="remote",
                    health_status="ready",
                    queue_depth=0,
                    legacy_names=("Remote Worker",),
                ),
                WorkerDescriptor(
                    worker_id="offline-browser",
                    label="Offline Browser",
                    kind="remote_runtime",
                    worker_type="generic_remote_worker",
                    worker_subtype="remote_runtime_worker",
                    capabilities=("browser.open_url",),
                    capability_namespaces=("browser",),
                    targets=("browser",),
                    operations=("browser.open_url",),
                    permission_scope="remote",
                    health_status="offline",
                ),
            ],
        )

        local_decision = pool.schedule({"needs": ["browser"], "worker_type": "browser_worker"})
        remote_decision = pool.schedule(WorkerRequirement(needs=("browser",), prefer_remote=True))
        missing_decision = pool.schedule({"needs": ["android.adb"]})

        self.assertEqual(local_decision.status, "selected")
        self.assertEqual(local_decision.selected.worker_id, "local-browser")
        self.assertEqual(remote_decision.selected.worker_id, "remote-browser")
        self.assertEqual(missing_decision.status, "missing")
        self.assertIn("missing=android.adb", missing_decision.reason)

    def test_worker_pool_planned_worker_seeds_are_taxonomy_only(self):
        pool = WorkerPool()
        snapshot = pool.snapshot()
        planned = {item["worker_subtype"]: item for item in snapshot["planned_workers"]}
        taxonomy = snapshot["taxonomy"]

        self.assertIn("python_worker", planned)
        self.assertIn("ffmpeg_worker", planned)
        self.assertIn("git_worker", planned)
        self.assertIn("service_rag_worker", planned)
        self.assertEqual(planned["python_worker"]["metadata"]["maturity"], "planned")
        self.assertFalse(planned["git_worker"]["metadata"]["schedulable"])
        self.assertIn("planned:python_worker", taxonomy["planned_capability_namespaces"]["python"])
        self.assertIn("planned:service_rag_worker", taxonomy["planned_workers_by_type"]["service_worker"])

        decision = pool.schedule(WorkerRequirement(needs=("python",)))
        self.assertEqual(decision.status, "missing")

    def test_planned_worker_seeds_feed_capability_graph_without_scheduling(self):
        graph = build_capability_registry(workers=planned_worker_seed_descriptors())
        snapshot = graph.snapshot()
        capabilities = {item["capability_id"]: item for item in snapshot["capabilities"]}

        self.assertIn("python_execute", capabilities)
        self.assertIn("ffmpeg_transcode", capabilities)
        self.assertIn("git_status", capabilities)
        self.assertTrue(capabilities["python_execute"]["metadata"]["planned"])
        self.assertFalse(capabilities["git_status"]["metadata"]["schedulable"])
        self.assertEqual(capabilities["ffmpeg_transcode"]["bindings"][0]["binding_type"], "worker_descriptor")
        self.assertEqual(capabilities["ffmpeg_transcode"]["bindings"][0]["metadata"]["maturity"], "planned")

        pool = WorkerPool()
        self.assertEqual(pool.schedule(WorkerRequirement(needs=("git",))).status, "missing")

    def test_capability_graph_recommendation_ranks_schedulable_capabilities(self):
        skill = SkillSpec(
            name="commerce.publish",
            description="发布商品",
            required_capabilities=("publish_product",),
            required_worker_needs=("browser",),
            metadata={"capability_id": "publish_product", "domain": "ecommerce", "status": "active"},
        )
        graph = build_capability_registry(skills=[skill], workers=planned_worker_seed_descriptors())

        recommendation = graph.recommend(
            "发布商品 publish_product",
            domain="ecommerce",
            required_capabilities=("publish_product",),
            required_workers=("browser",),
        ).snapshot()

        self.assertEqual(recommendation["top_capability_id"], "publish_product")
        top = recommendation["candidates"][0]
        self.assertTrue(top["schedulable"])
        self.assertIn("domain_match:ecommerce", top["reasons"])
        self.assertIn("required_capability:publish_product", top["reasons"])
        self.assertIn("required_worker:browser", top["reasons"])
        self.assertEqual(top["worker_evidence"][0]["requirement"], "browser")
        self.assertEqual(top["worker_evidence"][0]["status"], "missing")
        self.assertIn("worker_missing:browser", top["gaps"])

    def test_capability_graph_recommendation_reports_ready_worker_evidence(self):
        skill = SkillSpec(
            name="commerce.publish",
            description="发布商品",
            required_capabilities=("publish_product",),
            required_worker_needs=("browser",),
            metadata={"capability_id": "publish_product", "domain": "ecommerce", "status": "active"},
        )
        ready_browser = WorkerDescriptor(
            worker_id="local-browser",
            label="Local Browser Worker",
            worker_type="browser_worker",
            worker_subtype="browser",
            capabilities=("browser.open",),
            capability_namespaces=("browser", "playwright"),
            targets=("browser",),
            operations=("browser.open_url",),
            health_status="ready",
            metadata={"maturity": "ready", "schedulable": True},
        )
        graph = build_capability_registry(skills=[skill], workers=[ready_browser])

        recommendation = graph.recommend(
            "发布商品 publish_product",
            domain="ecommerce",
            required_capabilities=("publish_product",),
            required_workers=("browser",),
        ).snapshot()

        top = recommendation["candidates"][0]
        self.assertEqual(recommendation["top_capability_id"], "publish_product")
        evidence = top["worker_evidence"][0]
        self.assertEqual(evidence["status"], "ready")
        self.assertTrue(evidence["schedulable"])
        self.assertIn("local-browser", evidence["matched_worker_ids"])
        self.assertIn("worker_ready:browser", top["reasons"])

    def test_capability_graph_recommendation_keeps_planned_workers_non_default(self):
        graph = build_capability_registry(workers=planned_worker_seed_descriptors())

        hidden = graph.recommend("git status", required_capabilities=("git_status",)).snapshot()
        included = graph.recommend("git status", required_capabilities=("git_status",), include_planned=True).snapshot()

        self.assertEqual(hidden["candidate_count"], 0)
        self.assertEqual(included["top_capability_id"], "git_status")
        top = included["candidates"][0]
        self.assertFalse(top["schedulable"])
        self.assertIn("not_schedulable", top["gaps"])
        self.assertTrue(top["capability"]["metadata"]["planned"])
        self.assertEqual(top["worker_evidence"][0]["status"], "planned")
        self.assertTrue(top["worker_evidence"][0]["planned"])
        self.assertIn("planned:git_worker", top["worker_evidence"][0]["matched_worker_ids"])

    def test_worker_pool_allows_read_only_capability_during_safety_stop(self):
        class ReadOnlyExecutor(BaseExecutor):
            name = "read_only"

            def supports(self, request: ExecutionRequest) -> bool:
                return request.target == "local_pc" and request.operation == "clipboard_read"

            def execute(self, request: ExecutionRequest) -> ExecutionResult:
                return ExecutionResult(True, "clipboard ok", data={"text": "hello"})

        tool_registry = build_default_tool_registry()
        capability_registry = build_capability_registry(tools=tool_registry.list_specs())
        with TemporaryDirectory() as tmp:
            safety_path = f"{tmp}/kill_switch.json"
            set_safety_stop(mode="soft_stop", reason="unit-test", actor="unit-test", path=safety_path)
            with patch.dict("os.environ", {"SPIRITKIN_SAFETY_STATE_PATH": safety_path}):
                execution = WorkerPool([ReadOnlyExecutor()], capability_registry=capability_registry).execute(
                    ExecutionRequest("local_pc", "clipboard_read"),
                    actor="unit-test",
                )

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.audit_event.status, "succeeded")

    def test_brain_router_builds_auditable_local_first_decision(self):
        router = BrainRouter(
            agent_profiles={
                "programming": {
                    "provider": "ollama",
                    "model": "qwen-coder",
                    "model_id": "local-coder",
                    "role": "specialist",
                    "domain": "programming",
                    "capabilities": ["code_edit", "tests"],
                }
            },
            model_catalog={
                "models": [
                    {
                        "model_id": "qwen-coder",
                        "provider": "ollama",
                        "role": "programming_agent",
                        "domain": "code_edit_debug_review",
                        "size_class": "30B-A3B",
                        "priority": 90,
                    }
                ]
            },
        )

        decision = router.route(
            agent_id="programming",
            task_text="请重构这段代码并运行 tests",
            route="agent",
            domain="programming",
        )

        self.assertEqual(decision.agent_id, "programming")
        self.assertEqual(decision.provider, "ollama")
        self.assertEqual(decision.model, "qwen-coder")
        self.assertIn(decision.route, {"local_default", "local_complex_with_review_candidate"})
        self.assertIn("code_edit", decision.required_capabilities)
        self.assertEqual(router.snapshot()["audit"][0]["agent_id"], "programming")

    def test_agent_capability_container_collects_policy_assets(self):
        graph = build_capability_registry(tools=build_default_tool_registry().list_specs())
        record = graph.resolve_execution_request(ExecutionRequest("local_pc", "browser_open_url"))
        router = BrainRouter(agent_profiles={"ecommerce": {"provider": "ollama", "model": "qwen", "capabilities": ["publish_product"]}})
        decision = router.route(agent_id="ecommerce", task_text="发布商品", route="agent", domain="ecommerce")

        container = build_agent_capability_container(
            agent_id="ecommerce",
            profile={"label": "电商 Agent", "domain": "ecommerce", "role": "specialist", "capabilities": ["publish_product"]},
            capability_records=[record],
            skills=[
                SkillSpec(
                    name="publish.product",
                    description="发布商品",
                    steps=(SkillStepSpec(tool_name="browser.open_url"),),
                    metadata={"capability_id": "publish_product"},
                )
            ],
            knowledge_base={"knowledge_base_id": "kb_ecommerce"},
            brain_decision=decision,
        )
        snapshot = container.snapshot()

        self.assertEqual(snapshot["agent_id"], "ecommerce")
        self.assertIn("publish_product", snapshot["capabilities"])
        self.assertIn("browser.open_url", snapshot["allowed_tools"])
        self.assertIn("publish.product", snapshot["allowed_skills"])
        self.assertEqual(snapshot["knowledge_base"]["knowledge_base_id"], "kb_ecommerce")
        self.assertEqual(snapshot["brain_decision"]["agent_id"], "ecommerce")
        self.assertTrue(snapshot["metadata"]["scope_enforcement"]["tools_restricted"])
        allowed = evaluate_agent_container_scope(container, tool_name="browser.open_url")
        denied = evaluate_agent_container_scope(container, tool_name="file.delete")
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "not_in_agent_container_allowlist")

    def test_agent_management_persists_brain_profile(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"SPIRITKIN_AGENT_MANAGEMENT_PATH": str(Path(tmp) / "agents.json")}, clear=False):
            state = save_agent_management_state(
                {
                    "agents": [
                        {
                            "agent_id": "main_text",
                            "label": "Main",
                            "domain": "general",
                            "provider": "local",
                            "model": "qwen",
                            "brain_profile": "local_scheduler_master",
                        }
                    ]
                }
            )
            reloaded = save_agent_management_state({})

        self.assertEqual(state.agents[0].brain_profile, "local_scheduler_master")
        self.assertEqual(reloaded.snapshot()["agents"][0]["brain_profile"], "local_scheduler_master")

    def test_hybrid_planner_wraps_existing_plan_with_analysis_and_workflow(self):
        tool_registry = build_default_tool_registry()
        capability_registry = build_capability_registry(tools=tool_registry.list_specs())
        pipeline = HybridPlannerPipeline(capability_registry=capability_registry)
        context = SessionManager().build_context("打开 https://example.com", visual_context="", device_name="local_pc")

        result = pipeline.plan(context, [], tool_registry.list_specs())
        snapshot = result.snapshot()

        self.assertEqual(result.execution_plan.route, "executor")
        self.assertEqual(result.execution_plan.execution_request.operation, "browser_open_url")
        self.assertIn("local_pc_browser_open_url", snapshot["analysis"]["required_capabilities"])
        self.assertEqual(snapshot["task_plan"]["route"], "executor")
        self.assertEqual(snapshot["workflow_plan"]["mode"], "single_step")
        recommendation = snapshot["capability_recommendation"]
        self.assertEqual(recommendation["top_capability_id"], "local_pc_browser_open_url")
        self.assertGreaterEqual(recommendation["candidate_count"], 1)
        self.assertIn("required_capability:local_pc_browser_open_url", recommendation["candidates"][0]["reasons"])
        self.assertTrue(recommendation["candidates"][0]["schedulable"])

    def test_hybrid_planner_cloud_gate_requires_explicit_approval(self):
        pipeline = HybridPlannerPipeline()
        long_text = "请规划一个高风险发布部署重构闭环 " * 120
        context = SessionManager().build_context(long_text, visual_context="", device_name="local_pc")
        approved_context = replace(context, metadata={**dict(context.metadata or {}), "cloud_planning_approved": True})
        plan = Planner().plan(context, [], [])

        candidate = pipeline.describe_plan(context, plan).snapshot()
        approved = pipeline.describe_plan(approved_context, plan).snapshot()

        self.assertEqual(candidate["task_plan"]["planner_profile"], "cloud_planner_candidate")
        self.assertEqual(candidate["task_plan"]["budget"]["cloud_planner_gate"]["status"], "requires_approval")
        self.assertEqual(approved["task_plan"]["planner_profile"], "cloud_planner_approved")
        self.assertTrue(approved["task_plan"]["budget"]["cloud_planning_approved"])

    def test_hybrid_planner_creates_growth_gap_candidate_for_missing_execution_capability(self):
        pipeline = HybridPlannerPipeline(capability_registry=build_capability_registry())
        context = SessionManager().build_context("打开一个不存在的系统能力", visual_context="", device_name="local_pc")
        # Use an execution plan that the registry cannot resolve, as a real
        # worker/executor miss would do at runtime.
        from backend.executors.base import ExecutionRequest
        from backend.orchestrator.planner import ExecutionPlan

        missing = ExecutionPlan(
            route="executor",
            reason="missing capability",
            domain="execution",
            execution_request=ExecutionRequest("local_pc", "unsupported_future_operation", {}),
        )
        with TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"SPIRITKIN_GROWTH_EVENT_LOG": str(Path(tmp) / "events.jsonl"), "SPIRITKIN_GROWTH_REGISTRY_LOG": str(Path(tmp) / "registry.jsonl")},
            clear=False,
        ):
            result = pipeline.describe_plan(context, missing).snapshot()
            again = pipeline.describe_plan(context, missing).snapshot()

        self.assertEqual(result["growth_gap"]["status"], "candidate_created")
        self.assertIn("local_pc_unsupported_future_operation", result["growth_gap"]["missing_capabilities"])
        self.assertEqual(len(result["growth_gap"]["candidates"]), 1)
        self.assertEqual(len(again["growth_gap"]["candidates"]), 1)

    def test_code_jury_parses_structured_review_and_synthesizes_patch_plan(self):
        package = build_code_review_package(
            {
                "review_type": "code",
                "requirement": "修复路由元数据",
                "candidate_diff": "--- a/backend/app/runtime.py\n+++ b/backend/app/runtime.py\n",
                "files_changed": ["backend/app/runtime.py"],
                "unit_test_results": [{"command": "python -m unittest backend.tests.unit.test_runtime", "status": "passed"}],
                "capability_ids": ["run_tests"],
            }
        )
        raw_review = {
            "provider": "unit",
            "model": "reviewer",
            "response_text": """
            {
              "decision": "changes_requested",
              "scores": {
                "architecture": 0.9,
                "maintainability": 84,
                "performance": 92,
                "security": 88,
                "testability": 81
              },
              "findings": [
                {
                  "severity": "high",
                  "category": "testability",
                  "title": "Missing focused regression",
                  "detail": "The patch changes runtime metadata but lacks a focused assertion.",
                  "file_path": "backend/app/runtime.py",
                  "line": 42,
                  "suggested_fix": "Add a runtime metadata assertion."
                }
              ],
              "suggestions": ["Keep reviewer output advisory only."],
              "confidence": 0.8
            }
            """,
        }

        model_review = parse_model_jury_review(raw_review)
        report = build_jury_report(package, [raw_review], pass_threshold=80)
        plan = synthesize_patch_plan(report)

        self.assertTrue(model_review.structured)
        self.assertEqual(model_review.scores["architecture"], 90)
        self.assertEqual(report.decision, "changes_requested")
        self.assertFalse(report.promotion_gate["auto_apply_allowed"])
        self.assertEqual(plan.status, "proposal_ready")
        self.assertIn("backend/app/runtime.py", plan.patch_scope)
        self.assertIn("python -m unittest backend.tests.unit.test_runtime", plan.follow_up_tests)

    def test_code_jury_treats_unstructured_model_text_as_insufficient_evidence(self):
        package = build_code_review_package({"requirement": "检查 PR", "files_changed": ["backend/app/foo.py"]})

        report = build_jury_report(package, [{"provider": "unit", "model": "reviewer", "response_text": "Looks fine to me."}])
        plan = synthesize_patch_plan(report)

        self.assertEqual(report.decision, "insufficient_evidence")
        self.assertEqual(report.model_reviews[0].status, "unstructured_evidence")
        self.assertEqual(plan.status, "blocked")

    def test_local_model_policy_selects_16gb_quantization_and_sequential_roles(self):
        snapshot = build_local_model_policy_snapshot(
            model_catalog=bundled_model_catalog(),
            environ={"SPIRITKIN_VRAM_GB": "16", "SPIRITKIN_RAM_GB": "64", "SPIRITKIN_GPU_COUNT": "1"},
        )

        assignments = {item["role_id"]: item for item in snapshot["role_assignments"]}
        self.assertEqual(snapshot["hardware"]["hardware_class"], "single_gpu_16gb")
        self.assertTrue(snapshot["policy"]["single_active_large_model"])
        self.assertEqual(assignments["local_scheduler_master"]["quantization_profile"], "Q4_K_M")
        self.assertFalse(assignments["local_scheduler_master"]["concurrent_with_large_model"])
        self.assertEqual(assignments["local_27b_specialist"]["model_id"], "Qwen/Qwen3.6-27B")
        self.assertEqual(snapshot["scheduler_benchmark"]["case_count"], 4)

    def test_scheduler_benchmark_scores_json_tool_workflow_and_context(self):
        cases = {case.case_id: case for case in default_scheduler_benchmark_cases()}
        good = evaluate_scheduler_benchmark_case(
            cases["tool_call_accuracy_browser"],
            {"route": "executor", "tool_calls": [{"name": "browser.open_url"}]},
        )
        bad = evaluate_scheduler_benchmark_case(
            cases["tool_call_accuracy_browser"],
            {"route": "executor", "tool_calls": [{"name": "file.write"}]},
        )
        suite = evaluate_scheduler_benchmark_suite(
            {
                "json_validity_route_plan": {"route": "tool", "tool_calls": [], "workflow_steps": [], "confidence": 0.9},
                "tool_call_accuracy_browser": {"route": "executor", "tool_calls": [{"name": "browser.open_url"}]},
                "workflow_step_completeness_publish": {"route": "workflow", "workflow_steps": ["intake", "asset_check", "review_gate", "upload_product"]},
                "context_drift_followup": {"route": "agent", "context_retained_ids": ["order-42", "ecom-demo"], "irrelevant_context_ids": []},
            }
        )

        self.assertTrue(good.passed)
        self.assertFalse(bad.passed)
        self.assertIn("missing tool calls", bad.findings[0])
        self.assertTrue(suite["passed"])
        self.assertEqual(suite["category_scores"]["tool_call_accuracy"], 100.0)

    def test_scheduler_benchmark_history_is_recorded_and_loaded(self):
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "benchmarks.jsonl"
            result = evaluate_scheduler_benchmark_suite(
                {
                    "json_validity_route_plan": {"route": "tool", "tool_calls": [], "workflow_steps": [], "confidence": 0.9},
                    "tool_call_accuracy_browser": {"route": "executor", "tool_calls": [{"name": "browser.open_url"}]},
                    "workflow_step_completeness_publish": {"route": "workflow", "workflow_steps": ["intake", "asset_check", "review_gate", "upload_product"]},
                    "context_drift_followup": {"route": "agent", "context_retained_ids": ["order-42", "ecom-demo"], "irrelevant_context_ids": []},
                }
            )
            record = record_scheduler_benchmark_result(result, outputs_by_case_id={"tool_call_accuracy_browser": {}}, path=history_path)
            history = load_scheduler_benchmark_history(path=history_path)

        self.assertTrue(record["passed"])
        self.assertEqual(history[0]["score"], 100.0)
        self.assertEqual(history[0]["input_case_ids"], ["tool_call_accuracy_browser"])

    def test_replaceable_brain_snapshot_keeps_capability_assets_model_independent(self):
        graph = build_capability_registry(tools=build_default_tool_registry().list_specs()).snapshot()
        snapshot = build_brain_replacement_snapshot(model_catalog=bundled_model_catalog(), capability_graph=graph, skills=[{"name": "publish.product"}], workflows={"definitions": [{"name": "content.video_generation.v1"}]})

        self.assertEqual(snapshot["schema_version"], "spiritkin.replaceable_brain.v1")
        self.assertTrue(snapshot["independent_assets"]["knowledge_policy_independent"])
        self.assertFalse(snapshot["independent_assets"]["model_bound_assets_allowed"])
        self.assertGreater(snapshot["adapter_registry"]["adapter_count"], 0)
        self.assertTrue(snapshot["capabilities"]["brain_adapter_registry"])
        self.assertFalse(snapshot["replacement_gate"]["auto_replace_allowed"])

    def test_replaceable_brain_gate_blocks_missing_lora_artifact_and_allows_reviewed_candidate(self):
        blocked = evaluate_brain_replacement(
            current_adapter_id="base_qwen35b",
            candidate_adapter=normalize_brain_adapter(
                {
                    "adapter_id": "lora_publish_v1",
                    "adapter_type": "lora",
                    "base_model_id": "Qwen/Qwen3.6-35B-A3B-Instruct",
                    "review_state": "candidate",
                    "capability_ids": ["publish_product"],
                }
            ),
            benchmark_results=[],
        )
        approved = evaluate_brain_replacement(
            current_adapter_id="base_qwen35b",
            candidate_adapter=normalize_brain_adapter(
                {
                    "adapter_id": "lora_publish_v2",
                    "adapter_type": "lora",
                    "base_model_id": "Qwen/Qwen3.6-35B-A3B-Instruct",
                    "artifact_path": "outputs/publish-lora",
                    "review_state": "approved",
                    "capability_ids": ["publish_product"],
                }
            ),
            benchmark_results=[
                {"case_id": "publish_product_workflow_assets", "capability_id": "publish_product", "score": 96, "passed": True, "critical": True},
                {"case_id": "run_tests_tool_boundary", "capability_id": "run_tests", "score": 92, "passed": True, "critical": True},
            ],
            minimum_average_score=88,
        )

        self.assertFalse(blocked.allowed)
        self.assertIn("register_downloaded_adapter_artifact", blocked.required_actions)
        self.assertTrue(approved.allowed)
        self.assertEqual(approved.status, "approved_for_staging")

    def test_cluster_carries_session_summary_into_followup_prompt(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "我来继续分析 <emotion:thinking>"

        cluster = AgentCluster(llm_client=fake_llm, device_backend=FakeDeviceBackend(), memory_limit=3)
        cluster.process("Python traceback 应该先看什么？")
        cluster.process("然后下一步呢？")

        self.assertEqual(len(cluster.memory), 4)
        self.assertTrue(any("最近对话" in prompt for prompt in prompts))

    def test_cluster_injects_knowledge_hits_into_prompt(self):
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return "我查到项目架构里有 tools 和 executors 分层 <emotion:thinking>"

        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "项目架构", "项目架构包含 tools、knowledge 和 executors 分层。")
        cluster = AgentCluster(
            llm_client=fake_llm,
            device_backend=FakeDeviceBackend(),
            knowledge_retriever=SimpleKnowledgeRetriever(store),
        )

        reply = cluster.process("项目架构文档怎么说？")

        self.assertEqual(reply.agent_name, "tool_kb_search")
        self.assertEqual(reply.emotion, "thinking")
        self.assertIn("知识检索结果", prompts[0])
        self.assertIn("项目架构包含 tools、knowledge 和 executors 分层。", prompts[0])


if __name__ == "__main__":
    unittest.main()
