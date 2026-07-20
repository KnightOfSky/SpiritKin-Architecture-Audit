from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request


@dataclass
class OpenClawState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    gripper_opened: bool = True
    state: str = "idle"
    last_command: str = "boot"
    transport: str = "in_memory"

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "position": {"x": self.x, "y": self.y, "z": self.z},
            "gripper_opened": self.gripper_opened,
            "last_command": self.last_command,
            "transport": self.transport,
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, object] | None, *, default_transport: str = "in_memory") -> OpenClawState:
        snapshot = dict(snapshot or {})
        position = snapshot.get("position") if isinstance(snapshot.get("position"), dict) else {}
        return cls(
            x=float(position.get("x", 0.0)),
            y=float(position.get("y", 0.0)),
            z=float(position.get("z", 0.0)),
            gripper_opened=bool(snapshot.get("gripper_opened", True)),
            state=str(snapshot.get("state", "idle")),
            last_command=str(snapshot.get("last_command", "boot")),
            transport=str(snapshot.get("transport", default_transport) or default_transport),
        )


class JsonOpenClawStateStore:
    """最小本地状态存储：让软件态 OpenClaw 在重启后仍能恢复最近状态。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, object] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, snapshot: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


class InMemoryOpenClawClient:
    """纯软件 OpenClaw 客户端：用于本地联调、前端联调与自动化测试。"""

    def __init__(self, *, state_store: JsonOpenClawStateStore | None = None, state_path: str | Path | None = None):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._state_store = state_store or (JsonOpenClawStateStore(state_path) if state_path is not None else None)
        default_transport = "local_json" if self._state_store is not None else "in_memory"
        snapshot = self._state_store.load() if self._state_store is not None else None
        self._state = OpenClawState.from_snapshot(snapshot, default_transport=default_transport)

    def _record(self, method_name: str, **kwargs):
        self.calls.append((method_name, dict(kwargs)))

    def _snapshot(self) -> dict[str, object]:
        return self._state.snapshot()

    def _persist_state(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self._snapshot())

    def home(self):
        self._record("home")
        self._state.x = 0.0
        self._state.y = 0.0
        self._state.z = 0.0
        self._state.state = "idle"
        self._state.last_command = "home"
        self._persist_state()
        return self._snapshot()

    def move_to(self, **kwargs):
        self._record("move_to", **kwargs)
        self._state.x = float(kwargs["x"])
        self._state.y = float(kwargs["y"])
        self._state.z = float(kwargs["z"])
        self._state.state = "idle"
        self._state.last_command = "move_to"
        self._persist_state()
        return self._snapshot()

    def set_gripper(self, opened: bool):
        self._record("set_gripper", opened=opened)
        self._state.gripper_opened = bool(opened)
        self._state.state = "idle"
        self._state.last_command = "open_gripper" if opened else "close_gripper"
        self._persist_state()
        return self._snapshot()

    def open_gripper(self):
        return self.set_gripper(True)

    def close_gripper(self):
        return self.set_gripper(False)

    def get_status(self):
        self._record("get_status")
        return self._snapshot()


class HttpOpenClawClient:
    """HTTP OpenClaw client for real controller bridges.

    Expected controller API:
    - POST /home
    - POST /move_to with JSON body {x, y, z, speed?}
    - POST /gripper with JSON body {opened}
    - GET /status
    """

    def __init__(self, base_url: str, *, token: str = "", timeout: float = 5.0):
        self.base_url = str(base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise ValueError("OpenClaw HTTP base_url is required")
        self.token = str(token or "").strip()
        self.timeout = float(timeout)

    def home(self):
        return self._request("POST", "/home")

    def move_to(self, **kwargs):
        return self._request("POST", "/move_to", kwargs)

    def set_gripper(self, opened: bool):
        return self._request("POST", "/gripper", {"opened": bool(opened)})

    def open_gripper(self):
        return self.set_gripper(True)

    def close_gripper(self):
        return self.set_gripper(False)

    def get_status(self):
        return self._request("GET", "/status")

    def _request(self, method: str, path: str, payload: dict[str, object] | None = None):
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except error.URLError as exc:
            raise RuntimeError(f"OpenClaw HTTP transport unavailable: {exc}") from exc
        if not raw:
            return {"ok": True, "transport": "http", "endpoint": path}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenClaw HTTP transport returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("OpenClaw HTTP transport returned non-object JSON")
        data.setdefault("transport", "http")
        return data


def create_openclaw_client_from_env(environ: dict[str, str] | None = None):
    env = environ if environ is not None else os.environ
    base_url = str(env.get("SPIRITKIN_OPENCLAW_HTTP_BASE_URL") or "").strip()
    if not base_url:
        return None
    timeout_raw = str(env.get("SPIRITKIN_OPENCLAW_HTTP_TIMEOUT") or "5").strip()
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 5.0
    return HttpOpenClawClient(
        base_url=base_url,
        token=str(env.get("SPIRITKIN_OPENCLAW_HTTP_TOKEN") or "").strip(),
        timeout=timeout,
    )


class OpenClawArm:
    """OpenClaw 机械臂适配器：通过注入客户端避免在核心层绑定具体 SDK。"""

    name = "openclaw"

    def __init__(self, client):
        self._client = client

    def _invoke(self, method_name: str, **kwargs):
        method = getattr(self._client, method_name, None)
        if method is None:
            raise RuntimeError(f"OpenClaw 客户端缺少方法: {method_name}")
        return method(**kwargs) if kwargs else method()

    def home(self):
        return self._invoke("home")

    def move_to(self, *, x: float, y: float, z: float, speed: float | None = None):
        payload = {"x": x, "y": y, "z": z}
        if speed is not None:
            payload["speed"] = speed
        return self._invoke("move_to", **payload)

    def set_gripper(self, opened: bool):
        if hasattr(self._client, "set_gripper"):
            return self._invoke("set_gripper", opened=opened)
        method_name = "open_gripper" if opened else "close_gripper"
        return self._invoke(method_name)

    def get_status(self):
        return self._invoke("get_status")


def create_openclaw_arm(*, client=None, client_factory=None) -> OpenClawArm:
    if client is not None:
        return OpenClawArm(client)
    if client_factory is None:
        raise RuntimeError("请传入 client 或 client_factory 来创建 OpenClawArm")
    return OpenClawArm(client_factory())
