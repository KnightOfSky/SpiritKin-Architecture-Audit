from backend.app.collaboration import (
    PRESENTATION_INTERNAL,
    PRESENTATION_OUTWARD,
    PRESENTATION_USER,
    _classify_collaboration_presentation,
)


def test_human_author_is_user_presentation():
    verdict = _classify_collaboration_presentation("human_desktop", ("codex",), "question")
    assert verdict == PRESENTATION_USER


def test_model_reply_to_human_is_outward():
    verdict = _classify_collaboration_presentation("claude_code", ("human_desktop",), "answer")
    assert verdict == PRESENTATION_OUTWARD


def test_model_to_model_only_is_internal():
    verdict = _classify_collaboration_presentation("claude_code", ("codex",), "answer")
    assert verdict == PRESENTATION_INTERNAL


def test_broadcast_all_is_outward_because_human_is_implicit_audience():
    verdict = _classify_collaboration_presentation("codex", ("all",), "answer")
    assert verdict == PRESENTATION_OUTWARD


def test_no_recipient_defaults_to_outward():
    verdict = _classify_collaboration_presentation("codex", (), "answer")
    assert verdict == PRESENTATION_OUTWARD


def test_mixed_human_and_model_recipient_is_outward():
    verdict = _classify_collaboration_presentation("claude_code", ("codex", "human_desktop"), "answer")
    assert verdict == PRESENTATION_OUTWARD
