from __future__ import annotations

import json

from backend.evaluation.model_jury import build_model_jury_prompt, build_model_jury_report


def _benchmark():
    return {
        "benchmark_id": "benchmark-model-1",
        "candidate_id": "growth-model-1",
        "workspace_id": "tenant-a",
        "target": "growth-model-1",
        "target_type": "model",
        "version": "2",
        "baseline_version": "1",
        "dataset": "model-planning-v1",
        "before": {"success_rate": 0.8, "quality_score": 75},
        "after": {"success_rate": 0.9, "quality_score": 88},
        "delta": {"overall_score": 10},
        "promotion_gate": {"status": "waiting_jury"},
    }


def _review(provider: str, model: str, verdict: str = "approve", benchmark_id: str = "benchmark-model-1"):
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "response_text": json.dumps(
            {
                "benchmark_id": benchmark_id,
                "verdict": verdict,
                "confidence": 0.9,
                "rationale": "Measured result supports the verdict.",
                "risks": [],
            }
        ),
    }


def test_model_jury_requires_two_distinct_structured_reviews():
    benchmark = _benchmark()
    report = build_model_jury_report(
        benchmark,
        {"reviews": [_review("openai", "gpt"), _review("anthropic", "claude")]},
        requested_by="unit-test",
    )

    assert report["status"] == "approved"
    assert report["approved"] is True
    assert report["structured_review_count"] == 2
    assert report["policy"]["client_verdicts_trusted"] is False


def test_model_jury_rejects_duplicate_unstructured_and_wrong_benchmark_reviews():
    report = build_model_jury_report(
        _benchmark(),
        {
            "reviews": [
                _review("openai", "gpt"),
                _review("openai", "gpt"),
                _review("anthropic", "claude", benchmark_id="other"),
                {"ok": True, "provider": "gemini", "model": "gemini", "response_text": "looks good"},
            ]
        },
        requested_by="unit-test",
    )

    assert report["status"] == "insufficient_evidence"
    assert report["approved"] is False
    assert report["structured_review_count"] == 1


def test_model_jury_prompt_contains_only_bounded_benchmark_evidence():
    prompt = build_model_jury_prompt({**_benchmark(), "internal_path": "C:/secret", "raw_output": "private"})

    assert "benchmark-model-1" in prompt
    assert "internal_path" not in prompt
    assert "raw_output" not in prompt
