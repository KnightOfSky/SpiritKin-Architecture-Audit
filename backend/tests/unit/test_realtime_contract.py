import unittest
from pathlib import Path

from backend.app import realtime_contract as contract
from backend.app.runtime_state import IGNORED_EVENT_TYPES
from backend.app.service_ports import PORT_SPECS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")


class RealtimeContractTest(unittest.TestCase):
    def test_frontend_contract_file_matches_source(self) -> None:
        self.assertEqual(
            _read(contract.FRONTEND_CONTRACT_PATH),
            contract.render_frontend_contract(),
            "frontend/js/realtime_contract.js 与契约源漂移，请运行 python scripts/generate_realtime_contract.py",
        )

    def test_desktop_contract_file_matches_source(self) -> None:
        self.assertEqual(
            _read(contract.DESKTOP_CONTRACT_PATH),
            contract.render_desktop_contract(),
            "RealtimeContract.g.cs 与契约源漂移，请运行 python scripts/generate_realtime_contract.py",
        )

    def test_shared_event_types_unique(self) -> None:
        self.assertEqual(len(contract.SHARED_EVENT_TYPES), len(set(contract.SHARED_EVENT_TYPES)))

    def test_runtime_state_ignored_events_registered(self) -> None:
        self.assertLessEqual(IGNORED_EVENT_TYPES, set(contract.SHARED_EVENT_TYPES))

    def test_default_ports_track_service_specs(self) -> None:
        self.assertEqual(contract.default_ports(), {spec.service_id: spec.default_port for spec in PORT_SPECS})
        self.assertEqual(contract.port_env_vars(), {spec.service_id: spec.env_var for spec in PORT_SPECS})

    def test_identifier_derivation(self) -> None:
        self.assertEqual(contract._pascal("assistant.confirmation_requested"), "AssistantConfirmationRequested")
        self.assertEqual(contract._camel("device.openclaw_state_updated"), "deviceOpenclawStateUpdated")


if __name__ == "__main__":
    unittest.main()
