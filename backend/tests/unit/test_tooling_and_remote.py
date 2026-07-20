import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.devices import InMemoryOpenClawClient
from backend.executors import (
    ExecutionRequest,
    ExecutorRemoteNodeClient,
    NodeRegistry,
    OpenClawExecutor,
    RemoteExecutionResponse,
    RemoteExecutor,
    RemoteNode,
    RemoteNodeHeartbeat,
)
from backend.knowledge import (
    HashingEmbeddingProvider,
    InMemoryKnowledgeStore,
    InMemoryVectorStore,
    JsonVectorStore,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleReranker,
    SimpleKnowledgeRetriever,
    build_embedding_retriever_from_directory,
    build_embedding_retriever_from_store,
    build_project_docs_embedding_retriever,
    build_project_docs_retriever,
    build_retriever_from_directory,
    clear_project_docs_retriever_cache,
    ingest_directory,
    ingest_text_document,
)
from backend.orchestrator.worker_pool import WorkerPool
from backend.remote import RemoteHeartbeatPoller
from backend.services.openclaw import create_openclaw_remote_node
from backend.tools import ToolCall, build_default_tool_registry


class FakeRemoteClient:
    def __init__(self, *, heartbeat_timestamp: float = 100.0):
        self.payloads = []
        self.heartbeat_timestamp = heartbeat_timestamp

    def execute(self, payload):
        self.payloads.append(payload)
        return RemoteExecutionResponse(success=True, message="remote ok", data={"node_id": payload.node_id})

    def heartbeat(self, node_id, *, aliases=None, metadata=None):
        return RemoteNodeHeartbeat(
            node_id=node_id,
            targets={"desktop"},
            aliases=set(aliases or set()),
            capabilities={"remote_desktop"},
            metadata=dict(metadata or {}),
            auth_token_id="fake-token",
            timestamp=self.heartbeat_timestamp,
        )


class FailingRemoteClient(FakeRemoteClient):
    def heartbeat(self, node_id, *, aliases=None, metadata=None):
        raise RuntimeError("heartbeat unavailable")


