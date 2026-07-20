"""Turn-limit guardrail for automatic model-to-model collaboration replies.

Model-to-model auto-replies can loop forever and burn API cost, so every
automatic reply must pass through this guard first. The guard tracks how many
automatic turns a conversation thread has consumed against a cap. When the cap
is reached the thread is paused ("awaiting_refill") and a human must explicitly
top it up ("人工续杯") before more automatic replies are produced.

State is persisted under the collaboration state root so the cap is shared
across the separate worker processes that each drive one agent, and survives
process restarts. Human-authored messages never consume a turn — only
automatic model replies do.
"""

from __future__ import annotations

import os
from typing import Any

from backend.state_store import (
    now_ts,
    read_json_state,
    resolve_state_path,
    write_json_state,
)

TURN_GUARD_SCHEMA_VERSION = "spiritkin.collaboration.turn_guard.v1"
DEFAULT_COLLABORATION_ROOT = "state/collaboration"
TURN_GUARD_STATE_FILE = "conversation_turn_guard.json"

STATUS_ACTIVE = "active"
STATUS_AWAITING_REFILL = "awaiting_refill"


def _default_turn_cap() -> int:
    raw = os.getenv("SPIRITKIN_COLLABORATION_TURN_CAP", "0")
    if str(raw or "").strip() == "":
        return 0
    try:
        cap = int(str(raw).strip())
    except (TypeError, ValueError):
        cap = 0
    return max(0, cap)


def _default_turn_hard_cap() -> int:
    raw = os.getenv("SPIRITKIN_COLLABORATION_TURN_HARD_CAP", "40")
    if str(raw or "").strip() == "":
        return 40
    try:
        cap = int(str(raw).strip())
    except (TypeError, ValueError):
        cap = 40
    return max(0, cap)


def _guard_path(root: str | os.PathLike[str] | None = None):
    base = resolve_state_path("SPIRITKIN_COLLABORATION_ROOT", DEFAULT_COLLABORATION_ROOT, root)
    return base / TURN_GUARD_STATE_FILE


