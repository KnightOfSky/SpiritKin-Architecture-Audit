"""Latest-wins request coordination extracted from AgentCluster (cluster W).

Tracks the most recent request id per client/session scope under a lock so that
superseded ("stale") requests can be detected and short-circuited. Pure
coordination state with no dependency on AgentCluster internals.
"""

from __future__ import annotations

from threading import Lock


class RequestCoordinator:
    def __init__(self) -> None:
        self._lock = Lock()
        self._latest_by_scope: dict[str, str] = {}

    @staticmethod
    def request_scope(metadata: dict[str, object]) -> str:
        client_id = str(metadata.get("client_id") or metadata.get("frontend") or "default").strip() or "default"
        session_id = str(metadata.get("session_id") or "").strip() or "default"
        return f"{client_id}:{session_id}"

    @staticmethod
    def request_id(metadata: dict[str, object]) -> str:
        return str(metadata.get("request_id") or "").strip()

    @staticmethod
    def latest_wins_enabled(metadata: dict[str, object]) -> bool:
        return str(metadata.get("interrupt_mode") or "").strip().lower() == "latest_wins" or bool(metadata.get("supersedes_previous"))

    def register(self, metadata: dict[str, object]) -> None:
        request_id = self.request_id(metadata)
        if not request_id or not self.latest_wins_enabled(metadata):
            return
        with self._lock:
            self._latest_by_scope[self.request_scope(metadata)] = request_id

    def is_stale(self, metadata: dict[str, object]) -> bool:
        request_id = self.request_id(metadata)
        if not request_id or not self.latest_wins_enabled(metadata):
            return False
        with self._lock:
            return self._latest_by_scope.get(self.request_scope(metadata)) != request_id
