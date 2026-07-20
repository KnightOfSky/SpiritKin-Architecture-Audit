from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTEXT_STORE_SCHEMA_VERSION = "spiritkin.context_store.v1"
DEFAULT_CONTEXT_STORE_PATH = "state/context/context_patches.jsonl"


@dataclass(frozen=True)
class ContextPatch:
    context_id: str
    patch_type: str
    actor: str
    path: str
    value: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    patch_id: str = field(default_factory=lambda: f"ctxpatch-{uuid.uuid4().hex}")
    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_STORE_SCHEMA_VERSION,
            "patch_id": self.patch_id,
            "context_id": self.context_id,
            "patch_type": self.patch_type,
            "actor": self.actor,
            "path": normalize_context_path(self.path),
            "value": self.value,
            "metadata": dict(self.metadata or {}),
            "created_at": self.created_at,
        }


def context_patch_from_snapshot(snapshot: dict[str, Any]) -> ContextPatch | None:
    if not isinstance(snapshot, dict):
        return None
    return ContextPatch(
        context_id=str(snapshot.get("context_id") or ""),
        patch_type=str(snapshot.get("patch_type") or "set"),
        actor=str(snapshot.get("actor") or ""),
        path=normalize_context_path(str(snapshot.get("path") or "")),
        value=snapshot.get("value"),
        metadata=dict(snapshot.get("metadata") or {}) if isinstance(snapshot.get("metadata"), dict) else {},
        patch_id=str(snapshot.get("patch_id") or f"ctxpatch-{uuid.uuid4().hex}"),
        created_at=_float_or_now(snapshot.get("created_at")),
    )


class AppendOnlyContextStore:
    """Small append-only Context Kernel seed used before full state enforcement."""

    def __init__(self, patches: list[ContextPatch] | None = None):
        self._patches: list[ContextPatch] = list(patches or [])

    def append_patch(
        self,
        *,
        context_id: str,
        patch_type: str,
        actor: str,
        path: str,
        value: Any,
        metadata: dict[str, Any] | None = None,
    ) -> ContextPatch:
        patch = ContextPatch(
            context_id=context_id,
            patch_type=patch_type,
            actor=actor,
            path=normalize_context_path(path),
            value=value,
            metadata=dict(metadata or {}),
        )
        self._patches.append(patch)
        return patch

    def list_patches(self, *, context_id: str = "", view: str = "full") -> list[ContextPatch]:
        return [
            patch
            for patch in self._patches
            if (not context_id or patch.context_id == context_id) and patch_visible_in_view(patch, view)
        ]

    def snapshot(self, *, context_id: str, view: str = "full") -> dict[str, Any]:
        patches = self.list_patches(context_id=context_id, view=view)
        return {
            "schema_version": CONTEXT_STORE_SCHEMA_VERSION,
            "context_id": context_id,
            "view": normalize_context_view(view),
            "patches": [patch.snapshot() for patch in patches],
            "patch_count": len(patches),
        }


class JsonlContextStore(AppendOnlyContextStore):
    """Append-only Context Kernel store backed by JSONL without owning legacy state."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = resolve_context_store_path(path)
        super().__init__(patches=_read_context_patches(self.path))

    def append_patch(
        self,
        *,
        context_id: str,
        patch_type: str,
        actor: str,
        path: str,
        value: Any,
        metadata: dict[str, Any] | None = None,
    ) -> ContextPatch:
        patch = super().append_patch(
            context_id=context_id,
            patch_type=patch_type,
            actor=actor,
            path=path,
            value=value,
            metadata=metadata,
        )
        append_context_patch(patch, path=self.path)
        return patch

    def reload(self) -> None:
        self._patches = _read_context_patches(self.path)

    def ledger_snapshot(self, *, context_id: str = "", view: str = "full", limit: int = 200) -> dict[str, Any]:
        patches = self.list_patches(context_id=context_id, view=view)[-max(1, int(limit)) :]
        return {
            "schema_version": CONTEXT_STORE_SCHEMA_VERSION,
            "path": str(self.path),
            "context_id": context_id,
            "view": normalize_context_view(view),
            "patches": [patch.snapshot() for patch in patches],
            "patch_count": len(patches),
        }


def resolve_context_store_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.getenv("SPIRITKIN_CONTEXT_STORE_PATH", DEFAULT_CONTEXT_STORE_PATH)
    target = Path(raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve()


def append_context_patch(patch: ContextPatch, *, path: str | os.PathLike[str] | None = None) -> ContextPatch:
    target = resolve_context_store_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(patch.snapshot(), ensure_ascii=False, separators=(",", ":")) + "\n")
    return patch


def load_context_patches(
    *,
    path: str | os.PathLike[str] | None = None,
    context_id: str = "",
    view: str = "full",
    limit: int = 500,
) -> list[ContextPatch]:
    patches = _read_context_patches(resolve_context_store_path(path))
    visible = [
        patch
        for patch in patches
        if (not context_id or patch.context_id == context_id) and patch_visible_in_view(patch, view)
    ]
    return visible[-max(1, int(limit)) :]


def normalize_context_path(path: str) -> str:
    normalized = "/" + str(path or "").strip().strip("/")
    return "/" if normalized == "/" else normalized


def normalize_context_view(view: str) -> str:
    normalized = str(view or "full").strip().lower()
    return normalized if normalized in {"full", "task", "worker"} else "full"


def patch_visible_in_view(patch: ContextPatch, view: str) -> bool:
    normalized_view = normalize_context_view(view)
    if normalized_view == "full":
        return True

    metadata = dict(patch.metadata or {})
    raw_views = metadata.get("views", metadata.get("view"))
    if isinstance(raw_views, str):
        declared_views = {raw_views}
    elif isinstance(raw_views, (list, tuple, set)):
        declared_views = {str(item) for item in raw_views}
    else:
        declared_views = set()
    declared_views = {item.strip().lower() for item in declared_views if item.strip()}
    if declared_views and (normalized_view in declared_views or "shared" in declared_views):
        return True

    path = normalize_context_path(patch.path)
    prefixes = {
        "task": ("/intent", "/task", "/workflow", "/artifacts", "/execution"),
        "worker": ("/task/worker", "/worker", "/artifacts", "/execution/worker"),
    }
    return path.startswith(prefixes[normalized_view])


def _read_context_patches(path: Path) -> list[ContextPatch]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    patches: list[ContextPatch] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        patch = context_patch_from_snapshot(data) if isinstance(data, dict) else None
        if patch is not None:
            patches.append(patch)
    return patches


def _float_or_now(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.time()
