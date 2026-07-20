from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.capability.growth.runtime import GrowthRuntime, handle_growth_action
from backend.capability.growth.sandbox_bundle import GrowthSandboxBundleStore
from backend.capability.growth.sandbox_executor import GrowthDockerSandboxExecutor
from backend.capability.growth.sandbox_runtime import GrowthSandboxRuntimeProbe, sandbox_execution_policy

IMAGE = f"example/python@sha256:{'a' * 64}"


def _inspect_payload(volume_name: str) -> str:
    return json.dumps(
        [
            {
                "Config": {"User": "65534:65534"},
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "PidsLimit": 64,
                    "Memory": 256 * 1024 * 1024,
                    "NanoCpus": 500_000_000,
                    "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m"},
                    "LogConfig": {"Type": "json-file", "Config": {"max-size": "64k", "max-file": "1"}},
                },
                "Mounts": [
                    {"Type": "volume", "Name": volume_name, "Destination": "/workspace", "RW": False}
                ],
            }
        ]
    )


def _candidate() -> dict:
    return {
        "candidate_id": "growth-tool-sandbox",
        "workspace_id": "tenant-a",
        "kind": "tool",
        "status": "candidate",
        "current_stage": "sandbox",
        "activation": {"enabled": False},
    }


def _artifact(bundle: dict | None = None) -> dict:
    return {
        "artifact_id": "builder-sandbox",
        "candidate_id": "growth-tool-sandbox",
        "workspace_id": "tenant-a",
        "verification_plan": {"execution_status": "passed"},
        "sandbox_plan": {"bundle": GrowthSandboxBundleStore.summary(bundle or {})},
    }


def _prepare_bundle(store: GrowthSandboxBundleStore) -> dict:
    return store.prepare(
        _candidate(),
        _artifact(),
        {
            "files": [{"path": "tests/test_probe.py", "content": "print('sandbox-ok')\n"}],
            "command": ["python", "-I", "tests/test_probe.py"],
            "timeout_seconds": 5,
            "expected_exit_codes": [0],
        },
        prepared_by="unit-test",
    )


