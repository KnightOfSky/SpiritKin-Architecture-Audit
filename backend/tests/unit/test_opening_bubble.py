from __future__ import annotations

from types import SimpleNamespace

from backend.app.command_gateway import build_proactive_feedback_response
from backend.proactive.opening_bubble import OpeningBubbleCandidate, OpeningBubbleService


class _Agent:
    pending_execution = None
    task_queue_snapshot = []


def test_startup_stays_quiet_without_evidence() -> None:
    service = OpeningBubbleService()

    assert service.startup_event(agent=_Agent(), now=1000) is None


def test_safety_candidate_wins_over_unfinished_task() -> None:
    agent = _Agent()
    agent.pending_execution = SimpleNamespace(request=SimpleNamespace(target="local_pc", operation="delete_file"))
    agent.task_queue_snapshot = [{"task_id": "task-1", "request": "整理报告", "status": "queued"}]

    event = OpeningBubbleService().startup_event(agent=agent, now=1000)

    assert event is not None
    assert event["type"] == "opening_bubble.present"
    assert event["payload"]["kind"] == "safety"
    assert event["payload"]["action"]["type"] == "open_conversation"
    assert event["payload"]["requires_confirmation"] is True


def test_quiet_boundary_keeps_recovery_but_not_care() -> None:
    agent = _Agent()
    agent.task_queue_snapshot = [{"task_id": "task-1", "request": "继续 M15", "status": "running"}]
    relationship = {"care_strategy": {"proactive_level": "off"}}

    event = OpeningBubbleService().startup_event(agent=agent, relationship=relationship, now=1000)

    assert event is not None
    assert event["payload"]["kind"] == "recovery"
    assert "继续 M15" in event["payload"]["text"]


def test_proactive_suggestion_maps_to_navigation_only_action() -> None:
    event = {
        "type": "proactive.suggested",
        "payload": {
            "signal_id": "signal-1",
            "signal_kind": "task_context",
            "text": "需要我帮你拆下一步吗？",
            "action_prompt": "请拆解下一步",
            "created_at": 1000,
            "expires_at": 2000,
        },
    }

    bubble = OpeningBubbleService().from_proactive_event(event, now=1000)

    assert bubble is not None
    action = bubble["payload"]["action"]
    assert action == {
        "type": "open_conversation",
        "label": "打开对话",
        "prompt": "请拆解下一步",
        "source_id": "signal-1",
    }
    assert "executor" not in bubble["payload"]
    assert "tool" not in bubble["payload"]


def test_opening_bubble_deduplicates_across_restore() -> None:
    candidate = OpeningBubbleCandidate(kind="recovery", text="继续任务", source_id="task-1", expires_at=3000)
    first_service = OpeningBubbleService(dedupe_seconds=3600)
    first = first_service.select_event([candidate], now=1000)
    second_service = OpeningBubbleService(dedupe_seconds=3600)
    second_service.restore([{"event_type": "opening_bubble.present", "payload": first["payload"]}])

    assert second_service.select_event([candidate], now=1200) is None


class _FeedbackRuntime:
    def record_proactive_feedback(self, signal_id, feedback):
        return {"type": "proactive.feedback", "payload": {"signal_id": signal_id, "feedback": feedback}}


def test_feedback_endpoint_validates_and_records() -> None:
    status, response = build_proactive_feedback_response(
        _FeedbackRuntime(),
        {"signal_id": "signal-1", "feedback": "dismissed"},
    )
    invalid_status, _ = build_proactive_feedback_response(_FeedbackRuntime(), {"feedback": "dismissed"})

    assert status == 200
    assert response["event"]["payload"]["feedback"] == "dismissed"
    assert invalid_status == 400
