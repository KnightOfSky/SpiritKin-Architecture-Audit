from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from backend.app.collaboration import handle_collaboration_action, post_collaboration_message


@pytest.fixture
def collab_env(monkeypatch):
    with TemporaryDirectory() as tmp:
        monkeypatch.setenv("SPIRITKIN_AGENT_ROUTE_BUS_ROOT", str(Path(tmp) / "agent_route_bus"))
        # auto_reply.json 等开关文件也必须落在临时目录：不隔离的话会读到仓库
        # 真实 state/collaboration/auto_reply.json（桌面开关随手一关测试就翻车）。
        monkeypatch.setenv("SPIRITKIN_COLLABORATION_ROOT", str(Path(tmp) / "collaboration"))
        # Push is best-effort and would try to reach ws://…:8765; disable it so
        # the guard tests never depend on the event bridge being up.
        monkeypatch.setenv("SPIRITKIN_DISABLE_COLLABORATION_PUSH", "1")
        monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "3")
        # These tests exercise the turn budget, which only applies once the
        # operator has explicitly enabled automatic model→model replies.
        monkeypatch.setenv("SPIRITKIN_COLLABORATION_AUTO_REPLY", "1")
        yield Path(tmp) / "collaboration"


def _post(root, **payload):
    return post_collaboration_message(payload, root=root)


def test_auto_reply_default_on_and_env_off_rejects_model_to_model_answer(collab_env, monkeypatch):
    # 2026-07-06 起双工默认开：无 env、无 auto_reply.json 时模型互聊 answer 可落库（轮次护栏仍然限次）。
    monkeypatch.delenv("SPIRITKIN_COLLABORATION_AUTO_REPLY", raising=False)
    assert _post(
        collab_env,
        from_agent="claude_code",
        to_agent="codex",
        role="answer",
        content="自动回复。",
        thread_id="disabled-thread",
    ).message_id
    # 显式关掉后模型互聊被拒收。
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_AUTO_REPLY", "0")
    with pytest.raises(ValueError) as excinfo:
        _post(
            collab_env,
            from_agent="claude_code",
            to_agent="codex",
            role="answer",
            content="自动回复。",
            thread_id="disabled-thread",
        )
    assert "auto_reply_disabled" in str(excinfo.value)
    # Human-authored and model→human messages are unaffected by the switch.
    assert _post(collab_env, from_agent="human_desktop", to_agent="codex", role="question", content="人问。", thread_id="disabled-thread").message_id
    assert _post(collab_env, from_agent="codex", to_agent="human_desktop", role="answer", content="答人。", thread_id="disabled-thread").message_id


def test_auto_reply_off_downgrades_mixed_recipients_to_human_only(collab_env, monkeypatch):
    # 2026-07-07 事故修复：fan-out 让模型给人类的回答总是抄送其他模型，
    # 双工关掉时旧逻辑整条拒收（用户只见"卡住"）。现在应剔除模型收件人、
    # 保留人类收件人照常投递，且不消耗轮次预算。
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_AUTO_REPLY", "0")
    message = _post(
        collab_env,
        from_agent="model_deepseek",
        to_agents=["human_desktop", "main_text"],
        role="answer",
        content="给人类的辩论回复。",
        thread_id="duplex-off-thread",
    )
    assert message.message_id
    assert message.to_agents == ("human_desktop",)
    # 纯模型→模型仍被拒收。
    with pytest.raises(ValueError) as excinfo:
        _post(
            collab_env,
            from_agent="model_deepseek",
            to_agents=["main_text"],
            role="answer",
            content="纯互聊。",
            thread_id="duplex-off-thread",
        )
    assert "auto_reply_disabled" in str(excinfo.value)


def test_model_to_model_answer_consumes_turns_until_capped(collab_env):
    thread = "auto-thread"
    # Three automatic model→model answers are allowed (cap=3).
    for _ in range(3):
        msg = _post(
            collab_env,
            from_agent="claude_code",
            to_agent="codex",
            role="answer",
            content="自动回复。",
            thread_id=thread,
        )
        assert msg.message_id
    # Fourth automatic reply is blocked by the turn cap.
    with pytest.raises(ValueError) as excinfo:
        _post(
            collab_env,
            from_agent="claude_code",
            to_agent="codex",
            role="answer",
            content="第四条自动回复。",
            thread_id=thread,
        )
    assert "turn_cap_reached" in str(excinfo.value)


def test_human_author_never_consumes_a_turn(collab_env):
    thread = "human-thread"
    for _ in range(10):
        _post(
            collab_env,
            from_agent="human_desktop",
            to_agent="codex",
            role="question",
            content="人类提问，不该消耗预算。",
            thread_id=thread,
        )
    status = handle_collaboration_action(
        {"action": "turn_guard_status", "thread_id": thread},
        root=collab_env,
    )["turn_guard"]
    assert status["thread"]["turns_used"] == 0