def _ready_probe(path: Path, monkeypatch) -> GrowthSandboxRuntimeProbe:
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_IMAGES", IMAGE)
    path.write_text(
        json.dumps(
            {
                "status": "ready",
                "reason": "ready",
                "cli_available": True,
                "daemon_available": True,
                "server_version": "test",
                "operating_system": "test",
                "execution_probe": {
                    "status": "passed",
                    "reason": "trusted_image_probe_passed",
                    "duration_ms": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    return GrowthSandboxRuntimeProbe(path)


def test_sandbox_execution_policy_requires_operator_enable_and_immutable_digest(monkeypatch):
    monkeypatch.delenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", raising=False)
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_IMAGES", "python:latest")
    assert sandbox_execution_policy()["configured"] is False
    assert sandbox_execution_policy()["approved_image_count"] == 0

    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_IMAGES", f"python:latest,{IMAGE}")
    policy = sandbox_execution_policy()
    assert policy["configured"] is True
    assert policy["approved_images"] == (IMAGE,)
    assert policy["automatic_pull"] is False
    assert policy["host_mounts_allowed"] is False

    monkeypatch.setenv("SPIRITKIN_GROWTH_SANDBOX_EXECUTION_ENABLED", "0")
    assert sandbox_execution_policy()["configured"] is False


def test_sandbox_bundle_is_bounded_immutable_text_and_detects_tampering(tmp_path):
    store = GrowthSandboxBundleStore(tmp_path / "artifacts")
    bundle = _prepare_bundle(store)
    files_root = store.verify_files(bundle)

    assert bundle["file_count"] == 1
    assert bundle["policy"]["host_execution_allowed"] is False
    assert files_root.joinpath("tests/test_probe.py").read_text(encoding="utf-8") == "print('sandbox-ok')\n"
    assert "path" not in json.dumps(store.summary(bundle))

    files_root.joinpath("untracked.py").write_text("print('unexpected')", encoding="utf-8")
    with pytest.raises(PermissionError, match="untracked files"):
        store.verify_files(bundle)


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ([{"path": "../escape.py", "content": "print(1)"}], "safe relative path"),
        ([{"path": "probe.py", "content": "api_key = 'real-secret-value'"}], "credentials or secrets"),
        ([{"path": "probe.py", "content": "bad\x00data"}], "NUL bytes"),
    ],
)
def test_sandbox_bundle_rejects_unsafe_content_before_write(tmp_path, files, error):
    store = GrowthSandboxBundleStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match=error):
        store.prepare(
            _candidate(),
            _artifact(),
            {"files": files, "command": ["python", "probe.py"]},
            prepared_by="unit-test",
        )
    assert not list((tmp_path / "sandboxes").rglob("manifest.json"))


def test_docker_executor_uses_strict_isolation_and_cleans_container(tmp_path, monkeypatch):
    bundle_store = GrowthSandboxBundleStore(tmp_path / "artifacts")
    bundle = _prepare_bundle(bundle_store)
    artifact = _artifact(bundle)
    probe = _ready_probe(tmp_path / "sandbox-runtime.json", monkeypatch)
    monkeypatch.setattr("backend.capability.growth.sandbox_executor.shutil.which", lambda _: "docker")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([IMAGE]), stderr="")
        if command[1:3] in (["volume", "create"], ["volume", "rm"]):
            return subprocess.CompletedProcess(command, 0, stdout="volume", stderr="")
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, stdout="container-id", stderr="")
        if command[1] == "cp":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "start":
            return subprocess.CompletedProcess(command, 0, stdout="container", stderr="")
        if command[1] == "wait":
            return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")
        if command[1] == "logs":
            return subprocess.CompletedProcess(command, 0, stdout="sandbox-ok\n", stderr="")
        if command[1:3] == ["container", "inspect"]:
            create = next(item for item in reversed(calls) if item[1] == "create")
            mount = create[create.index("--mount") + 1]
            volume_name = mount.split("src=", 1)[1].split(",", 1)[0]
            return subprocess.CompletedProcess(command, 0, stdout=_inspect_payload(volume_name), stderr="")
        if command[1:3] == ["rm", "--force"]:
            return subprocess.CompletedProcess(command, 0, stdout="removed", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("backend.capability.growth.sandbox_executor.subprocess.run", fake_run)
    executor = GrowthDockerSandboxExecutor(tmp_path / "artifacts", probe, bundle_store)
    report = executor.execute(
        _candidate(),
        artifact,
        {
            "execution_ack": "run_untrusted_code_in_isolated_container",
            "bundle_id": bundle["bundle_id"],
        },
        executed_by="unit-test",
    )

    creates = [command for command in calls if command[1] == "create"]
    create = creates[-1]
    assert report["status"] == "passed"
    assert report["exit_code"] == 0
    assert report["output"]["stdout_excerpt"] == "sandbox-ok\n"
    for required in ("--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL"):
        assert required in create
    for required in ("--pids-limit", "--memory", "--cpus", "--user", "65534:65534"):
        assert required in create
    assert "--mount" in create
    mount = create[create.index("--mount") + 1]
    assert mount.startswith("type=volume,") and mount.endswith(",readonly")
    assert not any("type=bind" in value for value in create)
    assert not any(value in create for value in ("--volume", "-v"))
    assert any(command[1:3] == ["rm", "--force"] for command in calls)
    assert any(command[1:3] == ["volume", "rm"] for command in calls)
    assert report["checks"]["container_cleanup_ok"] is True
    assert report["checks"]["isolation_validation_passed"] is True
    assert report["policy"]["candidate_stage_advanced"] is False
    assert report["policy"]["activation_enabled"] is False
    assert "path" not in json.dumps(executor.snapshot([_candidate()["candidate_id"]]))


def test_docker_executor_timeout_still_forces_cleanup(tmp_path, monkeypatch):
    bundle_store = GrowthSandboxBundleStore(tmp_path / "artifacts")
    bundle = _prepare_bundle(bundle_store)
    probe = _ready_probe(tmp_path / "sandbox-runtime.json", monkeypatch)
    monkeypatch.setattr("backend.capability.growth.sandbox_executor.shutil.which", lambda _: "docker")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([IMAGE]), stderr="")
        if command[1:3] in (["volume", "create"], ["volume", "rm"]):
            return subprocess.CompletedProcess(command, 0, stdout="volume", stderr="")
        if command[1] in {"create", "cp", "rm"}:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            create = next(item for item in reversed(calls) if item[1] == "create")
            mount = create[create.index("--mount") + 1]
            volume_name = mount.split("src=", 1)[1].split(",", 1)[0]
            return subprocess.CompletedProcess(command, 0, stdout=_inspect_payload(volume_name), stderr="")
        if command[1] == "start":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1] == "wait":
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 5), output="partial")
        raise AssertionError(command)

    monkeypatch.setattr("backend.capability.growth.sandbox_executor.subprocess.run", fake_run)
    report = GrowthDockerSandboxExecutor(tmp_path / "artifacts", probe, bundle_store).execute(
        _candidate(),
        _artifact(bundle),
        {"execution_ack": "run_untrusted_code_in_isolated_container"},
        executed_by="unit-test",
    )

    assert report["status"] == "failed"
    assert report["failure_reason"] == "execution_timeout"
    assert report["policy"]["container_code_executed"] is True
    assert any(command[1:3] == ["rm", "--force"] for command in calls)


