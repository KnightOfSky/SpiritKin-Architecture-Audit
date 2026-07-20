from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CapabilityToken:
    token_id: str
    actor: str
    capabilities: tuple[str, ...]
    expires_at: float | None = None
    granted_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.time()) >= self.expires_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "actor": self.actor,
            "capabilities": list(self.capabilities),
            "expires_at": self.expires_at,
            "granted_at": self.granted_at,
            "metadata": self.metadata,
        }


class CapabilityRegistry:
    def __init__(self):
        self._tokens: dict[str, CapabilityToken] = {}

    def grant(self, token: CapabilityToken) -> None:
        self._tokens[token.token_id] = token

    def revoke(self, token_id: str) -> bool:
        if token_id in self._tokens:
            del self._tokens[token_id]
            return True
        return False

    def check(self, token_id: str, target: str, operation: str) -> bool:
        token = self._tokens.get(token_id)
        if token is None:
            return False
        if token.is_expired():
            return False
        cap = f"{target}.{operation}"
        return "*" in token.capabilities or any(fnmatch.fnmatch(cap, pattern) for pattern in token.capabilities)

    def snapshot(self) -> dict[str, Any]:
        tokens = [token.snapshot() for token in self._tokens.values()]
        return {
            "total": len(tokens),
            "active": sum(1 for token in self._tokens.values() if not token.is_expired()),
            "expired": sum(1 for token in self._tokens.values() if token.is_expired()),
            "tokens": tokens,
        }

    def list_expired(self, now: float | None = None) -> list[CapabilityToken]:
        current = now or time.time()
        return [t for t in self._tokens.values() if t.is_expired(current)]

    def cleanup_expired(self) -> int:
        expired = self.list_expired()
        for t in expired:
            del self._tokens[t.token_id]
        return len(expired)


class JsonlCapabilityStore(CapabilityRegistry):
    def __init__(self, path: str | Path):
        super().__init__()
        self._path = Path(path).resolve()
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = CapabilityToken(
                        token_id=str(data.get("token_id") or ""),
                        actor=str(data.get("actor") or ""),
                        capabilities=tuple(data.get("capabilities") or []),
                        expires_at=data.get("expires_at"),
                        granted_at=float(data.get("granted_at") or time.time()),
                        metadata=dict(data.get("metadata") or {}),
                    )
                    if token.token_id:
                        self._tokens[token.token_id] = token
                except (json.JSONDecodeError, TypeError):
                    continue
        except (OSError, PermissionError):
            pass

    def grant(self, token: CapabilityToken) -> None:
        super().grant(token)
        self._append(token)

    def revoke(self, token_id: str) -> bool:
        removed = super().revoke(token_id)
        if removed:
            self._rewrite()
        return removed

    def _append(self, token: CapabilityToken) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(token.snapshot(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _rewrite(self) -> None:
        try:
            lines = [json.dumps(t.snapshot(), ensure_ascii=False) + "\n" for t in self._tokens.values()]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            pass


def build_capability_registry(path: str | Path | None = None) -> CapabilityRegistry:
    if not path:
        return CapabilityRegistry()
    return JsonlCapabilityStore(path)