def test_model_reply_to_human_only_does_not_consume(collab_env):
    thread = "reply-human-thread"
    for _ in range(10):
        _post(
            collab_env,
            from_agent="claude_code",
            to_agent="human_desktop",
            role="answer",
            content="模型只回人类，不该消耗预算。",
            thread_id=thread,
        )
    status = handle_collaboration_action(
        {"action": "turn_guard_status", "thread_id": thread},
        root=collab_env,
    )["turn_guard"]
    assert status["thread"]["turns_used"] == 0


def test_refill_reactivates_paused_conversation(collab_env):
    thread = "refill-thread"
    for _ in range(3):
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="自动。", thread_id=thread)
    with pytest.raises(ValueError):
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="超限。", thread_id=thread)
    refilled = handle_collaboration_action(
        {"action": "refill_turns", "thread_id": thread, "additional": 2, "actor": "human_desktop"},
        root=collab_env,
    )["turn_guard"]
    assert refilled["status"] == "active"
    assert refilled["remaining"] == 2
    # Automatic replies flow again after the human top-up.
    msg = _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="续杯后。", thread_id=thread)
    assert msg.message_id


def test_zero_turn_cap_uses_hard_fuse_and_human_message_resumes(collab_env, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_HARD_CAP", "3")
    thread = "unlimited-hard-thread"
    for _ in range(3):
        msg = _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="自动。", thread_id=thread)
        assert msg.message_id

    with pytest.raises(ValueError) as excinfo:
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="硬熔断。", thread_id=thread)
    assert "turn_hard_cap_reached" in str(excinfo.value)

    _post(collab_env, from_agent="human_desktop", to_agent="codex", role="question", content="继续。", thread_id=thread)
    status = handle_collaboration_action(
        {"action": "turn_guard_status", "thread_id": thread},
        root=collab_env,
    )["turn_guard"]
    assert status["thread"]["allowed"] is True
    assert status["thread"]["continuous_auto_turns"] == 0


def test_pause_turns_blocks_until_human_refill_or_message(collab_env):
    thread = "pause-thread"
    paused = handle_collaboration_action(
        {"action": "pause_turns", "thread_id": thread, "actor": "human_desktop"},
        root=collab_env,
    )["turn_guard"]
    assert paused["allowed"] is False
    assert paused["reason"] == "turn_paused"

    with pytest.raises(ValueError) as excinfo:
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="暂停后不应投递。", thread_id=thread)
    assert "turn_paused" in str(excinfo.value)

    refilled = handle_collaboration_action(
        {"action": "refill_turns", "thread_id": thread, "actor": "human_desktop"},
        root=collab_env,
    )["turn_guard"]
    assert refilled["allowed"] is True
    assert _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="续后。", thread_id=thread).message_id


def test_set_thread_turn_cap_applies_to_existing_thread(collab_env):
    thread = "set-cap-thread"
    assert _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="自动。", thread_id=thread).message_id
    changed = handle_collaboration_action(
        {"action": "set_thread_turn_cap", "thread_id": thread, "cap": 1, "actor": "human_desktop"},
        root=collab_env,
    )["turn_guard"]
    assert changed["cap"] == 1
    assert changed["allowed"] is False
    assert changed["reason"] == "turn_cap_reached"

    with pytest.raises(ValueError) as excinfo:
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="即时上限阻止。", thread_id=thread)
    assert "turn_cap_reached" in str(excinfo.value)


def test_reset_clears_consumed_turns(collab_env):
    thread = "reset-thread"
    for _ in range(2):
        _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="自动。", thread_id=thread)
    reset = handle_collaboration_action(
        {"action": "reset_turns", "thread_id": thread},
        root=collab_env,
    )["turn_guard"]
    assert reset["turns_used"] == 0
    assert reset["status"] == "active"


def test_snapshot_includes_turn_guard(collab_env):
    _post(collab_env, from_agent="codex", to_agent="claude_code", role="answer", content="自动。", thread_id="snap-thread")
    snapshot = handle_collaboration_action({"action": "snapshot"}, root=collab_env)["collaboration"]
    assert "turn_guard" in snapshot
    assert snapshot["turn_guard"]["schema_version"].startswith("spiritkin.collaboration.turn_guard")


def test_push_helper_reports_disabled(collab_env):
    from backend.app.collaboration import push_collaboration_message_to_event_bridge

    message = _post(
        collab_env,
        from_agent="human_desktop",
        to_agent="codex",
        role="question",
        content="推送禁用时应报告 disabled。",
        thread_id="push-thread",
    )
    result = push_collaboration_message_to_event_bridge(message)
    assert result["pushed"] is False
    assert result["reason"] == "collaboration_push_disabled"
