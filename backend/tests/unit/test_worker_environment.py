from __future__ import annotations

from backend.orchestrator.worker_environment import (
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    STATUS_NOT_CONFIGURED,
    STATUS_PREVIEW_ONLY,
    WORKER_ENVIRONMENT_SCHEMA_VERSION,
    build_worker_environment_reports,
    validate_android_worker_environment,
    validate_browser_worker_environment,
    validate_openclaw_worker_environment,
    validate_remote_worker_environment,
)


def test_browser_worker_not_configured_by_default():
    report = validate_browser_worker_environment({})
    assert report.worker_id == "executor:browser_worker"
    assert report.status == STATUS_NOT_CONFIGURED
    assert report.registered is False
    assert report.remediation  # non-empty remediation guidance
    assert report.env_signals["SPIRITKIN_BROWSER_WORKER_COMMAND"] is False


def test_browser_worker_available_when_command_set():
    report = validate_browser_worker_environment(
        {"SPIRITKIN_BROWSER_WORKER_COMMAND": "python -m worker"}
    )
    assert report.status == STATUS_AVAILABLE
    assert report.registered is True
    assert report.remediation == ""
    assert report.env_signals["SPIRITKIN_BROWSER_WORKER_COMMAND"] is True


def test_openclaw_preview_only_without_http():
    report = validate_openclaw_worker_environment({})
    assert report.status == STATUS_PREVIEW_ONLY
    assert report.registered is True  # in-memory fallback still registers
    assert report.metadata["transport"] == "in_memory"
    assert report.remediation


def test_openclaw_available_with_http_base_url():
    report = validate_openclaw_worker_environment(
        {
            "SPIRITKIN_OPENCLAW_HTTP_BASE_URL": "http://127.0.0.1:9100",
            "SPIRITKIN_OPENCLAW_HTTP_TOKEN": "secret",
        }
    )
    assert report.status == STATUS_AVAILABLE
    assert report.metadata["transport"] == "http"
    assert report.env_signals["SPIRITKIN_OPENCLAW_HTTP_BASE_URL"] is True
    assert report.env_signals["SPIRITKIN_OPENCLAW_HTTP_TOKEN"] is True
    assert report.remediation == ""


def test_remote_worker_reflects_node_count():
    absent = validate_remote_worker_environment(node_count=0)
    assert absent.status == STATUS_NOT_CONFIGURED
    assert absent.registered is False
    assert absent.env_signals["remote_node_count"] == 0

    present = validate_remote_worker_environment(node_count=3)
    assert present.status == STATUS_AVAILABLE
    assert present.registered is True
    assert present.env_signals["remote_node_count"] == 3
    assert "3" in present.reason


def test_android_worker_status_mapping():
    pairing = validate_android_worker_environment(worker_status="needs_pairing")
    assert pairing.status == STATUS_NOT_CONFIGURED
    assert pairing.registered is False

    degraded = validate_android_worker_environment(worker_status="needs_attention")
    assert degraded.status == STATUS_DEGRADED
    assert degraded.registered is False

    ready = validate_android_worker_environment(worker_status="ready", device_count=2)
    assert ready.status == STATUS_AVAILABLE
    assert ready.registered is True
    assert ready.env_signals["device_count"] == 2


def test_android_worker_defaults_to_needs_pairing():
    report = validate_android_worker_environment(worker_status="")
    assert report.status == STATUS_NOT_CONFIGURED
    assert report.env_signals["companion_status"] == "needs_pairing"


def test_build_reports_aggregates_all_workers():
    payload = build_worker_environment_reports(
        remote_node_count=1,
        android_worker_status="ready",
        android_device_count=1,
        environ={},
    )
    assert payload["schema_version"] == WORKER_ENVIRONMENT_SCHEMA_VERSION
    assert payload["total"] == 4
    worker_ids = {report["worker_id"] for report in payload["reports"]}
    assert worker_ids == {
        "executor:browser_worker",
        "executor:openclaw",
        "executor:remote",
        "executor:android_device",
    }
    # status_counts should sum to total
    assert sum(payload["status_counts"].values()) == 4


def test_snapshot_is_json_serializable():
    report = validate_browser_worker_environment({})
    snap = report.snapshot()
    assert set(snap) == {
        "worker_id",
        "label",
        "status",
        "registered",
        "reason",
        "remediation",
        "env_signals",
        "metadata",
    }
