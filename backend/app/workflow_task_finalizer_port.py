from __future__ import annotations

from pathlib import Path
from typing import Any


class DefaultCollaborationTaskFinalizerPort:
    def load_task(self, task_id: str, root: Path | None) -> Any | None:
        from backend.app.collaboration import load_collaboration_tasks

        return next((task for task in load_collaboration_tasks(root) if task.task_id == task_id), None)

    def update_task(self, payload: dict[str, Any], root: Path | None) -> Any:
        from backend.app.collaboration import update_collaboration_task

        return update_collaboration_task(payload, root)
