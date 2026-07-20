from __future__ import annotations

import multiprocessing
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.orchestrator.workflow_store import locked_path


def _hold_external_lock(path: str, ready, release) -> None:
    with locked_path(Path(path)):
        ready.set()
        release.wait(5)


class WorkflowStoreLockingTests(unittest.TestCase):
    def test_second_process_waits_without_reading_locked_byte(self) -> None:
        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "runs.json"
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            process = context.Process(target=_hold_external_lock, args=(str(target), ready, release))
            process.start()
            self.assertTrue(ready.wait(5), "child process did not acquire workflow lock")
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                with locked_path(target):
                    acquired = True
            finally:
                release.set()
                timer.cancel()
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)

        self.assertTrue(acquired)
        self.assertEqual(process.exitcode, 0)


if __name__ == "__main__":
    unittest.main()
