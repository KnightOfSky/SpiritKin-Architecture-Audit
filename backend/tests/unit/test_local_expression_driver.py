from backend.expression.local_expression_driver import (
    infer_emotion_action,
    local_expression_enabled,
)


def test_disabled_by_default_env(monkeypatch):
    monkeypatch.delenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", raising=False)
    assert local_expression_enabled() is False


def test_disabled_never_calls_llm(monkeypatch):
    monkeypatch.delenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", raising=False)
    calls = {"count": 0}

    def _llm(_prompt):
        calls["count"] += 1
        return '{"emotion":"happy","action":"wave_hand"}'

    emotion, action = infer_emotion_action(
        "太好了，任务完成啦！",
        default_emotion="neutral",
        default_action="idle",
        llm_client=_llm,
    )
    # Disabled -> defaults returned, LLM never consulted.
    assert calls["count"] == 0
    assert (emotion, action) == ("neutral", "idle")


def test_enabled_classifies_and_normalizes(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")

    def _llm(_prompt):
        return '{"emotion":"happy","action":"wave_hand"}'

    emotion, action = infer_emotion_action(
        "太好了！",
        default_emotion="neutral",
        default_action="idle",
        llm_client=_llm,
    )
    assert (emotion, action) == ("happy", "wave_hand")


def test_illegal_output_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")

    def _llm(_prompt):
        return '{"emotion":"ecstatic","action":"backflip"}'

    emotion, action = infer_emotion_action(
        "内容",
        default_emotion="thinking",
        default_action="tap_chin",
        llm_client=_llm,
    )
    # Out-of-vocabulary emotion/action normalize to safe values, not the exotic input.
    assert emotion == "neutral"  # unknown emotion -> neutral
    assert action == "idle"  # unknown action -> idle


def test_unparseable_output_returns_defaults(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")

    def _llm(_prompt):
        return "not json at all"

    emotion, action = infer_emotion_action(
        "内容",
        default_emotion="thinking",
        default_action="tap_chin",
        llm_client=_llm,
    )
    assert (emotion, action) == ("thinking", "tap_chin")


def test_llm_exception_returns_defaults(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")

    def _llm(_prompt):
        raise RuntimeError("local model down")

    emotion, action = infer_emotion_action(
        "内容",
        default_emotion="waiting",
        default_action="await_confirmation",
        llm_client=_llm,
    )
    assert (emotion, action) == ("waiting", "await_confirmation")


def test_empty_text_returns_defaults_without_calling_llm(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")
    calls = {"count": 0}

    def _llm(_prompt):
        calls["count"] += 1
        return '{"emotion":"happy","action":"nod"}'

    emotion, action = infer_emotion_action(
        "   ",
        default_emotion="neutral",
        default_action="idle",
        llm_client=_llm,
    )
    assert calls["count"] == 0
    assert (emotion, action) == ("neutral", "idle")


def test_json_embedded_in_prose_is_extracted(monkeypatch):
    monkeypatch.setenv("SPIRITKIN_LOCAL_EXPRESSION_ENABLED", "true")

    def _llm(_prompt):
        return 'Sure! Here you go: {"emotion":"confused","action":"tilt_head"} hope that helps'

    emotion, action = infer_emotion_action(
        "嗯？",
        default_emotion="neutral",
        default_action="idle",
        llm_client=_llm,
    )
    assert (emotion, action) == ("confused", "tilt_head")