def test_docker_executor_refuses_to_start_when_inspect_contradicts_policy(tmp_path, monkeypatch):
    bundle_store = GrowthSandboxBundleStore(tmp_path / "artifacts")
    bundle = _prepare_bundle(bundle_store)
    probe = _ready_probe(tmp_path / "sandbox-runtime.json", monkeypatch)
    monkeypatch.setattr("backend.capability.growth.sandbox_executor.shutil.which", lambda _: "docker")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([IMAGE]), stderr="")
        if command[1:3] in (["volume", "create"], ["volume", "rm"]):
            return subprocess.CompletedProcess(command, 0, stdout="volume", stderr="")
        if command[1] in {"create", "cp", "rm"}:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:3] == ["container", "inspect"]:
            unsafe = json.loads(_inspect_payload("wrong-volume"))
            unsafe[0]["HostConfig"]["NetworkMode"] = "bridge"
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(unsafe), stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("backend.capability.growth.sandbox_executor.subprocess.run", fake_run)
    report = GrowthDockerSandboxExecutor(tmp_path / "artifacts", probe, bundle_store).execute(
        _candidate(),
        _artifact(bundle),
        {"execution_ack": "run_untrusted_code_in_isolated_container"},
        executed_by="unit-test",
    )

    assert report["status"] == "failed"
    assert report["failure_reason"] == "container_isolation_validation_failed"
    assert report["policy"]["container_code_executed"] is False
    assert report["checks"]["network_disabled"] is False
    assert not any(command[1] == "start" for command in calls)
    assert any(command[1:3] == ["volume", "rm"] for command in calls)


def test_runtime_bundle_and_execution_evidence_never_advance_or_activate(tmp_path, monkeypatch):
    runtime = GrowthRuntime(
        event_path=tmp_path / "events.jsonl",
        registry_path=tmp_path / "registry.jsonl",
        artifact_root=tmp_path / "artifacts",
        sandbox_state_path=tmp_path / "sandbox-runtime.json",
    )
    proposed = runtime.propose_tool(
        {"missing_capability": "python.run_script", "workspace_id": "tenant-a", "requested_by": "unit-test"}
    )["candidate"]
    candidate_id = proposed["candidate_id"]
    runtime.advance_stage(
        {
            "candidate_id": candidate_id,
            "workspace_id": "tenant-a",
            "stage": "research",
            "evidence": {"summary": "requirements researched"},
        }
    )
    prepared = runtime.prepare_builder_artifact({"candidate_id": candidate_id, "workspace_id": "tenant-a"})
    bundle_result = runtime.prepare_sandbox_bundle(
        {
            "candidate_id": candidate_id,
            "workspace_id": "tenant-a",
            "prepared_by": "unit-test",
            "files": [{"path": "probe.py", "content": "print('ok')\n"}],
            "command": ["python", "-I", "probe.py"],
        }
    )
    assert bundle_result["candidate"]["current_stage"] == "research"
    assert bundle_result["candidate"]["activation"]["enabled"] is False
    assert bundle_result["builder_artifact"]["verification_plan"]["execution_status"] == "not_run"
    runtime.advance_stage(
        {
            "candidate_id": candidate_id,
            "workspace_id": "tenant-a",
            "stage": "sandbox",
            "evidence": {"summary": "bundle ready for static preflight"},
        }
    )
    verified = runtime.verify_builder_artifact(
        {"candidate_id": candidate_id, "workspace_id": "tenant-a", "verified_by": "unit-test"}
    )
    assert verified["verification_report"]["status"] == "passed"

    fake_report = {
        "execution_id": "execute-unit",
        "bundle_id": bundle_result["sandbox_bundle"]["bundle_id"],
        "status": "passed",
        "failure_reason": "",
        "exit_code": 0,
        "duration_ms": 12.5,
        "created_at": 1.0,
        "checks": {"network_disabled": True},
        "policy": {"candidate_stage_advanced": False, "activation_enabled": False},
    }
    monkeypatch.setattr(runtime.sandbox_executor, "execute", lambda *_args, **_kwargs: fake_report)
    executed = runtime.execute_builder_sandbox(
        {
            "candidate_id": candidate_id,
            "workspace_id": "tenant-a",
            "executed_by": "unit-test",
            "execution_ack": "run_untrusted_code_in_isolated_container",
        }
    )
    assert executed["candidate"]["current_stage"] == "sandbox"
    assert executed["candidate"]["activation"]["enabled"] is False
    assert executed["candidate"]["evidence"]["sandbox_execution"]["status"] == "passed"
    assert prepared["builder_artifact"]["artifact_id"] == executed["builder_artifact"]["artifact_id"]


@pytest.mark.parametrize("action", ["prepare_sandbox_bundle", "execute_builder_sandbox"])
def test_public_sandbox_actions_require_confirmation(action):
    with pytest.raises(PermissionError, match="explicit confirmation"):
        handle_growth_action({"action": action, "candidate_id": "missing"})
