
import pytest

from backend.app import collaboration_turn_guard as guard


@pytest.fixture
def guard_root(tmp_path, monkeypatch):
    root = tmp_path / "collab"
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_ROOT", str(root))
    monkeypatch.delenv("SPIRITKIN_COLLABORATION_TURN_CAP", raising=False)
    return root


def test_default_cap_consumes_turns_until_capped(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "3")
    thread = "t-cap"
    verdicts = [guard.record_turn_and_check(thread, agent="codex") for _ in range(3)]
    assert [v["allowed"] for v in verdicts] == [True, True, True]
    assert verdicts[-1]["remaining"] == 0
    # Cap reached: next automatic turn is denied and thread awaits refill.
    denied = guard.record_turn_and_check(thread, agent="claude_code")
    assert denied["allowed"] is False
    assert denied["reason"] == "turn_cap_reached"
    assert denied["status"] == guard.STATUS_AWAITING_REFILL


def test_denied_turn_does_not_increment_counter(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "1")
    thread = "t-noincr"
    guard.record_turn_and_check(thread)
    first_denied = guard.record_turn_and_check(thread)
    second_denied = guard.record_turn_and_check(thread)
    assert first_denied["allowed"] is False
    assert second_denied["allowed"] is False
    assert first_denied["turns_used"] == second_denied["turns_used"] == 1


def test_refill_reactivates_and_extends_cap(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "2")
    thread = "t-refill"
    guard.record_turn_and_check(thread)
    guard.record_turn_and_check(thread)
    assert guard.check_turn_allowance(thread)["allowed"] is False
    refilled = guard.refill_turns(thread, additional=2, actor="human_desktop")
    assert refilled["status"] == guard.STATUS_ACTIVE
    assert refilled["cap"] == 4
    assert refilled["remaining"] == 2
    # Now more automatic turns are allowed again.
    assert guard.record_turn_and_check(thread)["allowed"] is True


def test_refill_without_amount_uses_default_cap(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "2")
    thread = "t-refill-default"
    guard.record_turn_and_check(thread)
    guard.record_turn_and_check(thread)
    refilled = guard.refill_turns(thread)
    assert refilled["cap"] == 4
    assert refilled["refills"] == 1


def test_refill_without_amount_extends_existing_finite_cap_when_default_is_unlimited(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "2")
    thread = "t-refill-existing-finite"
    guard.record_turn_and_check(thread)
    guard.record_turn_and_check(thread)
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")

    refilled = guard.refill_turns(thread)
    assert refilled["cap"] == 4
    assert refilled["remaining"] == 2


def test_reset_zeroes_counter(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "2")
    thread = "t-reset"
    guard.record_turn_and_check(thread)
    guard.record_turn_and_check(thread)
    reset = guard.reset_turns(thread)
    assert reset["turns_used"] == 0
    assert reset["status"] == guard.STATUS_ACTIVE
    assert guard.check_turn_allowance(thread)["allowed"] is True


def test_state_shared_across_calls_persists_to_disk(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "5")
    thread = "t-persist"
    guard.record_turn_and_check(thread)
    guard.record_turn_and_check(thread)
    # A fresh read (simulating another worker process) sees the same count.
    assert guard.check_turn_allowance(thread)["turns_used"] == 2
    assert (guard_root / guard.TURN_GUARD_STATE_FILE).exists()


def test_snapshot_reports_all_threads(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "4")
    guard.record_turn_and_check("t-a")
    guard.record_turn_and_check("t-b")
    snap = guard.turn_guard_snapshot()
    assert snap["total_threads"] == 2
    assert snap["default_cap"] == 4
    thread_ids = {item["thread_id"] for item in snap["threads"]}
    assert {"t-a", "t-b"} <= thread_ids


def test_empty_thread_id_uses_global_bucket(guard_root):
    v1 = guard.record_turn_and_check("")
    v2 = guard.check_turn_allowance("")
    assert v1["thread_id"] == "__global__"
    assert v2["turns_used"] == 1


def test_invalid_cap_env_falls_back_to_default(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "not-a-number")
    assert guard._default_turn_cap() == 0


def test_zero_cap_allows_until_hard_cap(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_HARD_CAP", "3")
    thread = "t-unlimited"
    verdicts = [guard.record_turn_and_check(thread, agent="codex") for _ in range(3)]
    assert [v["allowed"] for v in verdicts] == [True, True, True]
    assert verdicts[-1]["cap"] == 0
    assert verdicts[-1]["unlimited"] is True
    assert verdicts[-1]["continuous_auto_turns"] == 3

    denied = guard.record_turn_and_check(thread, agent="claude_code")
    assert denied["allowed"] is False
    assert denied["reason"] == "turn_hard_cap_reached"
    assert denied["status"] == guard.STATUS_AWAITING_REFILL


def test_human_activity_clears_pause_and_hard_fuse(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_HARD_CAP", "1")
    thread = "t-human-clear"
    assert guard.record_turn_and_check(thread)["allowed"] is True
    assert guard.check_turn_allowance(thread)["allowed"] is False
    assert guard.check_turn_allowance(thread)["reason"] == "turn_hard_cap_reached"

    cleared = guard.record_human_activity(thread, actor="human_desktop")
    assert cleared["allowed"] is True
    assert cleared["continuous_auto_turns"] == 0
    assert cleared["status"] == guard.STATUS_ACTIVE


def test_pause_and_refill_resume_unlimited_thread(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    thread = "t-pause"
    paused = guard.pause_turns(thread)
    assert paused["allowed"] is False
    assert paused["reason"] == "turn_paused"
    assert guard.check_turn_allowance(thread)["allowed"] is False

    resumed = guard.refill_turns(thread)
    assert resumed["allowed"] is True
    assert resumed["cap"] == 0
    assert guard.check_turn_allowance(thread)["allowed"] is True


def test_set_thread_turn_cap_applies_immediately(guard_root, monkeypatch):
    monkeypatch.setenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    thread = "t-set-cap"
    guard.record_turn_and_check(thread)
    changed = guard.set_thread_turn_cap(thread, 1)
    assert changed["cap"] == 1
    assert changed["allowed"] is False
    assert changed["reason"] == "turn_cap_reached"
    assert guard.record_turn_and_check(thread)["allowed"] is False
