from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from backend.orchestrator.context_mirror import build_project_context_mirror_from_files
from backend.orchestrator.execution_finalizer import FinalizerVerdict
from backend.orchestrator.workflow_graph import WorkflowRun
from backend.orchestrator.workflow_task_finalizer import sync_workflow_verdict_to_task


class ArchitecturePortTests(unittest.TestCase):
    def test_context_file_mirror_requires_app_loaders(self):
        with self.assertRaisesRegex(RuntimeError, "loaders must be injected"):
            build_project_context_mirror_from_files()

    def test_context_file_mirror_accepts_injected_loaders(self):
        with TemporaryDirectory() as root:
            mirror = build_project_context_mirror_from_files(
                project_root=root,
                desktop_state_loader=lambda path: {"sessions": [], "projects": [], "tasks": []},
                collaboration_snapshot_loader=lambda path: {"active_tasks": [], "recent_messages": []},
            )

        self.assertEqual(mirror.source_count, 3)

    def test_collaboration_finalizer_fails_explicitly_without_app_port(self):
        run = WorkflowRun(
            run_id="run-1",
            workflow_name="demo.v1",
            workflow_version="1",
            inputs={"task_id": "collab-1"},
        )
        verdict = FinalizerVerdict(
            task_id="run-1",
            verified=True,
            score=1.0,
            decision="commit",
            next_status="COMMITTED",
        )

        result = sync_workflow_verdict_to_task(run, verdict)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "integration_unavailable")


if __name__ == "__main__":
    unittest.main()
