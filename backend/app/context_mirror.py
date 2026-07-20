from __future__ import annotations

from pathlib import Path

from backend.app.collaboration import build_collaboration_snapshot
from backend.app.desktop_state import load_desktop_state
from backend.orchestrator.context_mirror import ContextMirrorSnapshot
from backend.orchestrator.context_mirror import build_project_context_mirror_from_files as _build_from_files


def build_project_context_mirror_from_files(
    *,
    project_root: str | Path | None = None,
    desktop_state_path: str | Path | None = None,
    collaboration_root: str | Path | None = None,
    ecommerce_state_dir: str | Path | None = None,
    context_id: str = "project:current",
) -> ContextMirrorSnapshot:
    return _build_from_files(
        project_root=project_root,
        desktop_state_path=desktop_state_path,
        collaboration_root=collaboration_root,
        ecommerce_state_dir=ecommerce_state_dir,
        context_id=context_id,
        desktop_state_loader=load_desktop_state,
        collaboration_snapshot_loader=build_collaboration_snapshot,
    )