def _load_state(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state = read_json_state(_guard_path(root), {"schema_version": TURN_GUARD_SCHEMA_VERSION, "threads": {}})
    threads = state.get("threads")
    if not isinstance(threads, dict):
        state["threads"] = {}
    return state


def _thread_key(thread_id: str) -> str:
    return str(thread_id or "").strip() or "__global__"


def _thread_record(state: dict[str, Any], thread_id: str) -> dict[str, Any]:
    key = _thread_key(thread_id)
    record = state["threads"].get(key)
    if not isinstance(record, dict):
        record = {
            "thread_id": key,
            "turns_used": 0,
            "cap": _default_turn_cap(),
            "hard_cap": _default_turn_hard_cap(),
            "continuous_auto_turns": 0,
            "refills": 0,
            "status": STATUS_ACTIVE,
            "blocked_reason": "",
            "created_at": now_ts(),
            "updated_at": now_ts(),
        }
        state["threads"][key] = record
    if "hard_cap" not in record:
        record["hard_cap"] = _default_turn_hard_cap()
    if "continuous_auto_turns" not in record:
        record["continuous_auto_turns"] = 0
    if "blocked_reason" not in record:
        record["blocked_reason"] = ""
    return record


def _verdict(record: dict[str, Any], *, allowed: bool, reason: str) -> dict[str, Any]:
    cap = int(record.get("cap") or 0)
    turns_used = int(record.get("turns_used") or 0)
    hard_cap = int(record.get("hard_cap") or 0)
    continuous_auto_turns = int(record.get("continuous_auto_turns") or 0)
    return {
        "schema_version": TURN_GUARD_SCHEMA_VERSION,
        "allowed": allowed,
        "reason": reason,
        "thread_id": record.get("thread_id"),
        "turns_used": turns_used,
        "cap": cap,
        "unlimited": cap <= 0,
        "remaining": max(0, cap - turns_used) if cap > 0 else 0,
        "hard_cap": hard_cap,
        "continuous_auto_turns": continuous_auto_turns,
        "hard_remaining": max(0, hard_cap - continuous_auto_turns) if hard_cap > 0 else 0,
        "refills": int(record.get("refills") or 0),
        "status": str(record.get("status") or STATUS_ACTIVE),
        "blocked_reason": str(record.get("blocked_reason") or ""),
    }


def _allowance_verdict(record: dict[str, Any], *, snapshot_reason: str = "within_cap") -> dict[str, Any]:
    cap = int(record.get("cap") or 0)
    turns_used = int(record.get("turns_used") or 0)
    hard_cap = int(record.get("hard_cap") or 0)
    continuous_auto_turns = int(record.get("continuous_auto_turns") or 0)
    status = str(record.get("status") or STATUS_ACTIVE)
    blocked_reason = str(record.get("blocked_reason") or "")
    if status == STATUS_AWAITING_REFILL:
        return _verdict(record, allowed=False, reason=blocked_reason or "turn_paused")
    if hard_cap > 0 and continuous_auto_turns >= hard_cap:
        return _verdict(record, allowed=False, reason="turn_hard_cap_reached")
    if cap > 0 and turns_used >= cap:
        return _verdict(record, allowed=False, reason="turn_cap_reached")
    return _verdict(record, allowed=True, reason=snapshot_reason)


def check_turn_allowance(thread_id: str, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Read-only check of whether an automatic reply is currently allowed."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    return _allowance_verdict(record)


def record_turn_and_check(thread_id: str, agent: str = "", root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Consume one automatic turn if allowed; persist and return the verdict.

    Returns allowed=False without consuming a turn once the cap is reached, and
    flips the thread to ``awaiting_refill`` so operators see it needs a manual
    top-up. Call this immediately before generating an automatic model reply.
    """
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    allowance = _allowance_verdict(record, snapshot_reason="turn_consumable")
    if not allowance.get("allowed", False):
        record["status"] = STATUS_AWAITING_REFILL
        record["blocked_reason"] = str(allowance.get("reason") or "turn_cap_reached")
        record["updated_at"] = now_ts()
        write_json_state(_guard_path(root), state)
        return _verdict(record, allowed=False, reason=str(allowance.get("reason") or "turn_cap_reached"))
    cap = int(record.get("cap") or 0)
    turns_used = int(record.get("turns_used") or 0)
    record["turns_used"] = turns_used + 1
    record["continuous_auto_turns"] = int(record.get("continuous_auto_turns") or 0) + 1
    record["status"] = STATUS_ACTIVE
    record["blocked_reason"] = ""
    record["last_agent"] = str(agent or "").strip()
    record["updated_at"] = now_ts()
    if cap > 0 and record["turns_used"] >= cap:
        record["status"] = STATUS_AWAITING_REFILL
        record["blocked_reason"] = "turn_cap_reached"
    write_json_state(_guard_path(root), state)
    return _verdict(record, allowed=True, reason="turn_consumed")


def refill_turns(
    thread_id: str,
    additional: int = 0,
    actor: str = "human_desktop",
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Human top-up ("人工续杯"): extend the cap and reactivate the thread."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    try:
        extra = int(additional)
    except (TypeError, ValueError):
        extra = 0
    if extra <= 0:
        extra = _default_turn_cap()
    current_cap = int(record.get("cap") or 0)
    if extra <= 0 and current_cap > 0:
        extra = current_cap
    if current_cap > 0:
        record["cap"] = current_cap + extra
    else:
        record["cap"] = 0
    record["continuous_auto_turns"] = 0
    record["refills"] = int(record.get("refills") or 0) + 1
    record["status"] = STATUS_ACTIVE
    record["blocked_reason"] = ""
    record["last_refill_by"] = str(actor or "").strip() or "human_desktop"
    record["last_refill_at"] = now_ts()
    record["updated_at"] = now_ts()
    write_json_state(_guard_path(root), state)
    return _verdict(record, allowed=True, reason="refilled")


def set_thread_turn_cap(
    thread_id: str,
    cap: int,
    actor: str = "human_desktop",
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Apply a new cap to an existing thread without restarting workers."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    try:
        next_cap = int(cap)
    except (TypeError, ValueError):
        next_cap = 0
    record["cap"] = max(0, next_cap)
    record["status"] = STATUS_ACTIVE
    record["blocked_reason"] = ""
    record["continuous_auto_turns"] = 0
    record["last_cap_set_by"] = str(actor or "").strip() or "human_desktop"
    record["last_cap_set_at"] = now_ts()
    record["updated_at"] = now_ts()
    write_json_state(_guard_path(root), state)
    return _allowance_verdict(record, snapshot_reason="cap_set")


def pause_turns(
    thread_id: str,
    actor: str = "human_desktop",
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Soft-stop automatic model-to-model replies for a thread."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    record["status"] = STATUS_AWAITING_REFILL
    record["blocked_reason"] = "turn_paused"
    record["last_paused_by"] = str(actor or "").strip() or "human_desktop"
    record["last_paused_at"] = now_ts()
    record["updated_at"] = now_ts()
    write_json_state(_guard_path(root), state)
    return _verdict(record, allowed=False, reason="turn_paused")


def record_human_activity(
    thread_id: str,
    actor: str = "human_desktop",
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Clear soft-stop/hard-fuse state when a human re-enters the thread."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    record["continuous_auto_turns"] = 0
    if str(record.get("blocked_reason") or "") in {"turn_paused", "turn_hard_cap_reached"}:
        record["status"] = STATUS_ACTIVE
        record["blocked_reason"] = ""
    record["last_human_activity_by"] = str(actor or "").strip() or "human_desktop"
    record["last_human_activity_at"] = now_ts()
    record["updated_at"] = now_ts()
    write_json_state(_guard_path(root), state)
    return _allowance_verdict(record, snapshot_reason="human_activity")


def reset_turns(thread_id: str, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Zero the consumed-turn counter for a thread (fresh conversation)."""
    state = _load_state(root)
    record = _thread_record(state, thread_id)
    record["turns_used"] = 0
    record["continuous_auto_turns"] = 0
    record["status"] = STATUS_ACTIVE
    record["blocked_reason"] = ""
    record["updated_at"] = now_ts()
    write_json_state(_guard_path(root), state)
    return _verdict(record, allowed=True, reason="reset")


def turn_guard_snapshot(thread_id: str = "", root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Operator-visible snapshot: one thread, or all threads when unset."""
    state = _load_state(root)
    if str(thread_id or "").strip():
        record = _thread_record(state, thread_id)
        return {
            "schema_version": TURN_GUARD_SCHEMA_VERSION,
            "default_cap": _default_turn_cap(),
            "default_hard_cap": _default_turn_hard_cap(),
            "thread": _allowance_verdict(record, snapshot_reason="snapshot"),
        }
    threads = []
    for record in state.get("threads", {}).values():
        if not isinstance(record, dict):
            continue
        threads.append(
            _allowance_verdict(record, snapshot_reason="snapshot")
        )
    return {
        "schema_version": TURN_GUARD_SCHEMA_VERSION,
        "default_cap": _default_turn_cap(),
        "default_hard_cap": _default_turn_hard_cap(),
        "total_threads": len(threads),
        "threads": threads,
    }
