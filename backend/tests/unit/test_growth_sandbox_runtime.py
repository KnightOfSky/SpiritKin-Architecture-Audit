from __future__ import annotations

import json
import subprocess

from backend.capability.growth.runtime import GrowthRuntime, handle_growth_action
from backend.capability.growth.sandbox_runtime import GrowthSandboxRuntimeProbe


def test_missing_docker_is_cached_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.shutil.which", lambda _: None)
    probe = GrowthSandboxRuntimeProbe(tmp_path / "sandbox.json")

    report = probe.probe()

    assert report["status"] == "unavailable"
    assert report["reason"] == "docker_cli_missing"
    assert report["candidate_execution_enabled"] is False
    assert probe.snapshot()["reason"] == "docker_cli_missing"


def test_docker_probe_is_read_only_and_reports_daemon_metadata(tmp_path, monkeypatch):
    image = f"example/python@sha256:{'a' * 64}"
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_IMAGES", image)
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_PROBE_COMMAND_JSON", '["python", "-I", "-c", "print(1)"]')
    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.shutil.which", lambda _: "docker")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        command = args[0]
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"ServerVersion": "27.1", "OperatingSystem": "Docker Desktop", "Images": 4}), stderr="")
        if command[1] == "wait":
            return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.subprocess.run", fake_run)
    report = GrowthSandboxRuntimeProbe(tmp_path / "sandbox.json").probe()

    assert report["status"] == "ready"
    assert report["server_version"] == "27.1"
    assert report["image_count"] == 4
    assert report["automatic_pull"] is False
    assert report["candidate_execution_enabled"] is True
    assert report["approved_image_count"] == 1
    assert calls[0][0][0] == ["docker", "info", "--format", "{{json .}}"]
    assert any(call[0][0][1] == "create" and "--network" in call[0][0] and "none" in call[0][0] for call in calls)
    assert any(call[0][0][1] == "rm" for call in calls)


def test_runtime_snapshot_exposes_not_probed_without_running_docker(tmp_path, monkeypatch):
    def fail_run(*args, **kwargs):
        raise AssertionError("snapshot must not invoke the runtime")

    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.subprocess.run", fail_run)
    runtime = GrowthRuntime(
        event_path=tmp_path / "events.jsonl",
        registry_path=tmp_path / "registry.jsonl",
        artifact_root=tmp_path / "artifacts",
        sandbox_state_path=tmp_path / "sandbox.json",
    )

    assert runtime.snapshot()["sandbox_runtime"]["status"] == "not_probed"


def test_execution_probe_failure_keeps_candidate_execution_disabled(tmp_path, monkeypatch):
    image = f"example/python@sha256:{'a' * 64}"
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_IMAGES", image)
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_PROBE_COMMAND_JSON", '["python", "-c", "print(1)"]')
    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.shutil.which", lambda _: "docker")

    def fake_run(command, **_kwargs):
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"ServerVersion": "27.1"}), stderr="")
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="unavailable")
        raise AssertionError(command)

    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.subprocess.run", fake_run)
    report = GrowthSandboxRuntimeProbe(tmp_path / "sandbox.json").probe()

    assert report["status"] == "ready"
    assert report["execution_probe"]["status"] == "unavailable"
    assert report["candidate_execution_enabled"] is False


def test_runtime_probe_records_only_public_status(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.shutil.which", lambda _: None)
    runtime = GrowthRuntime(
        event_path=tmp_path / "events.jsonl",
        registry_path=tmp_path / "registry.jsonl",
        artifact_root=tmp_path / "artifacts",
        sandbox_state_path=tmp_path / "sandbox.json",
    )

    result = runtime.probe_sandbox_runtime({})

    assert result["ok"] is True
    assert result["sandbox_runtime"]["status"] == "unavailable"
    assert "path" not in json.dumps(result, ensure_ascii=False)


def test_public_handler_probes_managed_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.capability.growth.sandbox_runtime.shutil.which", lambda _: None)
    monkeypatch.setenv("SPIRITKIN_GROWTH_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("SPIRITKIN_GROWTH_REGISTRY_LOG", str(tmp_path / "registry.jsonl"))
    monkeypatch.setenv("SPIRITKIN_GROWTH_BUILDER_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_RUNTIME_PATH", str(tmp_path / "sandbox.json"))

    result = handle_growth_action({"action": "probe_sandbox_runtime", "path": "C:/forbidden", "confirmed": True})

    assert result["sandbox_runtime"]["status"] == "unavailable"
    assert "forbidden" not in json.dumps(result)