class ToolingAndRemoteTests(unittest.TestCase):
    def test_default_tool_registry_builds_execution_request(self):
        registry = build_default_tool_registry()

        result = registry.invoke(ToolCall(name="pointer.move", arguments={"x": 320, "y": 240}))

        self.assertTrue(result.success)
        self.assertIsNotNone(result.execution_request)
        self.assertEqual(result.execution_request.target, "local_pc")
        self.assertEqual(result.execution_request.operation, "move_pointer")
        self.assertEqual(result.execution_request.params, {"x": 320, "y": 240})

    def test_execution_tool_binds_local_browser_worker_target(self):
        registry = build_default_tool_registry()

        result = registry.invoke(
            ToolCall(
                name="browser.open_url",
                arguments={
                    "url": "https://example.com",
                    "worker_binding": {
                        "binding_type": "browser",
                        "worker_id": "local-browser",
                        "execution_target": "browser",
                    },
                },
            )
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.execution_request)
        self.assertEqual(result.execution_request.target, "browser")
        self.assertEqual(result.execution_request.operation, "browser_open_url")
        self.assertEqual(result.execution_request.params["url"], "https://example.com")
        self.assertEqual(result.execution_request.params["worker_binding"]["worker_id"], "local-browser")

    def test_execution_tool_binds_remote_browser_worker_target(self):
        registry = build_default_tool_registry()

        result = registry.invoke(
            ToolCall(
                name="browser.open_url",
                arguments={
                    "url": "https://example.com",
                    "worker_binding": {
                        "binding_type": "remote_browser",
                        "worker_id": "remote:office-pc",
                        "execution_target": "remote:office-pc",
                        "remote_node_id": "office-pc",
                    },
                },
            )
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.execution_request)
        self.assertEqual(result.execution_request.target, "remote:office-pc")
        self.assertEqual(result.execution_request.operation, "browser_open_url")
        self.assertEqual(result.execution_request.params["node_id"], "office-pc")
        self.assertEqual(result.execution_request.params["remote_target"], "browser")
        self.assertEqual(result.execution_request.params["worker_binding"]["binding_type"], "remote_browser")

    def test_remote_browser_execution_request_routes_through_worker_pool(self):
        client = FakeRemoteClient()
        node_registry = NodeRegistry(
            [RemoteNode(node_id="office-pc", client=client, targets={"browser", "desktop"}, capabilities={"browser.open_url"})]
        )
        remote_executor = RemoteExecutor(node_registry)
        registry = build_default_tool_registry()
        tool_result = registry.invoke(
            ToolCall(
                name="browser.open_url",
                arguments={
                    "url": "https://example.com",
                    "worker_binding": {
                        "binding_type": "remote_browser",
                        "execution_target": "remote:office-pc",
                        "remote_node_id": "office-pc",
                    },
                },
            )
        )
        pool = WorkerPool([remote_executor])

        execution = pool.execute(tool_result.execution_request, actor="unit-test")

        self.assertTrue(execution.result.success)
        self.assertEqual(execution.worker.worker_id, "executor:remote")
        self.assertEqual(client.payloads[0].node_id, "office-pc")
        self.assertEqual(client.payloads[0].target, "browser")
        self.assertEqual(client.payloads[0].operation, "browser_open_url")
        self.assertEqual(client.payloads[0].params["url"], "https://example.com")

    def test_default_tool_registry_exposes_openclaw_status_as_read_only_tool(self):
        registry = build_default_tool_registry()

        result = registry.invoke(ToolCall(name="arm.status", arguments={}))
        specs = {spec.name: spec for spec in registry.list_specs()}

        self.assertTrue(result.success)
        self.assertEqual(result.execution_request.target, "openclaw")
        self.assertEqual(result.execution_request.operation, "status")
        self.assertTrue(specs["arm.status"].read_only)

    def test_knowledge_tool_returns_ranked_hits(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "OpenClaw Manual", "OpenClaw arm home command and gripper control guide")
        retriever = SimpleKnowledgeRetriever(store)
        registry = build_default_tool_registry(knowledge_retriever=retriever)

        result = registry.invoke(ToolCall(name="kb.search", arguments={"query": "OpenClaw gripper", "top_k": 2}))

        self.assertTrue(result.success)
        self.assertEqual(result.message, "命中 1 条知识片段")
        self.assertEqual(result.data[0]["source_title"], "OpenClaw Manual")
        self.assertIn("gripper", result.data[0]["text"].lower())

    def test_default_tool_registry_exposes_web_search_tool(self):
        registry = build_default_tool_registry()

        specs = {spec.name: spec for spec in registry.list_specs()}

        self.assertIn("web.search", specs)
        self.assertEqual(specs["web.search"].target, "web")
        self.assertTrue(specs["web.search"].read_only)

    def test_tool_registry_returns_structured_error_for_unknown_tool(self):
        registry = build_default_tool_registry()

        result = registry.invoke(ToolCall(name="unknown.tool", arguments={}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_not_registered")
        self.assertEqual(result.metadata["tool_name"], "unknown.tool")

    def test_remote_executor_routes_request_by_alias(self):
        client = FakeRemoteClient()
        registry = NodeRegistry(
            [RemoteNode(node_id="office-pc", client=client, aliases={"company_pc"}, targets={"desktop"})]
        )
        executor = RemoteExecutor(registry)

        result = executor.execute(ExecutionRequest(target="company_pc", operation="click_pointer", params={"x": 10, "y": 20}))

        self.assertTrue(result.success)
        self.assertEqual(result.message, "remote ok")
        self.assertEqual(client.payloads[0].node_id, "office-pc")
        self.assertEqual(client.payloads[0].target, "desktop")
        self.assertEqual(client.payloads[0].operation, "click_pointer")

    def test_node_registry_registers_heartbeat_and_updates_online_snapshot(self):
        client = FakeRemoteClient()
        registry = NodeRegistry([RemoteNode(node_id="office-pc", client=client)])

        node = registry.register_heartbeat(
            RemoteNodeHeartbeat(
                node_id="office-pc",
                targets={"desktop"},
                aliases={"公司电脑"},
                capabilities={"desktop.execute", "desktop.inventory"},
                metadata={"label": "Office PC"},
                auth_token_id="token-1",
                timestamp=100.0,
            )
        )

        self.assertEqual(node.status, "online")
        self.assertEqual(node.auth_token_id, "token-1")
        self.assertIn("desktop.execute", node.capabilities)
        self.assertEqual(registry.list_online_nodes(ttl_seconds=30, now=120.0)[0].node_id, "office-pc")

    def test_node_registry_exposes_remote_workers_for_capability_scheduler(self):
        client = FakeRemoteClient()
        registry = NodeRegistry([RemoteNode(node_id="office-pc", client=client)])
        registry.register_heartbeat(
            RemoteNodeHeartbeat(
                node_id="office-pc",
                targets={"desktop", "browser"},
                aliases={"company_pc"},
                capabilities={"browser.open_url", "python.run", "ffmpeg.convert"},
                metadata={"label": "Office PC", "workspace": "office"},
                auth_token_id="token-1",
                timestamp=100.0,
            )
        )

        descriptors = registry.worker_descriptors(ttl_seconds=30, now=110.0)
        pool = WorkerPool(external_workers=descriptors)
        decision = pool.schedule({"needs": ["browser"], "prefer_remote": True, "workspace": "office"})

        self.assertEqual(descriptors[0].worker_id, "remote:office-pc")
        self.assertEqual(descriptors[0].worker_type, "generic_remote_worker")
        self.assertIn("browser", descriptors[0].capability_namespaces)
        self.assertIn("Remote Worker", descriptors[0].legacy_names)
        self.assertEqual(decision.status, "selected")
        self.assertEqual(decision.selected.worker_id, "remote:office-pc")

    def test_node_registry_marks_stale_nodes_when_heartbeat_expires(self):
        client = FakeRemoteClient()
        registry = NodeRegistry([RemoteNode(node_id="office-pc", client=client, last_seen_at=10.0, status="online")])

        stale = registry.mark_stale(ttl_seconds=15, now=30.5)

        self.assertEqual(stale, ["office-pc"])
        self.assertEqual(registry.get("office-pc").status, "stale")

    def test_node_registry_refresh_all_from_clients_builds_snapshot_and_marks_failures(self):
        registry = NodeRegistry(
            [
                RemoteNode(node_id="office-pc", client=FakeRemoteClient(heartbeat_timestamp=110.0), aliases={"company_pc"}),
                RemoteNode(node_id="broken-pc", client=FailingRemoteClient(), targets={"desktop"}, status="online", last_seen_at=80.0),
            ]
        )

        result = registry.refresh_all_from_clients(ttl_seconds=30, now=120.0)
        snapshot = registry.snapshot(ttl_seconds=30, now=120.0)

        self.assertEqual(result["refreshed"], ["office-pc"])
        self.assertIn("broken-pc", result["failed"])
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(snapshot["status_counts"]["online"], 1)
        self.assertEqual(snapshot["status_counts"]["offline"], 1)
        office_node = next(node for node in snapshot["nodes"] if node["node_id"] == "office-pc")
        self.assertEqual(office_node["status"], "online")
        self.assertEqual(office_node["auth_token_id"], "fake-token")
        self.assertIn("desktop", office_node["targets"])
        broken_node = next(node for node in snapshot["nodes"] if node["node_id"] == "broken-pc")
        self.assertEqual(broken_node["consecutive_heartbeat_failures"], 1)
        self.assertIn("heartbeat unavailable", broken_node["last_heartbeat_error"])
        self.assertEqual(snapshot["recent_events"][-1]["kind"], "heartbeat_failed")

    def test_node_registry_records_stale_event_once(self):
        registry = NodeRegistry([RemoteNode(node_id="office-pc", client=FakeRemoteClient(), last_seen_at=10.0, status="online")])

        first = registry.mark_stale(ttl_seconds=15, now=30.5)
        second = registry.mark_stale(ttl_seconds=15, now=40.5)
        snapshot = registry.snapshot(ttl_seconds=15, now=40.5)

        self.assertEqual(first, ["office-pc"])
        self.assertEqual(second, ["office-pc"])
        stale_events = [event for event in snapshot["recent_events"] if event["kind"] == "heartbeat_stale"]
        self.assertEqual(len(stale_events), 1)

    def test_node_registry_skips_stale_or_offline_nodes_for_routing(self):
        registry = NodeRegistry(
            [
                RemoteNode(node_id="office-pc", client=FakeRemoteClient(), aliases={"company_pc"}, targets={"desktop"}, status="stale"),
                RemoteNode(node_id="lab-pc", client=FailingRemoteClient(), targets={"desktop"}, status="offline"),
            ]
        )

        self.assertIsNone(registry.find_for_request(ExecutionRequest(target="company_pc", operation="status")))
        self.assertIsNone(registry.find_for_request(ExecutionRequest(target="remote:lab-pc", operation="status")))

    def test_executor_remote_node_client_can_build_heartbeat_snapshot(self):
        remote_client = ExecutorRemoteNodeClient([OpenClawExecutor(client=InMemoryOpenClawClient())])

        heartbeat = remote_client.heartbeat("lab-arm", aliases={"实验机械臂"}, metadata={"transport": "in_memory"})

        self.assertEqual(heartbeat.node_id, "lab-arm")
        self.assertIn("openclaw", heartbeat.targets)
        self.assertIn("openclaw", heartbeat.capabilities)
        self.assertEqual(heartbeat.metadata["transport"], "in_memory")

    def test_remote_executor_normalizes_explicit_remote_target_for_single_target_node(self):
        client = FakeRemoteClient()
        registry = NodeRegistry([RemoteNode(node_id="lab-arm", client=client, targets={"openclaw"})])
        executor = RemoteExecutor(registry)

        result = executor.execute(ExecutionRequest(target="remote:lab-arm", operation="status"))

        self.assertTrue(result.success)
        self.assertEqual(client.payloads[0].node_id, "lab-arm")
        self.assertEqual(client.payloads[0].target, "openclaw")

    def test_remote_heartbeat_poller_runs_refresh_and_emits_callback(self):
        class RecordingRegistry:
            def __init__(self):
                self.called = threading.Event()

            def refresh_all_from_clients(self, *, ttl_seconds: float = 30.0, now=None):
                self.called.set()
                return {"total": 1, "ttl_seconds": ttl_seconds}

        registry = RecordingRegistry()
        callbacks = []
        poller = RemoteHeartbeatPoller(registry, interval_seconds=1.0, ttl_seconds=12.0, on_result=callbacks.append)

        try:
            poller.start()
            self.assertTrue(registry.called.wait(0.5))
        finally:
            poller.stop()

        self.assertEqual(callbacks[0]["total"], 1)
        self.assertEqual(callbacks[0]["ttl_seconds"], 12.0)

    def test_executor_remote_node_client_can_run_openclaw_software_smoke_flow(self):
        remote_client = ExecutorRemoteNodeClient([OpenClawExecutor(client=InMemoryOpenClawClient())])
        registry = NodeRegistry([RemoteNode(node_id="lab-arm", client=remote_client, targets={"openclaw"})])
        executor = RemoteExecutor(registry)

        move_result = executor.execute(
            ExecutionRequest(target="remote:lab-arm", operation="move_to", params={"x": 1, "y": 2, "z": 3})
        )
        grip_result = executor.execute(ExecutionRequest(target="openclaw", operation="close_gripper"))
        status_result = executor.execute(ExecutionRequest(target="openclaw", operation="status"))

        self.assertTrue(move_result.success)
        self.assertTrue(grip_result.success)
        self.assertTrue(status_result.success)
        self.assertEqual(status_result.data["position"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertFalse(status_result.data["gripper_opened"])
        self.assertEqual(status_result.metadata["node_id"], "lab-arm")
        self.assertEqual(status_result.metadata["remote_target"], "openclaw")

    def test_remote_executor_propagates_executor_error_details_from_remote_node(self):
        remote_client = ExecutorRemoteNodeClient([OpenClawExecutor(client=InMemoryOpenClawClient())])
        registry = NodeRegistry([RemoteNode(node_id="lab-arm", client=remote_client, targets={"openclaw"})])
        executor = RemoteExecutor(registry)

        result = executor.execute(ExecutionRequest(target="remote:lab-arm", operation="move_to", params={"x": 1, "y": 2}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "missing_params")
        self.assertEqual(result.metadata["missing_param"], "z")
        self.assertEqual(result.metadata["executor"], "openclaw")

    def test_service_can_build_remote_openclaw_node_with_persistent_state(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "openclaw" / "remote-state.json"

            first_registry = NodeRegistry([create_openclaw_remote_node("lab-arm", state_path=state_path)])
            first_executor = RemoteExecutor(first_registry)
            move_result = first_executor.execute(
                ExecutionRequest(target="remote:lab-arm", operation="move_to", params={"x": 9, "y": 8, "z": 7})
            )

            second_registry = NodeRegistry([create_openclaw_remote_node("lab-arm", state_path=state_path)])
            second_executor = RemoteExecutor(second_registry)
            status_result = second_executor.execute(ExecutionRequest(target="remote:lab-arm", operation="status"))

            self.assertTrue(move_result.success)
            self.assertTrue(state_path.exists())
            self.assertTrue(status_result.success)
            self.assertEqual(status_result.data["position"], {"x": 9.0, "y": 8.0, "z": 7.0})
            self.assertEqual(status_result.data["transport"], "local_json")
            self.assertEqual(status_result.metadata["node_id"], "lab-arm")
            self.assertEqual(status_result.metadata["remote_target"], "openclaw")

    def test_ingest_directory_loads_markdown_files_for_kb_search(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "architecture.md").write_text("项目架构包含 tools knowledge remote", encoding="utf-8")
            (root / "notes.txt").write_text("训练数据设计需要样本与标签", encoding="utf-8")
            (root / "ignore.json").write_text('{"a":1}', encoding="utf-8")

            store = InMemoryKnowledgeStore()
            ingested_files = ingest_directory(store, root)
            retriever = SimpleKnowledgeRetriever(store)

            self.assertEqual(len(ingested_files), 2)
            result = retriever.retrieve("项目架构 knowledge", top_k=2)
            self.assertEqual(result[0].source_title, "architecture")
            self.assertIn("tools knowledge remote", result[0].chunk.text)

    def test_build_retriever_from_directory_returns_searchable_retriever(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ops.md").write_text("客服 SOP 包含售后与退款话术", encoding="utf-8")

            retriever = build_retriever_from_directory(root)
            result = retriever.retrieve("退款话术", top_k=1)

            self.assertEqual(result[0].source_title, "ops")
            self.assertIn("售后与退款话术", result[0].chunk.text)

    def test_build_project_docs_retriever_reads_docs_folder_under_project_root(self):
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            docs_dir = project_root / "docs"
            docs_dir.mkdir()
            (docs_dir / "roadmap.md").write_text("路线图先做工具层，再做知识库和远程节点", encoding="utf-8")

            clear_project_docs_retriever_cache()
            retriever = build_project_docs_retriever(project_root)

            self.assertIsNotNone(retriever)
            result = retriever.retrieve("远程节点", top_k=1)
            self.assertEqual(result[0].source_title, "roadmap")

    def test_build_project_docs_retriever_reuses_cached_instance(self):
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            docs_dir = project_root / "docs"
            docs_dir.mkdir()
            (docs_dir / "roadmap.md").write_text("路线图强调文档知识库", encoding="utf-8")

            fake_retriever = SimpleKnowledgeRetriever(InMemoryKnowledgeStore())
            clear_project_docs_retriever_cache()

            with patch("backend.knowledge.loader.build_retriever_from_directory", return_value=fake_retriever) as builder:
                first = build_project_docs_retriever(project_root)
                second = build_project_docs_retriever(project_root)

            self.assertIs(first, second)
            builder.assert_called_once()

    def test_build_project_docs_embedding_retriever_uses_separate_cache_key(self):
        with TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            docs_dir = project_root / "docs"
            docs_dir.mkdir()
            (docs_dir / "roadmap.md").write_text("embedding 路径需要单独缓存", encoding="utf-8")

            keyword_retriever = SimpleKnowledgeRetriever(InMemoryKnowledgeStore())
            embedding_retriever = SimpleKnowledgeRetriever(InMemoryKnowledgeStore())
            clear_project_docs_retriever_cache()

            with patch("backend.knowledge.loader.build_retriever_from_directory", return_value=keyword_retriever) as keyword_builder:
                first_keyword = build_project_docs_retriever(project_root)
            with patch(
                "backend.knowledge.loader.build_embedding_retriever_from_directory",
                return_value=embedding_retriever,
            ) as embedding_builder:
                first_embedding = build_project_docs_embedding_retriever(project_root)
                second_embedding = build_project_docs_embedding_retriever(project_root)

            self.assertIs(first_keyword, keyword_retriever)
            self.assertIs(first_embedding, second_embedding)
            self.assertIsNot(first_keyword, first_embedding)
            keyword_builder.assert_called_once()
            embedding_builder.assert_called_once()

    def test_build_embedding_retriever_from_store_returns_relevant_hits(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "客服SOP", "退款申请需要先核验订单，再给出售后话术")
        ingest_text_document(store, "doc-2", "开发记录", "向量检索后续接 embedding provider 与 vector store")

        retriever = build_embedding_retriever_from_store(
            store,
            embedding_provider=HashingEmbeddingProvider(dimensions=128),
            vector_store=InMemoryVectorStore(),
        )

        result = retriever.retrieve("退款售后怎么处理", top_k=1)

        self.assertEqual(result[0].source_title, "客服SOP")
        self.assertIn("售后话术", result[0].chunk.text)
        self.assertIn("rerank_score", result[0].chunk.metadata)

    def test_json_vector_store_persists_and_reloads_records(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "客服SOP", "退款申请需要先核验订单，再给出售后话术")
        with TemporaryDirectory() as temp_dir:
            vector_path = Path(temp_dir) / "vectors.json"
            retriever = build_embedding_retriever_from_store(
                store,
                embedding_provider=HashingEmbeddingProvider(dimensions=128),
                vector_store=JsonVectorStore(vector_path),
                reranker=None,
            )

            first = retriever.retrieve("退款售后怎么处理", top_k=1)
            reloaded = JsonVectorStore(vector_path)
            second = reloaded.search(HashingEmbeddingProvider(dimensions=128).embed_query("退款售后怎么处理"), top_k=1)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(second[0].source_title, "客服SOP")

    def test_build_embedding_retriever_from_directory_can_persist_vector_store(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "docs"
            root.mkdir()
            (root / "ops.md").write_text("客服 SOP 需要处理退款、售后与补发场景", encoding="utf-8")
            vector_path = Path(temp_dir) / "vectors.json"

            with patch.dict("os.environ", {"SPIRITKIN_ALLOW_HASHING_EMBEDDINGS": "1", "SPIRITKIN_EMBEDDING_PROVIDER": "hashing"}, clear=False):
                retriever = build_embedding_retriever_from_directory(root, vector_store_path=vector_path, reranker=None)

            hits = retriever.retrieve("补发售后", top_k=1)
            persisted = JsonVectorStore(vector_path).snapshot()
            vector_exists = vector_path.exists()

        self.assertTrue(hits)
        self.assertEqual(persisted["total"], 1)
        self.assertTrue(vector_exists)

    def test_build_embedding_retriever_from_directory_works_with_kb_search_tool(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ops.md").write_text("客服 SOP 需要处理退款、售后与补发场景", encoding="utf-8")

            with patch.dict("os.environ", {"SPIRITKIN_ALLOW_HASHING_EMBEDDINGS": "1"}, clear=False):
                retriever = build_embedding_retriever_from_directory(root, reranker=None)
            registry = build_default_tool_registry(knowledge_retriever=retriever)
            result = registry.invoke(ToolCall(name="kb.search", arguments={"query": "补发售后", "top_k": 1}))

            self.assertTrue(result.success)
            self.assertEqual(result.data[0]["source_title"], "ops")

    def test_openai_compatible_embedding_provider_calls_embeddings_endpoint(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data":[{"index":0,"embedding":[1,0]},{"index":1,"embedding":[0,1]}]}'

        with patch("backend.knowledge.embedding.request.urlopen", return_value=FakeResponse()) as urlopen:
            provider = OpenAICompatibleEmbeddingProvider(base_url="http://127.0.0.1:1234/v1", model="embed-local", api_key="lm-studio")
            vectors = provider.embed_documents(["alpha", "beta"])

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertIn("/embeddings", urlopen.call_args[0][0].full_url)

    def test_nomic_embedding_provider_adds_retrieval_task_prefixes(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"data":[{"index":0,"embedding":[1,0]}]}'

        with patch("backend.knowledge.embedding.request.urlopen", return_value=FakeResponse()) as urlopen:
            provider = OpenAICompatibleEmbeddingProvider(
                base_url="http://127.0.0.1:1234/v1",
                model="text-embedding-nomic-embed-text-v1.5",
                api_key="lm-studio",
            )
            provider.embed_documents(["document text"])
            document_body = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))
            provider.embed_query("query text")
            query_body = json.loads(urlopen.call_args[0][0].data.decode("utf-8"))

        self.assertEqual(document_body["input"], ["search_document: document text"])
        self.assertEqual(query_body["input"], ["search_query: query text"])

    def test_build_embedding_provider_rejects_hashing_without_explicit_dev_flag(self):
        from backend.knowledge import build_embedding_provider

        with patch.dict("os.environ", {"SPIRITKIN_EMBEDDING_PROVIDER": "hashing"}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                build_embedding_provider()

        self.assertIn("hashing embeddings are a dev fallback", str(ctx.exception))

    def test_build_embedding_provider_allows_hashing_with_explicit_dev_flag(self):
        from backend.knowledge import build_embedding_provider

        with patch.dict("os.environ", {"SPIRITKIN_EMBEDDING_PROVIDER": "hashing", "SPIRITKIN_ALLOW_HASHING_EMBEDDINGS": "1"}, clear=True):
            provider = build_embedding_provider()

        self.assertIsInstance(provider, HashingEmbeddingProvider)

    def test_openai_compatible_reranker_orders_hits_from_model_json(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "A", "first")
        ingest_text_document(store, "doc-2", "B", "second")
        hits = SimpleKnowledgeRetriever(store).retrieve("first second", top_k=2)

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"order\\":[\\"2\\",\\"1\\"]}"}}]}'

        with patch("backend.knowledge.reranker.request.urlopen", return_value=FakeResponse()):
            reranked = OpenAICompatibleReranker(base_url="http://127.0.0.1:1234/v1", model="rerank-local").rerank("query", hits, top_k=2)

        self.assertEqual([item.source_title for item in reranked], [hits[1].source_title, hits[0].source_title])

    def test_openai_compatible_reranker_circuit_breaks_after_timeout(self):
        store = InMemoryKnowledgeStore()
        ingest_text_document(store, "doc-1", "A", "first")
        hits = SimpleKnowledgeRetriever(store).retrieve("first", top_k=1)
        reranker = OpenAICompatibleReranker(
            base_url="http://127.0.0.1:1234/v1",
            model="rerank-local",
            timeout=0.5,
            cooldown_seconds=60,
        )

        with patch("backend.knowledge.reranker.request.urlopen", side_effect=TimeoutError("stalled")) as urlopen:
            first = reranker.rerank("first", hits, top_k=1)
            second = reranker.rerank("first", hits, top_k=1)

        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(first[0].source_title, "A")
        self.assertEqual(second[0].source_title, "A")

    def test_openai_compatible_reranker_uses_bounded_env_timeouts(self):
        with patch.dict(
            os.environ,
            {
                "SPIRITKIN_RERANKER_TIMEOUT_SECONDS": "2.5",
                "SPIRITKIN_RERANKER_COOLDOWN_SECONDS": "12",
            },
            clear=False,
        ):
            reranker = OpenAICompatibleReranker(
                base_url="http://127.0.0.1:1234/v1",
                model="rerank-local",
            )

        self.assertEqual(reranker.timeout, 2.5)
        self.assertEqual(reranker.cooldown_seconds, 12.0)


if __name__ == "__main__":
    unittest.main()
