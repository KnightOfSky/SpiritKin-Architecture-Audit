from __future__ import annotations

import pytest

from backend.evaluation import BenchmarkRuntime


def _payload(**overrides):
    value = {
        "target": "growth-workflow-test",
        "target_type": "workflow",
        "version": "2.0",
        "baseline_version": "1.0",
        "dataset": "ecommerce-listing-v1",
        "measurement_source": "workflow_dry_run:wfr-123",
        "before": {
            "success_rate": 0.80,
            "latency_ms": 1500,
            "cost": 2.0,
            "retry_count": 4,
            "review_count": 3,
            "quality_score": 76,
        },
        "after": {
            "success_rate": 0.93,
            "latency_ms": 1100,
            "cost": 1.6,
            "retry_count": 2,
            "review_count": 1,
            "quality_score": 89,
        },
    }
    value.update(overrides)
    return value


def test_benchmark_derives_score_delta_and_passed_promotion_gate(tmp_path):
    runtime = BenchmarkRuntime(tmp_path / "benchmarks.jsonl")

    report = runtime.record_comparison(
        _payload(), candidate_id="growth-workflow-test", workspace_id="tenant-a", recorded_by="unit-test"
    )

    assert report["schema_version"] == "spiritkin.benchmark_runtime.v1"
    assert report["overall_score"] == pytest.approx(91.4)
    assert report["delta"]["overall_score"] > 0
    assert report["promotion_gate"]["passed"] is True
    assert report["policy"]["candidate_stage_advanced"] is False
    assert report["policy"]["activation_enabled"] is False
    assert runtime.snapshot(candidate_ids=["growth-workflow-test"])["passed_count"] == 1


def test_regressed_benchmark_blocks_promotion(tmp_path):
    runtime = BenchmarkRuntime(tmp_path / "benchmarks.jsonl")
    payload = _payload(
        after={
            "success_rate": 0.70,
            "latency_ms": 1700,
            "cost": 2.2,
            "retry_count": 5,
            "review_count": 4,
            "quality_score": 68,
        }
    )

    report = runtime.record_comparison(payload, candidate_id="growth-workflow-test", recorded_by="unit-test")

    assert report["promotion_gate"]["passed"] is False
    assert "success_rate_regressed" in report["promotion_gate"]["reasons"]
    assert "quality_score_regressed" in report["promotion_gate"]["reasons"]


def test_model_benchmark_waits_for_governed_jury_and_rejects_client_verdicts(tmp_path):
    runtime = BenchmarkRuntime(tmp_path / "benchmarks.jsonl")
    waiting = runtime.record_comparison(
        _payload(target_type="model", model_jury=[]),
        candidate_id="growth-model-test",
        recorded_by="unit-test",
    )

    assert waiting["promotion_gate"]["status"] == "waiting_jury"
    assert waiting["promotion_gate"]["passed"] is False
    with pytest.raises(PermissionError, match="client-supplied"):
        runtime.record_comparison(
            _payload(
                target_type="model",
                model_jury=[
                    {"provider": "gpt", "verdict": "approve", "rationale": "untrusted"},
                    {"provider": "claude", "verdict": "approve", "rationale": "untrusted"},
                ],
            ),
            candidate_id="growth-model-test",
            recorded_by="unit-test",
        )


def test_snapshot_with_empty_candidate_scope_returns_no_global_rows(tmp_path):
    runtime = BenchmarkRuntime(tmp_path / "benchmarks.jsonl")
    runtime.record_comparison(_payload(), candidate_id="candidate-a", recorded_by="unit-test")

    assert runtime.snapshot(candidate_ids=[])["count"] == 0


@pytest.mark.parametrize("field", ["success_rate", "quality_score", "latency_ms", "cost"])
def test_invalid_metrics_fail_closed(tmp_path, field):
    runtime = BenchmarkRuntime(tmp_path / "benchmarks.jsonl")
    payload = _payload()
    payload["after"][field] = "not-a-number"

    with pytest.raises(ValueError, match=field):
        runtime.record_comparison(payload, candidate_id="candidate-a", recorded_by="unit-test")
