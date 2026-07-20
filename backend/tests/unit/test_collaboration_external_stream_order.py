from __future__ import annotations

import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "collaboration_agent_worker.py"


def load_worker_module():
    spec = importlib.util.spec_from_file_location("collaboration_agent_worker_stream_order", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load collaboration_agent_worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollaborationExternalStreamOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.worker = load_worker_module()

    def test_stdout_events_are_drained_before_process_exit_and_return(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            script = "import sys; sys.stdout.write('delayed-token '); sys.stdout.flush()"
            message = {
                "schema_version": "spiritkin.agent_protocol.v1",
                "message_id": "agentmsg-drain-stream",
                "sender": "human_desktop",
                "recipient": "codex",
                "message_type": "question",
                "content": f"Workspace path: {workspace}\n请流式回复。",
                "context_id": "route-thread",
            }
            assistant = {
                "assistant_id": "codex_cli",
                "command": f'"{sys.executable}" -c "{script}"',
                "working_directory": "",
                "enabled": True,
            }
            events: list[dict] = []
            unblock = threading.Event()
            failures: list[BaseException] = []

            def fake_request_json(_api: str, _path: str, payload: dict):
                output = str(payload.get("metadata", {}).get("output") or "")
                if output == "delayed-token":
                    unblock.wait(timeout=7)
                events.append(payload)
                return {"ok": True}

            def run_worker() -> None:
                try:
                    result["reply"] = self.worker.run_external_assistant(
                        assistant,
                        message,
                        api="http://127.0.0.1:8788",
                        agent="codex",
                        transport="route_bus",
                        dry_run=False,
                    )
                except BaseException as exc:
                    failures.append(exc)

            result: dict[str, str] = {}
            with patch.object(self.worker, "request_json", side_effect=fake_request_json):
                thread = threading.Thread(target=run_worker)
                thread.start()
                time.sleep(0.2)
                self.assertTrue(thread.is_alive())
                unblock.set()
                thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(result.get("reply"), "delayed-token")
        count_after_return = len(events)
        time.sleep(0.1)
        self.assertEqual(len(events), count_after_return)
        lifecycles = [event.get("metadata", {}).get("lifecycle") for event in events]
        self.assertIn("process_exited", lifecycles)
        process_exited_index = lifecycles.index("process_exited")
        delayed_index = next(index for index, event in enumerate(events) if event.get("metadata", {}).get("output") == "delayed-token")
        self.assertLess(delayed_index, process_exited_index)


if __name__ == "__main__":
    unittest.main()
