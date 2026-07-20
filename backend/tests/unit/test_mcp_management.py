import json
import os
import queue
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.command_gateway import (
    build_desktop_mcp_management_response,
    build_desktop_mcp_management_update_response,
)
from backend.app.mcp_management import build_mcp_management_snapshot, mcp_adapter_config_entries, probe_mcp_servers
from backend.app.module_management import build_module_management_snapshot
from backend.security.safety_control import set_safety_stop
from backend.tools import ToolCall, build_default_tool_registry
from backend.tools.mcp_adapter import build_mcp_adapter_from_config


def _write_mcp_stdio_stub(path: Path) -> Path:
    script = path / "mcp_stdio_stub.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg=json.loads(line)\n"
        "    if 'id' not in msg:\n"
        "        continue\n"
        "    method=msg.get('method')\n"
        "    if method=='initialize':\n"
        "        result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'stub','version':'1'}}\n"
        "    elif method=='tools/list':\n"
        "        result={'tools':[{'name':'search','description':'stub search','inputSchema':{'type':'object'}},{'name':'summarize','description':'stub summary','inputSchema':{'type':'object','properties':{'text':{'type':'string'}}}}]}\n"
        "    elif method=='tools/call':\n"
        "        result={'content':[{'type':'text','text':'stub result'}],'structuredContent':{'echo':msg.get('params',{}).get('arguments',{})}}\n"
        "    else:\n"
        "        result={}\n"
        "    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}), flush=True)\n",
        encoding="utf-8",
    )
    return script


def _rpc_result(message: dict, result: dict) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode("utf-8")


class _StreamableMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_seen: list[dict] = []
    call_attempts = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        type(self).requests_seen.append({"message": message, "headers": dict(self.headers)})
        method = message.get("method")
        if self.path == "/slow":
            time.sleep(1.25)
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        stream_response = self.path == "/mcp-sse"
        if method == "tools/call" and not stream_response:
            type(self).call_attempts += 1
            if type(self).call_attempts == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "streamable-stub", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "search", "description": "HTTP search", "inputSchema": {"type": "object"}},
                    {
                        "name": "summarize",
                        "description": "HTTP summary",
                        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    },
                ]
            }
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": "http result"}],
                "structuredContent": {"echo": message.get("params", {}).get("arguments", {})},
            }
        else:
            result = {}
        body = _rpc_result(message, result)
        if stream_response:
            body = b"event: message\nid: event-" + str(message["id"]).encode("ascii") + b"\ndata: " + body + b"\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream_response else "application/json")
        self.send_header("Content-Length", str(len(body)))
        if method == "initialize":
            self.send_header("MCP-Session-Id", f"http-session-{type(self).call_attempts}")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, *_args):
        return


class _LegacySSEMCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    streams: list[queue.Queue[bytes]] = []
    streams_lock = threading.Lock()
    requests_seen: list[dict] = []

    def do_GET(self):
        if self.path != "/sse":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        events: queue.Queue[bytes] = queue.Queue()
        with type(self).streams_lock:
            type(self).streams.append(events)
        try:
            self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
            self.wfile.flush()
            while True:
                event = events.get(timeout=5)
                self.wfile.write(event)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, queue.Empty):
            return
        finally:
            with type(self).streams_lock:
                if events in type(self).streams:
                    type(self).streams.remove(events)

    def do_POST(self):
        if self.path != "/messages":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        message = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        type(self).requests_seen.append({"message": message, "headers": dict(self.headers)})
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if "id" not in message:
            return
        method = message.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sse-stub", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "search", "description": "SSE search", "inputSchema": {"type": "object"}},
                    {
                        "name": "summarize",
                        "description": "SSE summary",
                        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    },
                ]
            }
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": "sse result"}],
                "structuredContent": {"echo": message.get("params", {}).get("arguments", {})},
            }
        else:
            result = {}
        data = _rpc_result(message, result)
        with type(self).streams_lock:
            events = type(self).streams[-1] if type(self).streams else None
        if events is not None:
            events.put(b"event: message\ndata: " + data + b"\n\n")

    def log_message(self, *_args):
        return


