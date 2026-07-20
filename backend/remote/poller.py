from __future__ import annotations

import threading
from collections.abc import Callable

from backend.executors import NodeRegistry


class RemoteHeartbeatPoller:
    """Background poller that refreshes registered remote node heartbeats."""

    def __init__(self, node_registry: NodeRegistry, *, interval_seconds: float = 10.0, ttl_seconds: float = 30.0, on_result: Callable[[dict[str, object]], None] | None = None):
        self.node_registry = node_registry
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.on_result = on_result
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> dict[str, object]:
        result = self.node_registry.refresh_all_from_clients(ttl_seconds=self.ttl_seconds)
        if self.on_result is not None:
            self.on_result(result)
        return result

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="spiritkin-remote-heartbeat", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.interval_seconds)