from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from backend.security.safety_control import evaluate_execution_safety, evaluate_gateway_request_safety
from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec

_HEADER_ENV_REF = re.compile(r"^\s*([^<>=:\s]+)\s*(?:<-|=|:)\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


def _header_env_from_refs(value: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    values = [value] if isinstance(value, str) else list(value or [])
    for item in values:
        match = _HEADER_ENV_REF.match(str(item))
        if match:
            result[match.group(1).strip()] = match.group(2).strip()
    return result


def _safe_tool_segment(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char == "_" else "_" for char in str(value or "").strip().lower())
    return "_".join(part for part in normalized.split("_") if part) or "unknown"


def _url_origin(value: str) -> tuple[str, str | None, int | None]:
    parsed = urlparse(value)
    default_port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
    return parsed.scheme, parsed.hostname, parsed.port or default_port


@dataclass
class MCPToolMapping:
    mcp_server: str
    mcp_tool_name: str
    internal_tool_name: str
    target: str = "mcp"
    operation: str = ""
    risk_level: str = "medium"
    read_only: bool = False
    confirmation_required: bool = False
    schema_override: dict[str, Any] | None = None
    transport: str = "stdio"
    command: str = ""
    args: list[str] | None = None
    env_refs: list[str] | None = None
    working_directory: str = ""
    url: str = ""
    headers: dict[str, str] | None = None
    header_env: dict[str, str] | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 2


class MCPStdioClient:
    def __init__(self, command: str, args: list[str] | None = None, *, env_refs: list[str] | None = None, working_directory: str = "", timeout: float = 30.0):
        self.command = str(command or "").strip()
        self.args = list(args or [])
        self.env_refs = list(env_refs or [])
        self.working_directory = str(working_directory or "").strip()
        self.timeout = float(timeout)
        self._next_id = 1

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self._open_session()
        try:
            initialize, tools = self._initialize_session(session)
            result = self._request(session["process"], session["stdout_lines"], "tools/call", {"name": tool_name, "arguments": dict(arguments or {})})
            return {
                "initialize": initialize,
                "tools": tools,
                "result": result,
            }
        finally:
            _terminate_process(session["process"])

    def list_tools(self) -> dict[str, Any]:
        session = self._open_session()
        try:
            initialize, tools = self._initialize_session(session)
            return {"initialize": initialize, "tools": tools}
        finally:
            _terminate_process(session["process"])

    def _open_session(self) -> dict[str, Any]:
        if not self.command:
            raise RuntimeError("MCP stdio server command is not configured")
        argv = [*shlex.split(self.command, posix=os.name != "nt"), *self.args]
        if not argv:
            raise RuntimeError("MCP stdio server command is empty")
        env = self._build_env()
        cwd = self._resolve_cwd()
        process = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_hidden_subprocess_kwargs(),
        )
        return {"process": process, "stdout_lines": _start_stdout_reader(process)}

    def _initialize_session(self, session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        process = session["process"]
        stdout_lines = session["stdout_lines"]
        initialize = self._request(process, stdout_lines, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "SpiritKin", "version": "0.1"}})
        self._notify(process, "notifications/initialized", {})
        tools = self._request(process, stdout_lines, "tools/list", {})
        return initialize, tools

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        allowed = {str(name).strip() for name in self.env_refs if str(name).strip()}
        if not allowed:
            return {key: value for key, value in env.items() if key in {"PATH", "Path", "PYTHONPATH", "HOME", "USERPROFILE", "TEMP", "TMP"} or key.upper().startswith("SYSTEM")}
        return {key: value for key, value in env.items() if key in allowed or key.upper().startswith("SYSTEM") or key in {"PATH", "Path", "PYTHONPATH", "HOME", "USERPROFILE", "TEMP", "TMP"}}

    def _resolve_cwd(self) -> Path | None:
        if not self.working_directory:
            return None
        cwd = Path(self.working_directory).expanduser()
        if not cwd.is_absolute():
            cwd = Path(os.getcwd()) / cwd
        cwd = cwd.resolve()
        if not cwd.exists() or not cwd.is_dir():
            raise RuntimeError(f"MCP working_directory does not exist: {cwd}")
        return cwd

    def _request(self, process: subprocess.Popen, stdout_lines: queue.Queue[str], method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._read_response(process, stdout_lines, request_id)

    def _notify(self, process: subprocess.Popen, method: str, params: dict[str, Any]) -> None:
        self._send(process, {"jsonrpc": "2.0", "method": method, "params": params})

    @staticmethod
    def _send(process: subprocess.Popen, payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("MCP stdio server stdin is unavailable")
        process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _read_response(self, process: subprocess.Popen, stdout_lines: queue.Queue[str], request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                line = stdout_lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                line = ""
            if not line:
                if process.poll() is not None:
                    stderr = _read_process_stderr(process)
                    raise RuntimeError(f"MCP stdio server exited before response id={request_id}: {stderr}".strip())
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("id") != request_id:
                continue
            if payload.get("error"):
                raise RuntimeError(f"MCP error for id={request_id}: {payload['error']}")
            result = payload.get("result")
            return dict(result) if isinstance(result, dict) else {"value": result}
        raise TimeoutError(f"MCP stdio server timed out waiting for response id={request_id}")


def _hidden_subprocess_kwargs() -> dict[str, object]:
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _read_process_stderr(process: subprocess.Popen) -> str:
    if process.stderr is None:
        return ""
    try:
        return (process.stderr.read() or "").strip()
    except Exception:
        return ""


def _start_stdout_reader(process: subprocess.Popen) -> queue.Queue[str]:
    if process.stdout is None:
        raise RuntimeError("MCP stdio server stdout is unavailable")
    lines: queue.Queue[str] = queue.Queue()

    def read_lines() -> None:
        try:
            for line in process.stdout:
                lines.put(line)
        except Exception:
            lines.put("")

    thread = threading.Thread(target=read_lines, daemon=True)
    thread.start()
    return lines


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


class MCPRemoteError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def _response_payload(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        try:
            payload = json.loads(response.content.decode("utf-8", errors="replace") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
    return dict(payload) if isinstance(payload, dict) else {"value": payload}


def _iter_sse_events(response: requests.Response):
    event_name = "message"
    event_id = ""
    retry_ms: int | None = None
    data_lines: list[str] = []
    for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
        line = str(raw_line or "")
        if line == "":
            if data_lines:
                yield {"event": event_name, "id": event_id, "retry": retry_ms, "data": "\n".join(data_lines)}
            event_name = "message"
            event_id = ""
            retry_ms = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "id":
            event_id = value
        elif field == "retry":
            try:
                retry_ms = int(value)
            except ValueError:
                retry_ms = None
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield {"event": event_name, "id": event_id, "retry": retry_ms, "data": "\n".join(data_lines)}


def _decode_sse_json(event: dict[str, Any]) -> dict[str, Any] | None:
    data = str(event.get("data") or "").strip()
    if not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return dict(payload) if isinstance(payload, dict) else {"value": payload}


class _MCPRemoteClient:
    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str] | None = None,
        header_env: dict[str, str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        self.url = str(url or "").strip()
        self.headers = {str(key): str(value) for key, value in (headers or {}).items() if str(key).strip() and str(value).strip()}
        self.header_env = {str(key): str(value) for key, value in (header_env or {}).items() if str(key).strip() and str(value).strip()}
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(0, min(5, int(max_retries)))
        self.session_id = ""
        self.protocol_version = "2025-11-25"
        self.attempts = 0
        self.reconnects = 0
        self._next_id = 1
        self._session = requests.Session()

    def _request_headers(self, *, initialize: bool = False, accept: str = "application/json, text/event-stream") -> dict[str, str]:
        headers = dict(self.headers)
        for header, env_name in self.header_env.items():
            value = os.getenv(env_name, "")
            if value:
                headers[header] = value
        headers.setdefault("Accept", accept)
        headers.setdefault("Content-Type", "application/json")
        if self.session_id and not initialize:
            headers["MCP-Session-Id"] = self.session_id
        if not initialize and self.protocol_version:
            headers.setdefault("MCP-Protocol-Version", self.protocol_version)
        return headers

    def _http_status_error(self, response: requests.Response, *, context: str, retryable: bool = False) -> MCPRemoteError:
        if response.status_code == 401:
            missing = sorted({env_name for env_name in self.header_env.values() if not os.getenv(env_name, "")})
            configured = ", ".join(f"{header} <- {env_name}" for header, env_name in sorted(self.header_env.items()))
            if missing:
                detail = f"Missing environment variables: {', '.join(missing)}."
            elif configured:
                detail = f"Configured environment-backed headers: {configured}. Verify the token scope and format."
            else:
                detail = "Configure authentication with header_env and an environment variable (for example Authorization <- MCP_TOKEN)."
            return MCPRemoteError(
                f"{context} returned 401 Unauthorized. {detail} Secret values were not logged.",
                retryable=False,
                status_code=401,
            )
        body = response.text[:300]
        return MCPRemoteError(
            f"{context} returned {response.status_code}{f': {body}' if body else ''}",
            retryable=retryable,
            status_code=response.status_code,
        )

    def _new_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}

    @staticmethod
    def _result(payload: dict[str, Any], request_id: int) -> dict[str, Any]:
        if payload.get("id") not in {request_id, str(request_id)}:
            raise MCPRemoteError(f"MCP response id mismatch: expected {request_id}, got {payload.get('id')}")
        if payload.get("error"):
            raise MCPRemoteError(f"MCP error for id={request_id}: {payload['error']}")
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else {"value": result}

    def _stats(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "reconnects": self.reconnects,
            "session_established": bool(self.session_id),
            "session_id_hash": sha256(self.session_id.encode("utf-8")).hexdigest()[:12] if self.session_id else "",
            "protocol_version": self.protocol_version,
        }

    def _retry_loop(self, operation):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.attempts += 1
            try:
                return operation()
            except MCPRemoteError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                self.reconnects += 1
                self._reset_session()
                time.sleep(min(1.0, 0.15 * (2**attempt)))
            except (requests.exceptions.RequestException, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise MCPRemoteError(f"MCP remote request failed: {exc}", retryable=False) from exc
                self.reconnects += 1
                self._reset_session()
                time.sleep(min(1.0, 0.15 * (2**attempt)))
        raise MCPRemoteError(f"MCP remote request failed: {last_error}")

    def _reset_session(self) -> None:
        self.session_id = ""
        self._next_id = 1
        try:
            self._session.close()
        except Exception:
            pass
        self._session = requests.Session()


class MCPStreamableHTTPClient(_MCPRemoteClient):
    """MCP 2025 Streamable HTTP client with JSON/SSE response handling."""

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        def operation():
            initialize, tools = self._initialize_session()
            result = self._post_jsonrpc(self._new_request("tools/call", {"name": tool_name, "arguments": dict(arguments or {})}))
            return {"initialize": initialize, "tools": tools, "result": result, "transport": self._stats()}

        return self._retry_loop(operation)

    def list_tools(self) -> dict[str, Any]:
        def operation():
            initialize, tools = self._initialize_session()
            return {"initialize": initialize, "tools": tools, "transport": self._stats()}

        return self._retry_loop(operation)

    def _initialize_session(self) -> tuple[dict[str, Any], dict[str, Any]]:
        initialize_request = self._new_request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "SpiritKin", "version": "0.1"},
            },
        )
        initialize = self._post_jsonrpc(initialize_request, initialize=True)
        self.protocol_version = str(initialize.get("protocolVersion") or self.protocol_version)
        self._post_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        tools = self._post_jsonrpc(self._new_request("tools/list", {}))
        return initialize, tools

    def _post_jsonrpc(self, payload: dict[str, Any], *, initialize: bool = False) -> dict[str, Any]:
        request_id = payload.get("id")
        try:
            response = self._session.post(
                self.url,
                headers=self._request_headers(initialize=initialize),
                data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.exceptions.Timeout as exc:
            raise MCPRemoteError(f"MCP HTTP timed out: {self.url}", retryable=True) from exc
        except requests.exceptions.RequestException as exc:
            raise MCPRemoteError(f"MCP HTTP request failed: {exc}", retryable=True) from exc
        try:
            if response.status_code == 404 and self.session_id:
                raise MCPRemoteError("MCP session expired (HTTP 404)", retryable=True, status_code=404)
            if response.status_code in {408, 429} or response.status_code >= 500:
                raise self._http_status_error(response, context="MCP HTTP server", retryable=True)
            if response.status_code >= 400:
                raise self._http_status_error(response, context="MCP HTTP server")
            response_session = str(response.headers.get("MCP-Session-Id") or "").strip()
            if response_session:
                self.session_id = response_session
            if response.status_code == 202:
                return {"accepted": True}
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in content_type:
                result = self._read_sse_response(response, int(request_id) if request_id is not None else None)
                if result is None:
                    raise MCPRemoteError("MCP HTTP SSE stream closed before response", retryable=True)
                return result
            return self._result(_response_payload(response), int(request_id)) if request_id is not None else _response_payload(response)
        finally:
            response.close()

    def _read_sse_response(self, response: requests.Response, request_id: int | None) -> dict[str, Any] | None:
        last_event_id = ""
        retry_ms = 0
        for event in _iter_sse_events(response):
            last_event_id = str(event.get("id") or last_event_id)
            retry_ms = int(event.get("retry") or retry_ms or 0)
            payload = _decode_sse_json(event)
            if payload is not None and request_id is not None and payload.get("id") in {request_id, str(request_id)}:
                return self._result(payload, request_id)
        if last_event_id:
            self.reconnects += 1
            if retry_ms > 0:
                time.sleep(min(self.timeout, retry_ms / 1000.0))
            return self._resume_sse(last_event_id, request_id)
        return None

    def _resume_sse(self, last_event_id: str, request_id: int | None) -> dict[str, Any] | None:
        headers = self._request_headers(initialize=False, accept="text/event-stream")
        headers["Last-Event-ID"] = last_event_id
        try:
            response = self._session.get(self.url, headers=headers, timeout=self.timeout, allow_redirects=False, stream=True)
        except requests.exceptions.RequestException as exc:
            raise MCPRemoteError(f"MCP HTTP SSE reconnect failed: {exc}", retryable=True) from exc
        try:
            if response.status_code >= 400:
                raise self._http_status_error(response, context="MCP HTTP SSE reconnect", retryable=response.status_code >= 500)
            for event in _iter_sse_events(response):
                payload = _decode_sse_json(event)
                if payload is not None and request_id is not None and payload.get("id") in {request_id, str(request_id)}:
                    return self._result(payload, request_id)
        finally:
            response.close()
        return None


class MCPSSERemoteClient(_MCPRemoteClient):
    """Legacy HTTP+SSE MCP client (endpoint event + message POSTs)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._sse_response: requests.Response | None = None
        self._message_url = ""

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        def operation():
            try:
                initialize, tools = self._initialize_session()
                result = self._post_message(self._new_request("tools/call", {"name": tool_name, "arguments": dict(arguments or {})}))
                return {"initialize": initialize, "tools": tools, "result": result, "transport": self._stats()}
            finally:
                self._close_stream()

        return self._retry_loop(operation)

    def list_tools(self) -> dict[str, Any]:
        def operation():
            try:
                initialize, tools = self._initialize_session()
                return {"initialize": initialize, "tools": tools, "transport": self._stats()}
            finally:
                self._close_stream()

        return self._retry_loop(operation)

    def _initialize_session(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self._open_stream()
        initialize = self._post_message(
            self._new_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "SpiritKin", "version": "0.1"},
                },
            )
        )
        self.protocol_version = str(initialize.get("protocolVersion") or "2024-11-05")
        self._post_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        tools = self._post_message(self._new_request("tools/list", {}))
        return initialize, tools

    def _open_stream(self) -> None:
        self._close_stream()
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._events = events
        try:
            response = self._session.get(
                self.url,
                headers=self._request_headers(initialize=True, accept="text/event-stream"),
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.exceptions.RequestException as exc:
            raise MCPRemoteError(f"MCP SSE connection failed: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            try:
                raise self._http_status_error(response, context="MCP SSE connection", retryable=response.status_code >= 500)
            finally:
                response.close()
        self._sse_response = response
        reader = threading.Thread(target=self._read_stream, args=(response, events), daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                event = self._events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if event.get("closed"):
                raise MCPRemoteError("MCP SSE stream closed before endpoint event", retryable=True)
            if event.get("event") != "endpoint":
                continue
            endpoint = str(event.get("data") or "").strip()
            if not endpoint:
                raise MCPRemoteError("MCP SSE endpoint event is empty")
            message_url = urljoin(self.url, endpoint)
            if _url_origin(message_url) != _url_origin(self.url):
                raise MCPRemoteError("MCP SSE endpoint must use the registered server origin")
            self._message_url = message_url
            return
        raise MCPRemoteError("MCP SSE timed out waiting for endpoint event", retryable=True)

    def _read_stream(self, response: requests.Response, events: queue.Queue[dict[str, Any]]) -> None:
        try:
            for event in _iter_sse_events(response):
                events.put(event)
        except Exception as exc:
            events.put({"closed": True, "error": str(exc)})
        finally:
            events.put({"closed": True})

    def _post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._message_url:
            raise MCPRemoteError("MCP SSE message endpoint is not configured")
        request_id = payload.get("id")
        try:
            response = self._session.post(
                self._message_url,
                headers=self._request_headers(initialize=False),
                data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as exc:
            raise MCPRemoteError(f"MCP SSE POST timed out: {self._message_url}", retryable=True) from exc
        except requests.exceptions.RequestException as exc:
            raise MCPRemoteError(f"MCP SSE POST failed: {exc}", retryable=True) from exc
        try:
            if response.status_code >= 400:
                raise self._http_status_error(response, context="MCP SSE POST", retryable=response.status_code >= 500)
            if request_id is None:
                return {"accepted": True}
            if response.content:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if "json" in content_type:
                    return self._result(_response_payload(response), int(request_id))
            return self._wait_for_response(int(request_id))
        finally:
            response.close()

    def _wait_for_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                event = self._events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if event.get("closed"):
                raise MCPRemoteError("MCP SSE stream closed before response", retryable=True)
            payload = _decode_sse_json(event)
            if payload is None:
                continue
            if payload.get("id") in {request_id, str(request_id)}:
                return self._result(payload, request_id)
        raise MCPRemoteError(f"MCP SSE timed out waiting for response id={request_id}", retryable=True)

    def _close_stream(self) -> None:
        if self._sse_response is not None:
            try:
                self._sse_response.close()
            except Exception:
                pass
        self._sse_response = None
        self._message_url = ""
        self._events = queue.Queue()


class MCPAdapter:
    def __init__(self):
        self._mappings: dict[str, MCPToolMapping] = {}

    def register_mapping(self, mapping: MCPToolMapping) -> None:
        key = f"{mapping.mcp_server}:{mapping.mcp_tool_name}"
        self._mappings[key] = mapping

    def list_mappings(self) -> list[MCPToolMapping]:
        return list(self._mappings.values())

    def resolve(self, mcp_server: str, mcp_tool_name: str) -> MCPToolMapping | None:
        return self._mappings.get(f"{mcp_server}:{mcp_tool_name}")

    def to_internal_tool_spec(self, mapping: MCPToolMapping) -> ToolSpec:
        return ToolSpec(
            name=mapping.internal_tool_name,
            description=f"MCP 工具: {mapping.mcp_server}/{mapping.mcp_tool_name}",
            target=mapping.target,
            operation=mapping.operation or mapping.mcp_tool_name,
            risk_level=mapping.risk_level,
            read_only=mapping.read_only,
            schema=mapping.schema_override or {},
        )

    @staticmethod
    def _remote_client(mapping: MCPToolMapping):
        common = {
            "url": mapping.url,
            "headers": mapping.headers or {},
            "header_env": mapping.header_env or {},
            "timeout": mapping.timeout_seconds,
            "max_retries": mapping.max_retries,
        }
        if mapping.transport == "http":
            return MCPStreamableHTTPClient(**common)
        if mapping.transport == "sse":
            return MCPSSERemoteClient(**common)
        raise MCPRemoteError(f"unsupported MCP remote transport: {mapping.transport}")

    @staticmethod
    def _record_audit(
        mapping: MCPToolMapping,
        action: str,
        *,
        success: bool,
        message: str,
        metadata: dict[str, Any] | None = None,
        track_health: bool = True,
    ) -> None:
        try:
            from backend.app.mcp_management import record_mcp_runtime_audit

            record_mcp_runtime_audit(
                mapping.mcp_server,
                action,
                success=success,
                message=message,
                metadata={"transport": mapping.transport, **dict(metadata or {})},
                track_health=track_health,
            )
        except Exception:
            pass

    def generate_tool_registry_entry(self, mcp_server: str, mcp_tool_name: str) -> BaseTool | None:
        mapping = self.resolve(mcp_server, mcp_tool_name)
        if mapping is None:
            return None

        spec = self.to_internal_tool_spec(mapping)

        class MCPProxyTool(BaseTool):
            def __init__(self, tool_spec: ToolSpec):
                self._spec = tool_spec

            @property
            def spec(self) -> ToolSpec:
                return self._spec

            def invoke(self, call: ToolCall) -> ToolResult:
                started_at = time.monotonic()
                try:
                    from backend.app.mcp_management import mcp_server_available

                    server_available = mcp_server_available(mapping.mcp_server)
                except Exception:
                    server_available = True
                if server_available is False:
                    MCPAdapter._record_audit(
                        mapping,
                        "tool_call_blocked",
                        success=False,
                        message="MCP server is unavailable and its tools are temporarily removed",
                        metadata={"tool_name": mapping.mcp_tool_name, "reason": "runtime_unavailable"},
                        track_health=False,
                    )
                    return ToolResult(
                        success=False,
                        message=f"MCP 服务暂不可用，等待健康检查恢复: {mapping.mcp_server}",
                        error_code="mcp_server_unavailable",
                        metadata={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "transport": mapping.transport},
                    )
                execution_safety = evaluate_execution_safety(
                    target=mapping.target,
                    operation=mapping.operation or mapping.mcp_tool_name,
                    actor=str((call.arguments or {}).get("actor") or ""),
                    read_only=mapping.read_only,
                    dry_run=bool((call.arguments or {}).get("dry_run")),
                )
                if not execution_safety.allowed:
                    MCPAdapter._record_audit(
                        mapping,
                        "tool_call_blocked",
                        success=False,
                        message=execution_safety.message,
                        metadata={"tool_name": mapping.mcp_tool_name, "reason": execution_safety.error_code},
                        track_health=False,
                    )
                    return ToolResult(
                        success=False,
                        message=execution_safety.message,
                        error_code=execution_safety.error_code,
                        metadata={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "safety": execution_safety.snapshot()},
                    )
                if mapping.transport in {"http", "sse"}:
                    gateway_safety = evaluate_gateway_request_safety(path=f"/mcp/{mapping.mcp_server}/{mapping.mcp_tool_name}", method="POST")
                    if not gateway_safety.allowed:
                        MCPAdapter._record_audit(
                            mapping,
                            "tool_call_blocked",
                            success=False,
                            message=gateway_safety.message,
                            metadata={"tool_name": mapping.mcp_tool_name, "reason": gateway_safety.error_code},
                            track_health=False,
                        )
                        return ToolResult(
                            success=False,
                            message=gateway_safety.message,
                            error_code=gateway_safety.error_code,
                            metadata={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "safety": gateway_safety.snapshot()},
                        )
                try:
                    if mapping.transport == "stdio":
                        response = MCPStdioClient(
                            mapping.command,
                            mapping.args or [],
                            env_refs=mapping.env_refs or [],
                            working_directory=mapping.working_directory,
                            timeout=mapping.timeout_seconds,
                        ).call_tool(mapping.mcp_tool_name, call.arguments)
                    else:
                        response = MCPAdapter._remote_client(mapping).call_tool(mapping.mcp_tool_name, call.arguments)
                except Exception as exc:
                    duration_ms = round((time.monotonic() - started_at) * 1000, 2)
                    MCPAdapter._record_audit(
                        mapping,
                        "tool_call_failed",
                        success=False,
                        message=f"MCP tool call failed: {mapping.mcp_tool_name}",
                        metadata={"tool_name": mapping.mcp_tool_name, "duration_ms": duration_ms, "error_type": type(exc).__name__},
                    )
                    return ToolResult(
                        success=False,
                        message=f"MCP 代理调用失败: {mapping.mcp_server}/{mapping.mcp_tool_name}: {exc}",
                        data={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "arguments": call.arguments},
                        error_code=f"mcp_{mapping.transport}_call_failed",
                        metadata={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "transport": mapping.transport, "duration_ms": duration_ms},
                    )
                duration_ms = round((time.monotonic() - started_at) * 1000, 2)
                transport_stats = dict(response.get("transport") or {}) if isinstance(response, dict) else {}
                MCPAdapter._record_audit(
                    mapping,
                    "tool_call_completed",
                    success=True,
                    message=f"MCP tool call completed: {mapping.mcp_tool_name}",
                    metadata={"tool_name": mapping.mcp_tool_name, "duration_ms": duration_ms, **transport_stats},
                )
                return ToolResult(
                    success=True,
                    message=f"MCP 代理调用完成: {mapping.mcp_server}/{mapping.mcp_tool_name}",
                    data={"mcp_server": mcp_server, "mcp_tool": mcp_tool_name, "arguments": call.arguments, "mcp": response},
                    metadata={
                        "mcp_server": mcp_server,
                        "mcp_tool": mcp_tool_name,
                        "transport": mapping.transport,
                        "pending_mcp_execution": False,
                        "duration_ms": duration_ms,
                        "transport_stats": transport_stats,
                    },
                )

        return MCPProxyTool(spec)

    def discover_tool_mappings(self) -> list[MCPToolMapping]:
        discovered: list[MCPToolMapping] = []
        seen_servers: set[str] = set()
        for mapping in self.list_mappings():
            if mapping.mcp_server in seen_servers:
                continue
            seen_servers.add(mapping.mcp_server)
            if mapping.transport == "stdio" and not mapping.command:
                continue
            if mapping.transport in {"sse", "http"} and not mapping.url:
                continue
            started_at = time.monotonic()
            try:
                if mapping.transport == "stdio":
                    listed = MCPStdioClient(
                        mapping.command,
                        mapping.args or [],
                        env_refs=mapping.env_refs or [],
                        working_directory=mapping.working_directory,
                        timeout=mapping.timeout_seconds,
                    ).list_tools()
                else:
                    listed = self._remote_client(mapping).list_tools()
            except Exception as exc:
                self._record_audit(
                    mapping,
                    "tool_discovery_failed",
                    success=False,
                    message="MCP tool discovery failed",
                    metadata={"duration_ms": round((time.monotonic() - started_at) * 1000, 2), "error_type": type(exc).__name__},
                )
                continue
            tools = listed.get("tools", {}).get("tools") if isinstance(listed.get("tools"), dict) else []
            if not isinstance(tools, list):
                continue
            self._record_audit(
                mapping,
                "tool_discovery_completed",
                success=True,
                message=f"Discovered {len(tools)} MCP tools",
                metadata={
                    "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                    "tool_count": len(tools),
                    **(dict(listed.get("transport") or {}) if isinstance(listed, dict) else {}),
                },
            )
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                if not name or self.resolve(mapping.mcp_server, name) is not None:
                    continue
                discovered.append(
                    MCPToolMapping(
                        mcp_server=mapping.mcp_server,
                        mcp_tool_name=name,
                        internal_tool_name=f"mcp.{_safe_tool_segment(mapping.mcp_server)}.{_safe_tool_segment(name)}",
                        target=mapping.target,
                        operation=name,
                        risk_level=mapping.risk_level,
                        read_only=mapping.read_only,
                        confirmation_required=mapping.confirmation_required,
                        schema_override=tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {},
                        transport=mapping.transport,
                        command=mapping.command,
                        args=list(mapping.args or []),
                        env_refs=list(mapping.env_refs or []),
                        working_directory=mapping.working_directory,
                        url=mapping.url,
                        headers=dict(mapping.headers or {}),
                        header_env=dict(mapping.header_env or {}),
                        timeout_seconds=mapping.timeout_seconds,
                        max_retries=mapping.max_retries,
                    )
                )
        return discovered

    def probe_servers(self) -> list[dict[str, Any]]:
        """Probe each configured server once and persist the runtime health result."""
        results: list[dict[str, Any]] = []
        seen_servers: set[str] = set()
        for mapping in self.list_mappings():
            if mapping.mcp_server in seen_servers:
                continue
            seen_servers.add(mapping.mcp_server)
            started_at = time.monotonic()
            try:
                if mapping.transport == "stdio":
                    listed = MCPStdioClient(
                        mapping.command,
                        mapping.args or [],
                        env_refs=mapping.env_refs or [],
                        working_directory=mapping.working_directory,
                        timeout=mapping.timeout_seconds,
                    ).list_tools()
                else:
                    listed = self._remote_client(mapping).list_tools()
            except Exception as exc:
                duration_ms = round((time.monotonic() - started_at) * 1000, 2)
                self._record_audit(
                    mapping,
                    "heartbeat_failed",
                    success=False,
                    message="MCP server heartbeat failed",
                    metadata={"duration_ms": duration_ms, "error_type": type(exc).__name__},
                )
                results.append({"server_id": mapping.mcp_server, "ok": False, "message": str(exc)[:300], "duration_ms": duration_ms})
                continue
            duration_ms = round((time.monotonic() - started_at) * 1000, 2)
            transport = dict(listed.get("transport") or {}) if isinstance(listed, dict) else {}
            self._record_audit(
                mapping,
                "heartbeat_completed",
                success=True,
                message="MCP server heartbeat completed",
                metadata={"duration_ms": duration_ms, **transport},
            )
            results.append({"server_id": mapping.mcp_server, "ok": True, "duration_ms": duration_ms, "transport": transport})
        return results

    def discover_stdio_tool_mappings(self) -> list[MCPToolMapping]:
        stdio_adapter = MCPAdapter()
        for mapping in self.list_mappings():
            if mapping.transport == "stdio":
                stdio_adapter.register_mapping(mapping)
        return stdio_adapter.discover_tool_mappings()


def build_mcp_adapter_from_config(config_entries: list[dict[str, Any]] | None = None) -> MCPAdapter:
    adapter = MCPAdapter()
    for entry in (config_entries or []):
        mapping = MCPToolMapping(
            mcp_server=str(entry.get("mcp_server") or ""),
            mcp_tool_name=str(entry.get("mcp_tool_name") or ""),
            internal_tool_name=str(entry.get("internal_tool_name") or f"mcp.{entry.get('mcp_server', 'unknown')}.{entry.get('mcp_tool_name', 'unknown')}"),
            target=str(entry.get("target") or "mcp"),
            operation=str(entry.get("operation") or entry.get("mcp_tool_name", "")),
            risk_level=str(entry.get("risk_level") or "medium"),
            read_only=bool(entry.get("read_only")),
            confirmation_required=bool(entry.get("confirmation_required")),
            schema_override=entry.get("schema_override"),
            transport=str(entry.get("server_transport") or entry.get("transport") or "stdio").strip().lower(),
            command=str(entry.get("command") or ""),
            args=[str(item) for item in (entry.get("args") or []) if str(item)],
            env_refs=[str(item) for item in (entry.get("env_refs") or []) if str(item)],
            working_directory=str(entry.get("working_directory") or ""),
            url=str(entry.get("url") or ""),
            headers={str(key): str(value) for key, value in dict(entry.get("headers") or {}).items()},
            header_env={
                **_header_env_from_refs(entry.get("env_refs") or []),
                **{str(key): str(value) for key, value in dict(entry.get("header_env") or {}).items()},
            },
            timeout_seconds=float(entry.get("timeout_seconds") or 30.0),
            max_retries=int(2 if entry.get("max_retries") is None else entry.get("max_retries")),
        )
        adapter.register_mapping(mapping)
    return adapter