class _UnauthorizedMCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = b'{"error":"unauthorized"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@contextmanager
def _serve_mcp(handler):
    handler.requests_seen = []
    if handler is _StreamableMCPHandler:
        handler.call_attempts = 0
    if handler is _LegacySSEMCPHandler:
        handler.streams = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class MCPManagementTests(unittest.TestCase):
    def test_mcp_registry_can_save_review_enable_and_export_tool_mapping(self):
        with TemporaryDirectory() as tmp:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                status, payload = build_desktop_mcp_management_response()
                self.assertEqual(status, 200)
                self.assertEqual(payload["mcp_management"]["server_count"], 0)

                save_status, save_payload = build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "docs",
                        "label": "Docs MCP",
                        "transport": "stdio",
                        "command": "npx docs-mcp",
                        "owner_agent_ids": ["programming"],
                        "env_refs": ["DOCS_TOKEN"],
                        "filesystem_scopes": ["docs"],
                        "tools": [
                            {
                                "mcp_tool_name": "search",
                                "internal_tool_name": "mcp.docs.search",
                                "read_only": True,
                                "risk_level": "low",
                            }
                        ],
                    }
                )
                self.assertEqual(save_status, 200)
                self.assertEqual(save_payload["server"]["review_state"], "candidate")
                self.assertFalse(save_payload["server"]["enabled"])
                self.assertEqual(save_payload["mcp_management"]["tool_mappings"], [])

                review_status, review_payload = build_desktop_mcp_management_update_response(
                    {"action": "approve_server", "server_id": "docs", "reviewer": "unit-test"}
                )
                self.assertEqual(review_status, 200)
                self.assertEqual(review_payload["server"]["review_state"], "approved")

                enable_status, enable_payload = build_desktop_mcp_management_update_response(
                    {"action": "enable_server", "server_id": "docs"}
                )
                self.assertEqual(enable_status, 200)
                snapshot = enable_payload["mcp_management"]
                self.assertEqual(snapshot["enabled_count"], 1)
                self.assertEqual(snapshot["ready_count"], 1)
                self.assertEqual(snapshot["ready_mapping_count"], 1)
                self.assertEqual(snapshot["tool_mappings"][0]["internal_tool_name"], "mcp.docs.search")
                self.assertGreaterEqual(snapshot["audit_count"], 3)
                self.assertEqual(snapshot["audit_log"][-1]["action"], "enable_server")

                entries = mcp_adapter_config_entries()
                adapter = build_mcp_adapter_from_config(entries)
                tool = adapter.generate_tool_registry_entry("docs", "search")
                self.assertIsNotNone(tool)
                self.assertEqual(tool.spec.name, "mcp.docs.search")

                direct = build_mcp_management_snapshot()
                self.assertEqual(direct["server_count"], 1)
                self.assertTrue(Path(registry_path).exists())

    def test_default_tool_registry_imports_only_ready_mcp_mappings(self):
        with TemporaryDirectory() as tmp:
            stub = _write_mcp_stdio_stub(Path(tmp))
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "candidate",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(stub)],
                        "owner_agent_ids": ["programming"],
                        "enabled": True,
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.candidate.search", "read_only": True}],
                    }
                )
                registry = build_default_tool_registry()
                specs = {spec.name: spec for spec in registry.list_specs()}
                self.assertNotIn("mcp.candidate.search", specs)

                build_desktop_mcp_management_update_response({"action": "approve_server", "server_id": "candidate", "reviewer": "unit-test"})
                registry = build_default_tool_registry()
                specs = {spec.name: spec for spec in registry.list_specs()}
                self.assertIn("mcp.candidate.search", specs)

                result = registry.invoke(ToolCall(name="mcp.candidate.search", arguments={"query": "docs"}))
                self.assertTrue(result.success)
                self.assertFalse(result.metadata["pending_mcp_execution"])
                self.assertEqual(result.data["mcp_server"], "candidate")
                self.assertEqual(result.data["mcp"]["result"]["structuredContent"]["echo"], {"query": "docs"})

                self.assertNotIn("mcp.candidate.summarize", specs)

    def test_dynamic_mcp_tool_registration_is_opt_in_and_uses_tools_list(self):
        with TemporaryDirectory() as tmp:
            stub = _write_mcp_stdio_stub(Path(tmp))
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "candidate",
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [str(stub)],
                        "owner_agent_ids": ["programming"],
                        "enabled": True,
                        "review_state": "approved",
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.candidate.search", "read_only": True}],
                    }
                )

                default_specs = {spec.name for spec in build_default_tool_registry().list_specs()}
                self.assertNotIn("mcp.candidate.summarize", default_specs)

                with patch.dict(os.environ, {"SPIRITKIN_MCP_DYNAMIC_TOOL_REGISTRATION": "1"}, clear=False):
                    dynamic_registry = build_default_tool_registry()
                dynamic_specs = {spec.name: spec for spec in dynamic_registry.list_specs()}
                self.assertIn("mcp.candidate.summarize", dynamic_specs)
                self.assertEqual(dynamic_specs["mcp.candidate.summarize"].schema["properties"]["text"]["type"], "string")

    def test_ready_mappings_require_agent_allowlist(self):
        with TemporaryDirectory() as tmp:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "no-owner",
                        "transport": "stdio",
                        "command": "npx no-owner-mcp",
                        "enabled": True,
                        "review_state": "approved",
                        "tools": [{"mcp_tool_name": "run", "internal_tool_name": "mcp.no_owner.run"}],
                    }
                )
                snapshot = build_mcp_management_snapshot()

        self.assertEqual(snapshot["ready_count"], 0)
        self.assertEqual(snapshot["tool_mappings"], [])
        self.assertIn("missing_agent_allowlist", snapshot["servers"][0]["health"]["issues"])

    def test_local_http_server_with_allowlist_can_be_ready(self):
        with TemporaryDirectory() as tmp:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "local-http",
                        "transport": "http",
                        "url": "http://127.0.0.1:8790/mcp",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.local_http.search", "read_only": True}],
                    }
                )
                snapshot = build_mcp_management_snapshot()

        self.assertEqual(snapshot["ready_count"], 1)
        self.assertEqual(snapshot["ready_mapping_count"], 1)
        self.assertEqual(snapshot["servers"][0]["health"]["issues"], [])

    def test_module_management_reports_mcp_attention_for_unreviewed_enabled_server(self):
        with TemporaryDirectory() as tmp:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "unsafe-http",
                        "transport": "http",
                        "url": "http://example.com/mcp",
                        "enabled": True,
                        "tools": [{"mcp_tool_name": "run", "internal_tool_name": "mcp.unsafe.run"}],
                    }
                )
                snapshot = build_module_management_snapshot(ecosystem_snapshot={"score": {"total": 90}, "proposals": [], "systems": {}})

        modules = {module["module_id"]: module for module in snapshot["modules"]}
        self.assertIn("mcp_management", modules)
        self.assertEqual(modules["mcp_management"]["status"], "needs_attention")
        self.assertGreaterEqual(modules["mcp_management"]["medium_action_count"], 1)

    def test_streamable_http_executes_retries_discovers_and_audits(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_StreamableMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            env = {
                "SPIRITKIN_MCP_REGISTRY_PATH": registry_path,
                "SPIRITKIN_MCP_DYNAMIC_TOOL_REGISTRATION": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "streamable-http",
                        "transport": "http",
                        "url": f"{base_url}/mcp",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "timeout_seconds": 3,
                        "max_retries": 2,
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.streamable.search", "read_only": True}],
                    }
                )
                registry = build_default_tool_registry()
                specs = {spec.name: spec for spec in registry.list_specs()}
                result = registry.invoke(ToolCall(name="mcp.streamable.search", arguments={"query": "docs"}))
                snapshot = build_mcp_management_snapshot()

        self.assertIn("mcp.streamable_http.summarize", specs)
        self.assertEqual(specs["mcp.streamable_http.summarize"].schema["properties"]["text"]["type"], "string")
        self.assertTrue(result.success)
        self.assertEqual(result.data["mcp"]["result"]["structuredContent"]["echo"], {"query": "docs"})
        self.assertEqual(result.metadata["transport_stats"]["attempts"], 2)
        self.assertEqual(result.metadata["transport_stats"]["reconnects"], 1)
        methods = [item["message"].get("method") for item in _StreamableMCPHandler.requests_seen]
        self.assertGreaterEqual(methods.count("initialize"), 3)
        subsequent = [item for item in _StreamableMCPHandler.requests_seen if item["message"].get("method") in {"tools/list", "tools/call"}]
        self.assertTrue(all(item["headers"].get("MCP-Session-Id", "").startswith("http-session-") for item in subsequent))
        self.assertTrue(all(item["headers"].get("MCP-Protocol-Version") == "2025-11-25" for item in subsequent))
        audit_actions = [item["action"] for item in snapshot["audit_log"]]
        self.assertIn("tool_discovery_completed", audit_actions)
        self.assertIn("tool_call_completed", audit_actions)

    def test_legacy_sse_executes_through_endpoint_event_and_audits(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_LegacySSEMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            env = {
                "SPIRITKIN_MCP_REGISTRY_PATH": registry_path,
                "SPIRITKIN_MCP_DYNAMIC_TOOL_REGISTRATION": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "legacy-sse",
                        "transport": "sse",
                        "url": f"{base_url}/sse",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "timeout_seconds": 3,
                        "max_retries": 1,
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.legacy.search", "read_only": True}],
                    }
                )
                registry = build_default_tool_registry()
                specs = {spec.name: spec for spec in registry.list_specs()}
                result = registry.invoke(ToolCall(name="mcp.legacy.search", arguments={"query": "events"}))
                snapshot = build_mcp_management_snapshot()

        self.assertIn("mcp.legacy_sse.summarize", specs)
        self.assertEqual(specs["mcp.legacy_sse.summarize"].schema["properties"]["text"]["type"], "string")
        self.assertTrue(result.success, result.message)
        self.assertEqual(result.data["mcp"]["result"]["structuredContent"]["echo"], {"query": "events"})
        self.assertEqual(result.metadata["transport"], "sse")
        self.assertEqual(result.metadata["transport_stats"]["attempts"], 1)
        methods = [item["message"].get("method") for item in _LegacySSEMCPHandler.requests_seen]
        self.assertGreaterEqual(methods.count("initialize"), 2)
        self.assertIn("tools/list", methods)
        self.assertIn("tools/call", methods)
        audit_actions = [item["action"] for item in snapshot["audit_log"]]
        self.assertIn("tool_discovery_completed", audit_actions)
        self.assertIn("tool_call_completed", audit_actions)

    def test_streamable_http_accepts_sse_responses(self):
        with _serve_mcp(_StreamableMCPHandler) as base_url:
            adapter = build_mcp_adapter_from_config(
                [
                    {
                        "mcp_server": "http-sse",
                        "mcp_tool_name": "search",
                        "internal_tool_name": "mcp.http_sse.search",
                        "server_transport": "http",
                        "url": f"{base_url}/mcp-sse",
                        "timeout_seconds": 3,
                        "max_retries": 0,
                    }
                ]
            )
            tool = adapter.generate_tool_registry_entry("http-sse", "search")
            result = tool.invoke(ToolCall(name="mcp.http_sse.search", arguments={"query": "sse-body"}))

        self.assertTrue(result.success, result.message)
        self.assertEqual(result.data["mcp"]["result"]["structuredContent"]["echo"], {"query": "sse-body"})
        self.assertEqual(result.metadata["transport_stats"]["attempts"], 1)

    def test_streamable_http_timeout_fails_fast_and_is_audited(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_StreamableMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "slow-http",
                        "transport": "http",
                        "url": f"{base_url}/slow",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "timeout_seconds": 1,
                        "max_retries": 0,
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.slow.search", "read_only": True}],
                    }
                )
                registry = build_default_tool_registry()
                started = time.monotonic()
                result = registry.invoke(ToolCall(name="mcp.slow.search", arguments={"query": "timeout"}))
                elapsed = time.monotonic() - started
                snapshot = build_mcp_management_snapshot()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "mcp_http_call_failed")
        self.assertLess(elapsed, 2.5)
        self.assertIn("tool_call_failed", [item["action"] for item in snapshot["audit_log"]])

    def test_remote_mcp_heartbeat_removes_tools_after_three_failures_and_recovers(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_StreamableMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "recoverable",
                        "transport": "http",
                        "url": "http://127.0.0.1:9/mcp",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "timeout_seconds": 1,
                        "max_retries": 0,
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.recoverable.search", "read_only": True}],
                    }
                )
                for _ in range(3):
                    result = probe_mcp_servers(["recoverable"])
                    self.assertFalse(result[0]["ok"])

                degraded = build_mcp_management_snapshot()
                server = degraded["servers"][0]
                self.assertEqual(server["health"]["runtime"]["consecutive_failures"], 3)
                self.assertEqual(server["health"]["runtime"]["status"], "unavailable")
                self.assertIn("runtime_unavailable", server["health"]["issues"])
                self.assertEqual(degraded["tool_mappings"], [])
                self.assertNotIn("mcp.recoverable.search", {spec.name for spec in build_default_tool_registry().list_specs()})

                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "recoverable",
                        "transport": "http",
                        "url": f"{base_url}/mcp",
                    }
                )
                recovered = probe_mcp_servers(["recoverable"])
                self.assertTrue(recovered[0]["ok"])
                snapshot = build_mcp_management_snapshot()

        server = snapshot["servers"][0]
        self.assertEqual(server["health"]["runtime"]["status"], "available")
        self.assertEqual(server["health"]["runtime"]["consecutive_failures"], 0)
        self.assertEqual(snapshot["ready_mapping_count"], 1)
        self.assertIn("server_runtime_unavailable", [event["action"] for event in snapshot["audit_log"]])
        self.assertIn("server_runtime_recovered", [event["action"] for event in snapshot["audit_log"]])

    def test_remote_mcp_gateway_safety_blocks_transport_before_network_request(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_StreamableMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            safety_path = str(Path(tmp) / "safety.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path, "SPIRITKIN_SAFETY_STATE_PATH": safety_path}, clear=False):
                build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "guarded",
                        "transport": "http",
                        "url": f"{base_url}/mcp",
                        "enabled": True,
                        "review_state": "approved",
                        "owner_agent_ids": ["programming"],
                        "tools": [{"mcp_tool_name": "search", "internal_tool_name": "mcp.guarded.search", "read_only": True}],
                    }
                )
                set_safety_stop(mode="hard_stop", reason="unit test", actor="unit-test")
                result = build_default_tool_registry().invoke(ToolCall(name="mcp.guarded.search", arguments={"query": "blocked"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "safety_hard_stop_active")
        self.assertEqual(_StreamableMCPHandler.requests_seen, [])

    def test_mcp_401_names_environment_mapping_without_disclosing_secret(self):
        with TemporaryDirectory() as tmp, _serve_mcp(_UnauthorizedMCPHandler) as base_url:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path, "MCP_TEST_TOKEN": "top-secret-token"}, clear=False):
                adapter = build_mcp_adapter_from_config(
                    [
                        {
                            "mcp_server": "protected",
                            "mcp_tool_name": "search",
                            "internal_tool_name": "mcp.protected.search",
                            "server_transport": "http",
                            "url": f"{base_url}/mcp",
                            "header_env": {"Authorization": "MCP_TEST_TOKEN"},
                            "max_retries": 0,
                        }
                    ]
                )
                tool = adapter.generate_tool_registry_entry("protected", "search")
                result = tool.invoke(ToolCall(name="mcp.protected.search", arguments={"query": "auth"}))

        self.assertFalse(result.success)
        self.assertIn("401 Unauthorized", result.message)
        self.assertIn("MCP_TEST_TOKEN", result.message)
        self.assertNotIn("top-secret-token", result.message)

    def test_legacy_env_refs_can_configure_remote_header_without_secret_storage(self):
        with _serve_mcp(_StreamableMCPHandler) as base_url:
            with patch.dict(os.environ, {"MCP_TEST_TOKEN": "top-secret-token"}, clear=False):
                adapter = build_mcp_adapter_from_config(
                    [
                        {
                            "mcp_server": "legacy-auth",
                            "mcp_tool_name": "search",
                            "internal_tool_name": "mcp.legacy_auth.search",
                            "server_transport": "http",
                            "url": f"{base_url}/mcp",
                            "env_refs": ["Authorization <- MCP_TEST_TOKEN"],
                            "max_retries": 1,
                        }
                    ]
                )
                mapping = adapter.list_mappings()[0]
                self.assertEqual(mapping.header_env, {"Authorization": "MCP_TEST_TOKEN"})
                self.assertNotIn("top-secret-token", repr(mapping))
                tool = adapter.generate_tool_registry_entry("legacy-auth", "search")
                result = tool.invoke(ToolCall(name="mcp.legacy_auth.search", arguments={"query": "auth"}))

        self.assertTrue(result.success, result.message)
        self.assertTrue(any(item["headers"].get("Authorization") == "top-secret-token" for item in _StreamableMCPHandler.requests_seen))

    def test_sensitive_remote_headers_require_environment_mapping(self):
        with TemporaryDirectory() as tmp:
            registry_path = str(Path(tmp) / "mcp" / "registry.json")
            with patch.dict(os.environ, {"SPIRITKIN_MCP_REGISTRY_PATH": registry_path}, clear=False):
                status, payload = build_desktop_mcp_management_update_response(
                    {
                        "action": "save_server",
                        "server_id": "secret-header",
                        "transport": "http",
                        "url": "http://127.0.0.1:8790/mcp",
                        "headers": {"Authorization": "Bearer should-not-persist"},
                    }
                )

        self.assertEqual(status, 400)
        self.assertIn("header_env", payload["detail"])


if __name__ == "__main__":
    unittest.main()
