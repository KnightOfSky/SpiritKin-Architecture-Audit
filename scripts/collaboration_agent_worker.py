from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.agent_management import load_agent_management_state, normalize_external_assistant_command
from backend.app.collaboration import (
    collaboration_auto_reply_enabled,
    is_human_collaboration_agent,
    resolve_collaboration_root,
)
from backend.app.learning_workflow import (
    ModelProviderConfig,
    discover_model_providers,
    load_assist_models,
    request_model_review,
)

DEFAULT_API = os.getenv("SPIRITKIN_DESKTOP_API", "http://127.0.0.1:8788")
DEFAULT_AGENT_CONFIG = os.getenv("SPIRITKIN_AGENT_MANAGEMENT_PATH", "state/desktop_console/agent_management.json")


class WorkerConfigurationError(RuntimeError):
    """Assistant is misconfigured (disabled, no command, etc.). Retrying cannot help."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SpiritKin collaboration Agent worker")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--agent", required=True, help="Agent inbox id, e.g. claude_code or codex")
    parser.add_argument("--assistant-id", default="", help="External assistant config id. Defaults to --agent or codex_cli for codex.")
    parser.add_argument("--agent-config", default=DEFAULT_AGENT_CONFIG)
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--transport", choices=["route_bus", "legacy_inbox"], default="route_bus")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Do not invoke CLI; post a deterministic worker note instead.")
    parser.add_argument("--no-post", action="store_true", help="Invoke CLI but do not write replies back.")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=1,
        help="Full assistant executions per message. Defaults to one because replaying a model/CLI run can repeat side effects; reply transport has its own idempotent retry.",
    )
    parser.add_argument("--retry-backoff", type=float, default=3.0, help="Base seconds between retries; grows linearly with the attempt number.")
    parser.add_argument("--auto-reply", action="store_true", help="Reply to model-authored messages too (or set SPIRITKIN_COLLABORATION_AUTO_REPLY=1). Off by default: only human-authored messages are processed.")
    parser.add_argument("--no-push", action="store_true", help="Disable the event-bridge push trigger and rely on interval polling only.")
    parser.add_argument("--push-url", default="", help="Event bridge WebSocket URL. Defaults to the runtime event sink (ws://127.0.0.1:8765).")
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    assistant = load_external_assistant(args.agent_config, args.assistant_id or default_assistant_id(args.agent))
    max_attempts = max(1, args.max_attempts)
    # 双工开关初值；主循环内每轮会重新读取（见下），保证桌面开关切换即时生效。
    auto_reply = bool(args.auto_reply) or collaboration_auto_reply_enabled()
    wake = threading.Event()
    if not args.no_push and not args.once:
        push_url = resolve_push_ws_url(args.push_url)
        if push_url:
            start_push_listener(push_url, args.agent, wake)
            print(f"push trigger subscribed: {push_url}", flush=True)
        else:
            print("push trigger unavailable (no event sink URL); polling only", file=sys.stderr, flush=True)
    seen: set[str] = set()
    consecutive_failures = 0
    while True:
        try:
            # 每轮实时重读双工开关：worker 启动时快照过一次，但桌面头部开关随时可切；
            # 只快照会导致"开关显示开、worker 仍按关跳过"的静默丢回复（2026-07-07 实测事故）。
            auto_reply = bool(args.auto_reply) or collaboration_auto_reply_enabled()
            pending = deque(
                prioritize_worker_messages(
                    list_worker_messages(api, args.agent, args.thread_id, args.task_id, transport=args.transport, limit=args.limit)
                )
            )
            handled = 0
            failed = 0
            first_processable_seen = False
            # 批内 turn guard 结果按 thread 缓存：被暂停的长批逐条都打 HTTP 纯属浪费；
            # 外层每轮重拉收件箱时重置，暂停/恢复最迟一个轮询间隔（5s）生效。
            turn_guard_cache: dict[str, tuple[bool, str]] = {}
            while pending:
                # 条间抢占：双工互聊批可能长达数条 × 分钟级生成，人类新消息不该排在批尾干等。
                # 每处理完一条就重拉一次收件箱，把新到的人类消息插到队首（不中断正在生成的那条）。
                preempt_human_messages(
                    api,
                    args.agent,
                    args.thread_id,
                    args.task_id,
                    transport=args.transport,
                    limit=args.limit,
                    pending=pending,
                    seen=seen,
                )
                message = pending.popleft()
                message_id = str(message.get("message_id") or "")
                if not message_id or message_id in seen or normalize_agent(message_sender(message)) == normalize_agent(args.agent):
                    continue
                human_message_waited = is_human_collaboration_agent(message_sender(message)) and first_processable_seen
                first_processable_seen = True
                if is_stale_message(message):
                    record_worker_event(
                        api,
                        args.agent,
                        message,
                        status="skipped",
                        transport=args.transport,
                        dry_run=args.dry_run,
                        error="",
                        metadata={"reason": "stale_message"},
                    )
                    mark_consumed(api, args.agent, message, args.transport)
                    seen.add(message_id)
                    continue
                if human_message_waited:
                    record_worker_event(
                        api,
                        args.agent,
                        message,
                        status="stream",
                        transport=args.transport,
                        dry_run=args.dry_run,
                        error="",
                        output="Local model is busy; human message has been queued with priority.",
                        stream="lifecycle",
                        metadata={"lifecycle": "queued", "reason": "human_priority_queue"},
                    )
                if should_skip_model_message(message, auto_reply=auto_reply):
                    record_worker_event(
                        api,
                        args.agent,
                        message,
                        status="skipped",
                        transport=args.transport,
                        dry_run=args.dry_run,
                        error="",
                        metadata={"reason": "auto_reply_disabled"},
                    )
                    mark_consumed(api, args.agent, message, args.transport)
                    seen.add(message_id)
                    continue
                if not is_human_collaboration_agent(message_sender(message)) and not is_tool_result_message(message):
                    guard_key = message_thread_id(message)
                    if guard_key not in turn_guard_cache:
                        turn_guard_cache[guard_key] = turn_allowance(api, message)
                    allowed, guard_reason = turn_guard_cache[guard_key]
                    if not allowed:
                        if guard_reason == "turn_paused":
                            # 人工暂停是挂起不是丢弃：不消费、不 seen，消息留在 bus，
                            # 恢复（refill/人类回帖）后下一轮轮询自然续上串联。
                            print(f"turn paused, deferring model message {message_id} -> {args.agent}", flush=True)
                            continue
                        # 预算用尽维持原语义：消费丢弃，等人工续杯后的新消息。
                        # 不发任何 worker 事件：桌面不渲染工作卡，思考链与回复同生共死。
                        print(f"turn cap exhausted, skipping model message {message_id} -> {args.agent}", flush=True)
                        mark_consumed(api, args.agent, message, args.transport)
                        seen.add(message_id)
                        continue
                claim_path = try_acquire_worker_message_claim(args.agent, message)
                if claim_path is None:
                    # Another worker process owns this exact agent/message. Do not
                    # emit a second started event and do not acknowledge its work.
                    continue
                try:
                    if not args.dry_run and not args.no_post and reply_already_persisted(api, args.agent, message):
                        # A prior process posted the deterministic reply but died
                        # before acknowledging the source. Consume it before any
                        # provider call so restart recovery has zero model cost.
                        mark_consumed(api, args.agent, message, args.transport)
                        seen.add(message_id)
                        consecutive_failures = 0
                        handled += 1
                        print(f"recovered existing reply {message_id} -> {args.agent}", flush=True)
                        continue
                    try:
                        record_worker_event(
                            api,
                            args.agent,
                            message,
                            status="started",
                            transport=args.transport,
                            dry_run=args.dry_run,
                            error="",
                        )
                        process_worker_message_with_retry(
                            api,
                            args.agent,
                            message,
                            assistant,
                            dry_run=args.dry_run,
                            no_post=args.no_post,
                            transport=args.transport,
                            max_attempts=max_attempts,
                            retry_backoff=args.retry_backoff,
                        )
                    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                        if is_idempotent_message_conflict(exc):
                            # The deterministic reply already exists. Treat this as a
                            # completed retry and consume the source message; otherwise
                            # every poll would invoke the external model again forever.
                            seen.add(message_id)
                            consecutive_failures = 0
                            try:
                                mark_consumed(api, args.agent, message, args.transport)
                            except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as ack_exc:
                                print(f"deduplicated reply ack failed {message_id} -> {args.agent}: {ack_exc}", file=sys.stderr, flush=True)
                            print(f"deduplicated existing reply {message_id} -> {args.agent}", flush=True)
                            handled += 1
                            continue
                        print(f"worker failed {message_id} -> {args.agent}: {exc}", file=sys.stderr, flush=True)
                        record_worker_event(
                            api,
                            args.agent,
                            message,
                            status="failed",
                            transport=args.transport,
                            dry_run=args.dry_run,
                            error=str(exc),
                        )
                        consecutive_failures += 1
                        try:
                            post_worker_failure_reply(api, args.agent, message, str(exc))
                            mark_consumed(api, args.agent, message, args.transport)
                            seen.add(message_id)
                        except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as reply_exc:
                            print(f"worker failure reply failed {message_id} -> {args.agent}: {reply_exc}", file=sys.stderr, flush=True)
                        maybe_post_self_heal_request(api, args.agent, message, str(exc), consecutive_failures)
                        failed += 1
                        continue
                    seen.add(message_id)
                    consecutive_failures = 0
                    print(f"handled {message_id} -> {args.agent}", flush=True)
                    final_status = "awaiting_tool" if message.pop("_awaiting_tool_result", False) else "processed"
                    record_worker_event(
                        api,
                        args.agent,
                        message,
                        status=final_status,
                        transport=args.transport,
                        dry_run=args.dry_run,
                        error="",
                    )
                    handled += 1
                finally:
                    release_worker_message_claim(claim_path)
            if args.once:
                if handled == 0 and failed == 0:
                    record_worker_event(
                        api,
                        args.agent,
                        {},
                        status="idle",
                        transport=args.transport,
                        dry_run=args.dry_run,
                        error="",
                        thread_id=args.thread_id,
                        task_id=args.task_id,
                    )
                return 3 if failed else 0
            if wake.wait(timeout=max(1.0, args.interval)):
                wake.clear()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            print(f"desktop collaboration API unavailable: {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 2
            time.sleep(max(2.0, args.interval))


def default_assistant_id(agent: str) -> str:
    normalized = normalize_agent(agent)
    if normalized == "codex":
        return "codex_cli"
    return normalized


def is_idempotent_message_conflict(error: BaseException | str) -> bool:
    return "message_id_conflict:" in str(error or "").strip().lower()


def should_skip_model_message(message: dict[str, Any], *, auto_reply: bool) -> bool:
    """Without the explicit auto-reply switch, only human-authored messages are processed.

    This is the cost gate: replying to a model-authored message is exactly the
    turn that could chain forever and burn API budget. The server enforces the
    same switch at persist time; skipping here avoids even making the API call.
    """
    if auto_reply or is_tool_result_message(message):
        return False
    return not is_human_collaboration_agent(message_sender(message))


def is_tool_result_message(message: dict[str, Any]) -> bool:
    """Tool results are execution continuations, not optional model-to-model chat."""

    sender = normalize_agent(message_sender(message))
    role = message_type(message).strip().lower()
    return sender.startswith("executor_") and role in {"event", "result", "tool_result"}


def tool_call_origin_message_id(message: dict[str, Any]) -> str:
    """Keep retries/continuations on the original user-message idempotency key."""

    if is_tool_result_message(message):
        envelope = message_envelope(message)
        metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
        parent_id = str(message.get("parent_message_id") or metadata.get("parent_message_id") or "").strip()
        if parent_id:
            return parent_id
    return str(message.get("message_id") or "").strip()


def message_created_at(message: dict[str, Any]) -> float:
    created = message.get("created_at")
    return float(created) if isinstance(created, (int, float)) else 0.0


def prioritize_worker_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Process human-authored messages before model-to-model continuations.

    A continuous duplex debate can keep generating model messages. A single
    local model worker is physically serial, so prioritizing human messages is
    the fairness guard that prevents a new user turn from waiting behind a long
    auto-reply chain.
    """
    return sorted(
        messages,
        key=lambda item: (
            0 if is_human_collaboration_agent(message_sender(item)) else 1,
            message_created_at(item),
            str(item.get("message_id") or ""),
        ),
    )


def preempt_human_messages(
    api: str,
    agent: str,
    thread_id: str,
    task_id: str,
    *,
    transport: str,
    limit: int,
    pending: deque[dict[str, Any]],
    seen: set[str],
) -> None:
    """条间抢占：剩余批首条不是人类消息时重拉一次收件箱，把新到的人类消息插到队首。

    只做条间（不中断正在生成的那条），"继续"最坏等待 = 当前那一条的完成时间，
    不再随批长线性增长。抢占失败（API 抖动）静默放弃，退回原批次顺序——尽力而为。
    """
    if not pending or is_human_collaboration_agent(message_sender(pending[0])):
        # 队首已是人类消息（prioritize 保证批内人类恒在模型前），无需多发一次 HTTP。
        return
    try:
        latest = list_worker_messages(api, agent, thread_id, task_id, transport=transport, limit=limit)
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return
    pending_ids = {str(item.get("message_id") or "") for item in pending}
    fresh = [
        item
        for item in latest
        if is_human_collaboration_agent(message_sender(item))
        and str(item.get("message_id") or "")
        and str(item.get("message_id") or "") not in seen
        and str(item.get("message_id") or "") not in pending_ids
        and normalize_agent(message_sender(item)) != normalize_agent(agent)
    ]
    # 逆序 appendleft → 队首按 created_at 升序，多条人类消息保持先来先服务。
    for item in sorted(fresh, key=message_created_at, reverse=True):
        pending.appendleft(item)


def turn_allowance(api: str, message: dict[str, Any]) -> tuple[bool, str]:
    """轮次预检：上限用尽时连思考都不开始。返回 (allowed, reason)。

    否则模型先"思考"完（桌面已渲染工作卡）、发布时才被 turn guard 拒收，
    留下一张没有回复的孤儿思考卡（2026-07-06 用户实测反馈：思考链跟回复
    必须一起出现）。预检失败时放行（fail-open），由后端 persist 闸门兜底。
    reason 用于区分处置：turn_paused（人工暂停 → 挂起不消费，恢复后续上）
    vs turn_cap_reached / turn_hard_cap_reached（预算用尽 → 消费丢弃）。
    """
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {"action": "turn_guard_status", "thread_id": message_thread_id(message)},
        )
    except (RuntimeError, OSError, TimeoutError) as exc:
        print(f"turn guard precheck failed (fail-open): {exc}", file=sys.stderr, flush=True)
        return True, ""
    guard = result.get("turn_guard") if isinstance(result, dict) else None
    thread = guard.get("thread") if isinstance(guard, dict) else None
    if isinstance(thread, dict) and thread.get("allowed") is False:
        return False, str(thread.get("reason") or "turn_cap_reached")
    return True, ""


def turn_allowance_ok(api: str, message: dict[str, Any]) -> bool:
    """向后兼容包装：只关心是否放行。"""
    allowed, _ = turn_allowance(api, message)
    return allowed


def collaboration_max_message_age_seconds() -> float:
    raw = str(os.environ.get("SPIRITKIN_COLLABORATION_MAX_MESSAGE_AGE", "")).strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    return value if value > 0 else 1800.0


def is_stale_message(message: dict[str, Any], *, now: float | None = None) -> bool:
    """worker 重启后不补处理陈年 backlog。

    scope=all_threads 的共享 worker 一旦重启，会把几小时前所有会话的
    未消费消息全部当作新消息回复一遍——旧会话集体"诈尸"、当前会话被
    挤到队尾（2026-07-05 实测）。超过时限的消息直接标记消费。
    """
    created = message.get("created_at")
    if not isinstance(created, (int, float)) or created <= 0:
        return False
    current = time.time() if now is None else now
    return (current - float(created)) > collaboration_max_message_age_seconds()


WORKER_MESSAGE_CLAIM_STALE_SECONDS = 1800.0


def process_id_is_running(pid: int) -> bool:
    """Return whether a claim owner still exists without signalling it."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still proves the PID exists.
        try:
            exit_code = ctypes.c_uint32()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False


def worker_message_claim_is_abandoned(path: Path) -> bool:
    """A dead owner is reclaimable immediately; age remains the corruption fallback."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        owner_pid = int(data.get("pid") or 0)
        if owner_pid > 0 and not process_id_is_running(owner_pid):
            return True
    except (OSError, ValueError, TypeError):
        pass
    try:
        return time.time() - path.stat().st_mtime > WORKER_MESSAGE_CLAIM_STALE_SECONDS
    except OSError:
        return False


def worker_message_claim_path(agent: str, message: dict[str, Any]) -> Path:
    safe_agent = re.sub(r"[^A-Za-z0-9_.-]", "_", normalize_agent(agent))[:80] or "agent"
    message_id = str(message.get("message_id") or "").strip()
    safe_message = re.sub(r"[^A-Za-z0-9_.-]", "_", message_id)[:160] or "message"
    root = resolve_collaboration_root() / "worker_claims" / safe_agent
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_message}.lock"


def try_acquire_worker_message_claim(agent: str, message: dict[str, Any]) -> tuple[Path, str] | None:
    """Cross-process lease for one logical agent/message execution."""

    if not str(message.get("message_id") or "").strip():
        return None
    path = worker_message_claim_path(agent, message)
    token = f"{os.getpid()}-{time.time_ns()}"
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if worker_message_claim_is_abandoned(path):
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"token": token, "pid": os.getpid(), "claimed_at": time.time()}, handle)
        return path, token


def release_worker_message_claim(claim: tuple[Path, str] | None) -> None:
    if claim is None:
        return
    path, token = claim
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if str(data.get("token") or "") != token:
            return
        path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass


def resolve_push_ws_url(explicit: str = "") -> str:
    if str(explicit or "").strip():
        return str(explicit).strip()
    try:
        from backend.app.runtime import resolve_event_sink_url

        return str(resolve_event_sink_url() or "").strip()
    except Exception as exc:  # noqa: BLE001 - push is optional; never block the poll loop on it.
        print(f"push url resolution failed: {exc}", file=sys.stderr, flush=True)
        return ""


def should_wake_for_event(event: Any, agent: str) -> bool:
    """True when a pushed event is a collaboration message this agent should fetch."""
    if not isinstance(event, dict) or str(event.get("type") or "") != "collaboration.message":
        return False
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    me = normalize_agent(agent)
    if normalize_agent(str(payload.get("from_agent") or "")) == me:
        return False
    recipients = payload.get("to_agents")
    if isinstance(recipients, str):
        recipients = [recipients]
    if not isinstance(recipients, (list, tuple)) or not recipients:
        return True
    normalized = {normalize_agent(str(item)) for item in recipients if str(item).strip()}
    return "all" in normalized or me in normalized


def start_push_listener(ws_url: str, agent: str, wake: threading.Event) -> threading.Thread:
    """Subscribe to the event bridge and set ``wake`` on relevant messages.

    Best-effort by design: any connect/read failure falls back to interval
    polling in the main loop, reconnecting with capped exponential backoff.
    """

    def _run() -> None:
        try:
            import asyncio

            import websockets
        except ImportError as exc:
            print(f"push trigger disabled (websockets unavailable): {exc}", file=sys.stderr, flush=True)
            return

        async def _listen() -> None:
            delay = 1.0
            while True:
                try:
                    async with websockets.connect(ws_url, open_timeout=10) as ws:
                        token = str(os.getenv("SPIRITKIN_DESKTOP_TOKEN") or os.getenv("SPIRITKIN_API_TOKEN") or os.getenv("SPIRITKIN_MOBILE_TOKEN") or "").strip()
                        await ws.send(json.dumps({"type": "runtime.auth", "token": token}))
                        delay = 1.0
                        async for frame in ws:
                            try:
                                data = json.loads(frame)
                            except (TypeError, ValueError):
                                continue
                            events = data if isinstance(data, list) else [data]
                            if any(should_wake_for_event(item, agent) for item in events):
                                wake.set()
                except Exception as exc:  # noqa: BLE001 - reconnect on any bridge failure; polling still covers delivery.
                    print(f"push trigger reconnect in {delay:.0f}s: {exc}", file=sys.stderr, flush=True)
                    await asyncio.sleep(delay)
                    delay = min(30.0, delay * 2)

        asyncio.run(_listen())

    thread = threading.Thread(target=_run, name="collaboration-push-listener", daemon=True)
    thread.start()
    return thread


def normalize_agent(agent: str) -> str:
    key = "".join(ch for ch in agent.lower() if ch.isalnum())
    return {
        "claudecode": "claude_code",
        "claude": "claude_code",
        "codexcli": "codex",
    }.get(key, agent.strip().lower().replace("-", "_"))


def request_json(api: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{api}{path}", data=data, method="GET" if data is None else "POST")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    token = os.getenv("SPIRITKIN_MOBILE_TOKEN", "").strip()
    if token:
        req.add_header("X-SpiritKin-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        # 网关的 4xx/5xx 响应体里带着真正的拒绝原因（如 turn_cap_reached），
        # 不读出来就只剩 "HTTP Error 400: Bad Request"，没法定位也没法分流。
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail[:400] or exc.reason}") from exc
    if not result.get("ok", True):
        raise RuntimeError(f"{result.get('error', 'request failed')}: {result.get('detail', '')}")
    return result


def list_inbox(api: str, agent: str, thread_id: str, task_id: str, *, include_read: bool, limit: int) -> list[dict[str, Any]]:
    result = request_json(
        api,
        "/desktop/collaboration",
        {
            "action": "list_messages",
            "to_agent": normalize_agent(agent),
            "thread_id": thread_id,
            "task_id": task_id,
            "include_read": include_read,
            "limit": limit,
        },
    )
    return [dict(item) for item in result.get("messages") or [] if isinstance(item, dict)]


def list_route_bus_inbox(api: str, agent: str, thread_id: str, task_id: str, *, limit: int) -> list[dict[str, Any]]:
    result = request_json(
        api,
        "/desktop/collaboration",
        {
            "action": "list_agent_route_bus_messages",
            "to_agent": normalize_agent(agent),
            "consumer": normalize_agent(agent),
            "thread_id": thread_id,
            "task_id": task_id,
            "include_acked": False,
            "include_audit": False,
            "limit": limit,
        },
    )
    route_bus = result.get("agent_route_bus_messages") or {}
    return [dict(item) for item in route_bus.get("messages") or [] if isinstance(item, dict)]


def list_worker_messages(api: str, agent: str, thread_id: str, task_id: str, *, transport: str, limit: int) -> list[dict[str, Any]]:
    if transport == "legacy_inbox":
        return list_inbox(api, agent, thread_id, task_id, include_read=False, limit=limit)
    return list_route_bus_inbox(api, agent, thread_id, task_id, limit=limit)


def process_worker_message(
    api: str,
    agent: str,
    message: dict[str, Any],
    assistant: dict[str, Any],
    *,
    dry_run: bool,
    no_post: bool,
    transport: str,
) -> str:
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        error="",
        output=f"Loaded collaboration context for {normalize_agent(agent)}.",
        stream="lifecycle",
        metadata={"lifecycle": "context_loaded", "context_pack_path": message_context_pack_path(message)},
    )
    if dry_run or not api or transport != "route_bus":
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            error="",
            output=f"Prompt ready for {normalize_agent(agent)}.",
            stream="lifecycle",
            metadata={
                "lifecycle": "prompt_ready",
                "assistant_id": str(assistant.get("assistant_id") or ""),
                "prompt_chars": len(build_prompt(message, assistant)),
                "dry_run": dry_run,
            },
        )
    # 发言定序（2026-07-08 三次迭代，用户裁决）：谁先想完谁先发言。
    # 同轮收件人各自并行思考；第一个产出可见正文的参与者抢到发言席位现场直播（token 上气泡），
    # 其余自动转后台起草（token 改道思考泳道），前位定稿后拿"草稿+定稿"修订成稿再上屏。
    # 不再按收件/进队时间定序——那会让快模型（DeepSeek）给慢的本地模型陪跑。
    queue_dir: Path | None = None
    turn_lock: Path | None = None
    speak_slot: SpeakSlot | None = None
    turn_deferred = False
    generation_message = message
    if not no_post and not dry_run:
        queue_dir, queue_peers = enter_speak_queue(api, agent, message, transport=transport)
        if queue_dir is None:
            # 队列没接上（互聊轮来件只有自己一个模型收件人）→ 发言权锁伪轮流制：
            # 非阻塞抢锁，抢到就把更新定稿并入上下文一次成稿；没抢到先并行起草。
            turn_lock, prior_replies, turn_deferred = enter_speak_turn(api, agent, message, transport=transport)
            if prior_replies:
                generation_message = build_speak_after_message(message, prior_replies)
        else:
            speak_slot = SpeakSlot(api, agent, message, queue_dir, queue_peers, transport)
    try:
        if turn_deferred:
            # 后台起草：token 流改道 reasoning 泳道（run_api_assistant 依此标记分流），
            # 正文气泡只留给轮到自己时的最终成稿。
            generation_message["_background_draft"] = True
        if speak_slot is not None:
            # 队列轮的泳道由席位在首个可见正文产出时现场判定（谁先想完谁直播）。
            generation_message["_speak_slot"] = speak_slot
        reply = (
            build_dry_run_reply(agent, message)
            if dry_run
            else run_external_assistant(
                assistant,
                generation_message,
                api=api,
                agent=agent,
                transport=transport,
                dry_run=dry_run,
            )
        )
        generation_message.pop("_background_draft", None)
        generation_message.pop("_speak_slot", None)
        if generation_message is not message and generation_message.pop("_salvaged_reply", False):
            message["_salvaged_reply"] = True
        if (
            not dry_run
            and not no_post
            and not message.get("_salvaged_reply")
            and collaboration_derail_retry_enabled()
            and looks_like_meta_derailed_reply(reply)
        ):
            # 修O：脱轨自愈——重掷一次并附纠偏指令；重试仍脱轨则原样发布（人工可见，不静默丢弃）。
            record_worker_event(
                api,
                agent,
                message,
                status="stream",
                transport=transport,
                dry_run=dry_run,
                error="",
                output="Reply looks meta-derailed (echoes prompt constraints/self-diagnosis); regenerating once.",
                stream="lifecycle",
                metadata={"lifecycle": "derail_retry"},
            )
            generation_message["_derail_retry"] = True
            retry_reply = run_external_assistant(
                assistant,
                generation_message,
                api=api,
                agent=agent,
                transport=transport,
                dry_run=dry_run,
            )
            generation_message.pop("_derail_retry", None)
            # 重试若走了抖救路径（拿到的是思考链片段），不能当正式回帖采纳。
            retry_salvaged = bool(generation_message.pop("_salvaged_reply", False))
            if (
                not retry_salvaged
                and str(retry_reply or "").strip()
                and not looks_like_meta_derailed_reply(retry_reply)
            ):
                reply = retry_reply
        queue_ahead: list[str] = []
        if speak_slot is not None:
            # 全程没流出可见正文（非流式回退等）：生成结束时补一次席位判定。
            speak_slot.claim()
            queue_ahead = speak_slot.ahead
        background_draft = bool(queue_ahead) or turn_deferred
        if background_draft and not no_post:
            reply, turn_lock = revise_with_finalized_replies(
                api,
                agent,
                message,
                assistant,
                reply,
                queue_dir=queue_dir if queue_ahead else None,
                queue_ahead=queue_ahead,
                acquire_turn=turn_deferred,
                transport=transport,
            )
        if not no_post:
            salvaged_reply = bool(message.pop("_salvaged_reply", False))
            submitted_tool_calls = 0
            if not dry_run and not salvaged_reply:
                # 抖救内容是思考链片段，不当作正式发言解析工具调用。
                # 工具调用解析要在去 Markdown 之前：```json 围栏是解析依据。
                submitted_tool_calls = submit_tool_calls_from_reply(api, agent, message, reply, transport=transport, dry_run=dry_run)
                if submitted_tool_calls:
                    reply = strip_submitted_tool_call_payloads(reply, submitted_tool_calls)
            defer_reply_until_tool_result = submitted_tool_calls > 0 and not is_tool_result_message(message)
            if defer_reply_until_tool_result:
                # A tool call and its continuation form one assistant turn. Posting
                # this pre-execution text creates a fake reply during the permission
                # gate and closes the work card before the executor result arrives.
                message["_awaiting_tool_result"] = True
            if not dry_run:
                # 协作气泡不渲染 Markdown：去掉 #/*/加粗等装饰符（代码围栏内内容原样保留）。
                reply = strip_markdown_decorations(reply)
            if not dry_run and defer_reply_until_tool_result:
                record_worker_event(
                    api,
                    agent,
                    message,
                    status="stream",
                    transport=transport,
                    dry_run=dry_run,
                    error="",
                    output="Waiting for the submitted tool call to finish before posting a reply.",
                    stream="lifecycle",
                    metadata={"lifecycle": "awaiting_tool_result", "tool_calls": submitted_tool_calls},
                )
            elif not dry_run:
                record_worker_event(
                    api,
                    agent,
                    message,
                    status="stream",
                    transport=transport,
                    dry_run=dry_run,
                    error="",
                    output=f"Posting external agent reply ({len(reply)} chars).",
                    stream="lifecycle",
                    metadata={"lifecycle": "reply_posting", "reply_chars": len(reply), "salvaged": salvaged_reply},
                )
            if not defer_reply_until_tool_result:
                post_reply(
                    api,
                    agent,
                    message,
                    reply,
                    recipients=failure_reply_recipients(agent, message) if salvaged_reply else None,
                    transport=transport,
                    dry_run=dry_run,
                )
            # 无论发布成功还是被后端闸门拒收，都要标记定稿，避免队列后位空等到超时。
            # Waiting on a tool still releases the speaking slot; the executor
            # continuation will publish the final text for this same turn.
            mark_speak_queue_posted(queue_dir, agent)
            mark_consumed(api, agent, message, transport)
            if not dry_run and not defer_reply_until_tool_result:
                record_worker_event(
                    api,
                    agent,
                    message,
                    status="stream",
                    transport=transport,
                    dry_run=dry_run,
                    error="",
                    output=f"Acknowledged collaboration route message {message.get('message_id') or 'message'}.",
                    stream="lifecycle",
                    metadata={"lifecycle": "acked"},
                )
    except Exception:
        # 生成/发布失败：撤出队列登记，别让后位空等到超时（等待方把缺席条目视为已完成）。
        withdraw_speak_queue_entry(queue_dir, agent)
        raise
    finally:
        generation_message.pop("_background_draft", None)
        generation_message.pop("_speak_slot", None)
        release_speak_turn_lock(turn_lock)
    return reply


def collaboration_derail_retry_enabled() -> bool:
    """Opt-in only: a second generation can repeat provider/CLI side effects."""

    raw = str(os.environ.get("SPIRITKIN_COLLABORATION_DERAIL_RETRY", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def process_worker_message_with_retry(
    api: str,
    agent: str,
    message: dict[str, Any],
    assistant: dict[str, Any],
    *,
    dry_run: bool,
    no_post: bool,
    transport: str,
    max_attempts: int,
    retry_backoff: float,
) -> str:
    attempts = max(1, max_attempts)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return process_worker_message(
                api,
                agent,
                message,
                assistant,
                dry_run=dry_run,
                no_post=no_post,
                transport=transport,
            )
        except WorkerConfigurationError:
            raise
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            last_exc = exc
            if attempt >= attempts:
                raise
            record_worker_event(
                api,
                agent,
                message,
                status="stream",
                transport=transport,
                dry_run=dry_run,
                error=str(exc),
                output=f"Attempt {attempt}/{attempts} failed; retrying.",
                stream="lifecycle",
                metadata={"lifecycle": "retry", "attempt": attempt, "max_attempts": attempts},
            )
            print(
                f"worker retry {message.get('message_id') or 'message'} -> {agent} "
                f"(attempt {attempt}/{attempts}): {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(max(0.0, retry_backoff) * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("process_worker_message_with_retry exhausted without result")


FAN_OUT_MODEL_LIMIT = 3


def fetch_thread_participants(api: str, thread_id: str) -> list[str]:
    """当前会话参与者 = 该线程最近一条人类消息的收件人。

    人类每次发送都会把消息发给会话当前成员（本地主模型 + @ 过的模型），
    所以这份收件人列表就是会话参与者的权威快照；成员被移除后人类的下一条
    消息不再包含它，双工扇出也随之收敛。查询失败返回空表，由调用方回退。
    """
    normalized_thread = str(thread_id or "").strip()
    if not normalized_thread:
        return []
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {"action": "list_messages", "thread_id": normalized_thread, "limit": 60},
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return []
    messages = result.get("messages")
    if not isinstance(messages, list):
        return []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if not is_human_collaboration_agent(message_sender(message)):
            continue
        raw = message.get("to_agents")
        targets = raw if isinstance(raw, (list, tuple)) else []
        participants: list[str] = []
        for target in targets:
            normalized = normalize_agent(str(target))
            if normalized and normalized != "all" and normalized not in participants:
                participants.append(normalized)
        return participants
    return []


def reply_recipients(agent: str, parent: dict[str, Any], participants: list[str] | None = None) -> list[str]:
    """回复收件人 = 人类发送者 ∪ (当前会话参与者 − 自己)。

    双工场景下同一条人类消息发给多个模型，各模型的回复也要让其他成员看到；
    是否真的允许 model→model answer 落库由后端 auto-reply 闸门兜底。
    优先用 participants（线程最近人类消息的收件人 = 当前会话成员），
    没有时才回退到父消息收件人推导。
    """
    me = normalize_agent(agent)
    recipients: list[str] = []
    sender = normalize_agent(message_sender(parent))
    if sender and sender != me:
        recipients.append(sender)
    if participants:
        for target in participants:
            normalized = normalize_agent(target)
            if not normalized or normalized in {me, "all"} or normalized in recipients:
                continue
            recipients.append(normalized)
        return recipients or ["human_desktop"]
    parent_targets: list[str] = []
    raw_recipients = parent.get("to_agents")
    if isinstance(raw_recipients, (list, tuple)):
        parent_targets.extend(str(item) for item in raw_recipients)
    parent_targets.extend(message_recipient(parent).split(","))
    envelope = message_envelope(parent)
    metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
    meta_targets = metadata.get("to_agents")
    if isinstance(meta_targets, (list, tuple)):
        parent_targets.extend(str(item) for item in meta_targets)
    for target in parent_targets:
        normalized = normalize_agent(target)
        if not normalized or normalized in {me, "all"} or normalized in recipients:
            continue
        recipients.append(normalized)
    # 广播熔断：父消息收件人太多（"发给全体"式广播）时不逐一回敬，
    # 否则 N 个模型互相扇出会形成 N² 消息风暴，把共享 worker 队列
    # 全部占满，拖慢所有会话（2026-07-05 实测 13 收件人风暴）。
    model_recipients = [item for item in recipients if not is_human_collaboration_agent(item)]
    if len(model_recipients) > FAN_OUT_MODEL_LIMIT:
        recipients = [item for item in recipients if is_human_collaboration_agent(item)]
    return recipients or ["human_desktop"]


def reply_message_id(agent: str, parent: dict[str, Any]) -> str:
    parent_id = tool_call_origin_message_id(parent)
    if not parent_id:
        return ""
    safe_agent = re.sub(r"[^a-zA-Z0-9_.-]+", "-", normalize_agent(agent)).strip("-") or "agent"
    safe_parent = re.sub(r"[^a-zA-Z0-9_.-]+", "-", parent_id).strip("-") or "message"
    return f"reply-{safe_agent}-{safe_parent}"


def reply_already_persisted(api: str, agent: str, parent: dict[str, Any]) -> bool:
    expected_id = reply_message_id(agent, parent)
    thread_id = message_thread_id(parent)
    if not expected_id or not thread_id:
        return False
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {
                "action": "list_messages",
                "thread_id": thread_id,
                "include_read": True,
                "limit": 200,
            },
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        # A preflight lookup must not make healthy message processing unavailable.
        return False
    messages = result.get("messages") if isinstance(result, dict) else []
    return any(
        str(item.get("message_id") or "").strip() == expected_id
        for item in (messages or [])
        if isinstance(item, dict)
    )


def post_reply(
    api: str,
    agent: str,
    parent: dict[str, Any],
    content: str,
    *,
    recipients: list[str] | None = None,
    transport: str = "",
    dry_run: bool = False,
) -> None:
    if recipients is None:
        participants = fetch_thread_participants(api, message_thread_id(parent))
        recipients = reply_recipients(agent, parent, participants)
    recipients = [
        item for item in recipients
        if not normalize_agent(str(item)).startswith("executor_")
    ] or ["human_desktop"]
    payload = {
        "action": "post_message",
        "task_id": message_task_id(parent),
        "thread_id": message_thread_id(parent),
        "from_agent": normalize_agent(agent),
        "to_agents": list(recipients or ["human_desktop"]),
        "message_id": reply_message_id(agent, parent),
        "parent_message_id": tool_call_origin_message_id(parent),
        "role": "answer",
        "content": content,
        "context_pack_path": message_context_pack_path(parent),
        "include_collaboration": False,
    }
    transport_attempt = 0
    removed_recipients: set[str] = set()
    while True:
        try:
            request_json(api, "/desktop/collaboration", payload)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            # Retrying the stable reply message_id is safe: the gateway returns the
            # already-persisted message if the first response was lost. Never bubble
            # a transient post failure into the full model-generation retry loop.
            transport_attempt += 1
            if transport_attempt >= 3:
                raise
            time.sleep(float(transport_attempt))
            continue
        except RuntimeError as exc:
            text = str(exc)
            invalid_match = re.search(r"recipient_not_allowed:([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
            if invalid_match:
                invalid = normalize_agent(invalid_match.group(1))
                if invalid and invalid not in removed_recipients:
                    removed_recipients.add(invalid)
                    filtered = [
                        item
                        for item in payload["to_agents"]
                        if normalize_agent(str(item)) != invalid
                    ]
                    payload["to_agents"] = filtered or ["human_desktop"]
                    record_worker_event(
                        api,
                        agent,
                        parent,
                        status="stream",
                        transport=transport,
                        dry_run=dry_run,
                        error=text,
                        output=f"Removed non-chat recipient {invalid}; retrying the same reply delivery.",
                        stream="lifecycle",
                        metadata={"lifecycle": "recipient_filtered", "recipient": invalid},
                    )
                    continue
        # 后端策略性拒收（轮次上限/双工关闭）是正常闸门行为，不是故障：
        # 静默丢弃这条回复即可。当成失败会触发重试 + "暂时无法处理"回执刷屏。
            lifecycle = ""
            if "turn_cap_reached" in text:
                lifecycle = "turn_cap_reached"
            elif "turn_hard_cap_reached" in text:
                lifecycle = "turn_hard_cap_reached"
            elif "turn_paused" in text:
                lifecycle = "turn_paused"
            elif "auto_reply_disabled" in text:
                lifecycle = "auto_reply_disabled"
            if lifecycle == "auto_reply_disabled" and not any(
                is_human_collaboration_agent(item) for item in payload["to_agents"]
            ):
                # Keep the generated reply and only change its delivery target.
                payload["to_agents"] = ["human_desktop"]
                continue
            if lifecycle:
                print(f"reply suppressed by gateway policy for {agent}: {text[:160]}", flush=True)
                record_worker_event(
                    api,
                    agent,
                    parent,
                    status="stream",
                    transport=transport,
                    dry_run=dry_run,
                    error=text,
                    output=lifecycle,
                    stream="lifecycle",
                    metadata={"lifecycle": lifecycle},
                )
                return
            raise


# ---- 发言队列（2026-07-08 三次迭代，用户裁决"谁先想完谁先发言"）----
# 同一条来件触发多个模型回复时：各自并行思考；第一个产出可见正文的参与者
# 抢到发言席位（claim_speak_slot 写 speaking_at）现场直播，其余转后台起草
# （token 改道思考泳道），等前位定稿后修订发言再上屏——已发出的发言绝不回收重发。
# 跨 worker 进程经协作 state 根下的 speak_queue 目录协调，
# 每个 agent 写自己的登记文件，无锁竞争。轮与轮之间仍由后端 turn guard 限次。

SPEAK_QUEUE_POLL_SECONDS = 1.5


def collaboration_speak_queue_enabled() -> bool:
    raw = str(os.environ.get("SPIRITKIN_COLLABORATION_SPEAK_QUEUE", "")).strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def speak_queue_timeout_seconds() -> float:
    raw = str(os.environ.get("SPIRITKIN_COLLABORATION_SPEAK_QUEUE_TIMEOUT", "")).strip()
    try:
        value = float(raw)
    except ValueError:
        # 先排队后发言：后位要等前位整段生成+发布（本地慢模型可达数分钟），
        # 默认超时相应放宽；超时仍放行发布，不死锁。
        return 600.0
    return max(10.0, value) if value > 0 else 600.0


def speak_queue_peers(agent: str, message: dict[str, Any]) -> list[str]:
    """同一条来件的其他模型收件人 = 发言队列同伴（人类、自己、all 除外）。"""
    me = normalize_agent(agent)
    targets: list[str] = []
    raw = message.get("to_agents")
    if isinstance(raw, (list, tuple)):
        targets.extend(str(item) for item in raw)
    targets.extend(message_recipient(message).split(","))
    peers: list[str] = []
    for target in targets:
        normalized = normalize_agent(target)
        if not normalized or normalized in {me, "all"} or is_human_collaboration_agent(normalized) or normalized in peers:
            continue
        peers.append(normalized)
    return peers


def speak_queue_dir(round_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(round_key or ""))[:80] or "round"
    return resolve_collaboration_root() / "speak_queue" / safe


def prune_speak_queue(root: Path, *, max_age_seconds: float = 3600.0) -> None:
    try:
        children = list(root.iterdir())
    except OSError:
        return
    cutoff = time.time() - max_age_seconds
    for child in children:
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def register_speak_queue_entry(agent: str, round_key: str) -> Path:
    """生成前进队登记：进队时间即发言顺序（先排队后发言）。"""
    queue_dir = speak_queue_dir(round_key)
    prune_speak_queue(queue_dir.parent)
    queue_dir.mkdir(parents=True, exist_ok=True)
    entry = queue_dir / f"{normalize_agent(agent)}.json"
    payload = {"agent": normalize_agent(agent), "enqueued_at": time.time()}
    entry.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return queue_dir


def withdraw_speak_queue_entry(queue_dir: Path | None, agent: str) -> None:
    """生成/发布失败时撤出队列，让后位把该席位视为已完成、不再空等。"""
    if queue_dir is None:
        return
    try:
        (queue_dir / f"{normalize_agent(agent)}.json").unlink(missing_ok=True)
    except OSError:
        pass
    first_speaker = queue_dir / ".first-speaker"
    try:
        if first_speaker.read_text(encoding="utf-8").strip() == normalize_agent(agent):
            first_speaker.unlink(missing_ok=True)
    except OSError:
        pass


def load_speak_queue_entries(queue_dir: Path) -> list[dict[str, Any]]:
    try:
        files = list(queue_dir.glob("*.json"))
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("agent") or "").strip():
            entries.append(data)
    entries.sort(key=lambda item: (float(item.get("enqueued_at") or item.get("draft_done_at") or 0.0), str(item.get("agent") or "")))
    return entries


def mark_speak_queue_posted(queue_dir: Path | None, agent: str) -> None:
    if queue_dir is None:
        return
    entry = queue_dir / f"{normalize_agent(agent)}.json"
    try:
        data = json.loads(entry.read_text(encoding="utf-8")) if entry.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("agent", normalize_agent(agent))
    data["posted_at"] = time.time()
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def wait_speak_queue_ahead_posted(queue_dir: Path, ahead: list[str]) -> None:
    """等待前位全部定稿；前位撤出（生成失败）视为已完成；超时放行，避免死锁。"""
    deadline = time.time() + speak_queue_timeout_seconds()
    wanted = [normalize_agent(item) for item in ahead]
    while time.time() < deadline:
        entries = {normalize_agent(str(item.get("agent") or "")): item for item in load_speak_queue_entries(queue_dir)}
        if all(
            name not in entries or float(entries[name].get("posted_at") or 0.0) > 0.0
            for name in wanted
        ):
            return
        time.sleep(SPEAK_QUEUE_POLL_SECONDS)


def fetch_round_replies(api: str, thread_id: str, round_key: str, authors: list[str]) -> list[tuple[str, str]]:
    """前位的定稿发言 = 已落库、parent 指向本轮来件、发件人在前位名单里的回复。"""
    normalized_thread = str(thread_id or "").strip()
    wanted = {normalize_agent(item) for item in authors}
    if not normalized_thread or not round_key or not wanted:
        return []
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {"action": "list_messages", "thread_id": normalized_thread, "limit": 100},
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return []
    messages = result.get("messages")
    if not isinstance(messages, list):
        return []
    replies: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("parent_message_id") or "").strip() != round_key:
            continue
        sender = normalize_agent(message_sender(message))
        if sender not in wanted:
            continue
        content = message_content(message)
        if content and not is_salvaged_reply_content(content):
            replies.append((sender, content))
    return replies


SPEAK_QUEUE_REVISION_INPUT_MAX_CHARS = 1500


def _clip_revision_text(text: str, limit: int = SPEAK_QUEUE_REVISION_INPUT_MAX_CHARS) -> str:
    """压缩传递：超长时保头（论点/开头）+ 保尾（结论），截中间。"""
    body = str(text or "")
    if len(body) <= limit:
        return body
    head = int(limit * 0.7)
    tail = limit - head
    return body[:head] + "\n…（中间部分已截断）…\n" + body[-tail:]


def build_speak_after_message(message: dict[str, Any], replies: list[tuple[str, str]], draft: str = "") -> dict[str, Any]:
    """后位发言者的生成来件 = 原始来件 + 前位定稿（+ 自己的并行草稿）。
    输出即最终发言，一次成稿——已上屏的发言绝不回收重发。"""
    # 定稿/草稿截长：不截的话很容易把本地模型 4096 的上下文窗口撑爆（HTTP 400）。
    block = "\n\n".join(f"【{sender} 的定稿发言】\n{_clip_revision_text(content)}" for sender, content in replies)
    draft_block = f"\n\n【你的思考草稿（未发表，读者看不到）】\n{_clip_revision_text(draft)}" if draft.strip() else ""
    content = (
        f"{message_content(message)}\n\n"
        "——发言顺序说明——\n"
        "以下参与者已先发言（定稿）。请在此基础上直接给出你的发言：避免重复已说清楚的部分、"
        "补充你的差异化观点、如有分歧请明确指出。"
        + ("你可以吸收自己草稿里的观点，但输出的是一份全新的正式发言。" if draft_block else "")
        + "\n硬性要求：不要复述或转述其他参与者的论点原文（需要反驳时一句话点到即可）；"
        "不要出现“综上”“根据以上发言”“我同意上述”等把自己放在跟帖位置的开场；"
        "直接以你自己的身份和立场发言。\n\n"
        f"{block}{draft_block}"
    )
    revised = dict(message)
    revised["content"] = content
    envelope = message.get("agent_envelope")
    if isinstance(envelope, dict):
        env = dict(envelope)
        env["content"] = content
        revised["agent_envelope"] = env
    return revised


def revise_with_finalized_replies(
    api: str,
    agent: str,
    message: dict[str, Any],
    assistant: dict[str, Any],
    draft: str,
    *,
    queue_dir: Path | None,
    queue_ahead: list[str],
    acquire_turn: bool,
    transport: str,
) -> tuple[str, Path | None]:
    """并行草稿完成后轮到自己发言：等前位定稿/抢发言权，把定稿+草稿修订一次成稿。
    修订稿才首次流式上正文气泡。返回 (最终发言, 持有的发言权锁或 None)。"""
    turn_lock: Path | None = None
    replies: list[tuple[str, str]] = []
    if queue_dir is not None and queue_ahead:
        wait_speak_queue_ahead_posted(queue_dir, queue_ahead)
        round_key = str(message.get("message_id") or "").strip()
        replies = fetch_round_replies(api, message_thread_id(message), round_key, queue_ahead)
    elif acquire_turn:
        # 草稿已备好，这里才真正排队等发言权（阻塞、超时放行）。
        turn_lock = acquire_speak_turn_lock(agent, message_thread_id(message))
        created = message.get("created_at")
        since = float(created) if isinstance(created, (int, float)) and float(created) > 0 else 0.0
        replies = fetch_thread_replies_since(api, message_thread_id(message), since=since, exclude_agent=agent)
    if not replies:
        return draft, turn_lock
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=False,
        error="",
        output=f"Finalizing speech from the parallel draft and {len(replies)} finalized replies.",
        stream="lifecycle",
        metadata={"lifecycle": "queue_context" if queue_dir is not None else "turn_context", "sources": [sender for sender, _ in replies]},
    )
    revision_message = build_speak_after_message(message, replies, draft=draft)
    try:
        revised = run_external_assistant(
            assistant,
            revision_message,
            api=api,
            agent=agent,
            transport=transport,
            dry_run=False,
        )
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=False,
            error=str(exc),
            output="Finalizing pass failed; posting the parallel draft.",
            stream="lifecycle",
            metadata={"lifecycle": "queue_revision_failed" if queue_dir is not None else "turn_revision_failed"},
        )
        return draft, turn_lock
    revised = (revised or "").strip()
    if revised:
        # 成稿成功后以成稿轮的抖救状态为准（草稿的抖救标记随之作废）。
        message.pop("_salvaged_reply", None)
        if revision_message.pop("_salvaged_reply", False):
            message["_salvaged_reply"] = True
    return (revised or draft), turn_lock


def enter_speak_queue(
    api: str,
    agent: str,
    message: dict[str, Any],
    *,
    transport: str,
) -> tuple[Path | None, list[str]]:
    """生成前进队登记（不定序、不阻塞）。发言顺序不再按进队时间——
    收件顺序基本随机，会让快模型给慢模型陪跑（2026-07-08 用户实测否决）。
    改为"谁先开始产出正文谁先发言"：各自并行思考，第一个产出可见正文的参与者
    经 claim_speak_slot 抢到席位现场直播；其余转后台起草，前位定稿后修订发言。
    返回 (队列目录或 None, 本轮同伴名单)。"""
    if not api or not collaboration_speak_queue_enabled():
        return None, []
    round_key = str(message.get("message_id") or "").strip()
    peers = speak_queue_peers(agent, message)
    if not round_key or not peers:
        return None, []
    queue_dir = register_speak_queue_entry(agent, round_key)
    return queue_dir, peers


def claim_speak_slot(queue_dir: Path, agent: str, peers: list[str]) -> list[str]:
    """开始产出正文时抢发言席位：原子首位标记 + speaking_at 排后续顺序。
    返回排在自己前面的同伴（含已定稿的）；空列表 = 自己第一个想完，现场直播。
    独占创建 .first-speaker 保证同一时钟 tick 内也不会由 agent 字典序反转首位。"""
    me = normalize_agent(agent)
    queue_dir.mkdir(parents=True, exist_ok=True)
    first_speaker_path = queue_dir / ".first-speaker"
    first_speaker = ""
    try:
        with first_speaker_path.open("x", encoding="utf-8") as handle:
            handle.write(me)
        first_speaker = me
    except FileExistsError:
        try:
            first_speaker = normalize_agent(first_speaker_path.read_text(encoding="utf-8"))
        except OSError:
            first_speaker = ""
    except OSError:
        first_speaker = ""
    entry = queue_dir / f"{me}.json"
    try:
        data = json.loads(entry.read_text(encoding="utf-8")) if entry.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("agent", me)
    data["speaking_at"] = time.time()
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
        entry.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    my_key = (float(data["speaking_at"]), me)
    peer_set = {normalize_agent(item) for item in peers}
    ahead: list[str] = []
    if first_speaker and first_speaker != me and first_speaker in peer_set:
        ahead.append(first_speaker)
    for item in load_speak_queue_entries(queue_dir):
        name = normalize_agent(str(item.get("agent") or ""))
        if name == me or name not in peer_set or name in ahead:
            continue
        if float(item.get("posted_at") or 0.0) > 0.0:
            ahead.append(name)
            continue
        speaking = item.get("speaking_at")
        if isinstance(speaking, (int, float)) and (float(speaking), name) < my_key:
            ahead.append(name)
    return ahead


class SpeakSlot:
    """草稿期发言席位（谁先想完谁先发言）：第一个产出可见正文的参与者现场直播
    （token 上正文气泡），其余按后台草稿处理（token 改道思考泳道），
    等前位定稿后由 revise_with_finalized_replies 修订发言。claim 幂等、线程安全
    （首次判定发生在流式批量上报线程）。"""

    def __init__(
        self,
        api: str,
        agent: str,
        message: dict[str, Any],
        queue_dir: Path,
        peers: list[str],
        transport: str,
    ) -> None:
        self._api = api
        self._agent = agent
        self._message = message
        self._queue_dir = queue_dir
        self._peers = peers
        self._transport = transport
        self._mutex = threading.Lock()
        self._resolved = False
        self._live = False
        self._ahead: list[str] = []

    @property
    def queue_dir(self) -> Path:
        return self._queue_dir

    @property
    def ahead(self) -> list[str]:
        return list(self._ahead)

    def lane(self) -> str:
        """流式 token 的泳道：抢到席位走正文气泡，否则改道起草泳道（与真思考链分离）。"""
        return "token" if self.claim() else "draft"

    def claim(self) -> bool:
        with self._mutex:
            if self._resolved:
                return self._live
            self._ahead = claim_speak_slot(self._queue_dir, self._agent, self._peers)
            self._live = not self._ahead
            self._resolved = True
            live = self._live
            ahead = list(self._ahead)
        try:
            record_worker_event(
                self._api,
                self._agent,
                self._message,
                status="stream",
                transport=self._transport,
                dry_run=False,
                error="",
                output=(
                    "Claimed the speaking slot; streaming live."
                    if live
                    else f"Peers spoke first ({', '.join(ahead)}); drafting in background and will revise after they post."
                ),
                stream="lifecycle",
                metadata={"lifecycle": "queue_live" if live else "queue_wait", "ahead": ahead},
            )
        except Exception:
            pass
        return live


# 发言权锁：双工互聊轮里每个模型收到的来件不同（各自回对方的上一条），
# speak_queue 按来件收件人算同伴时 peers=[]，排不上队 → 两边交叉开火。
# 补一把 thread 级文件锁：生成前先抢锁（先排队后发言），锁内把比自己来件更新的
# 定稿发言并入生成上下文一次成稿，发布完再放锁 —— 发言一个个来（伪轮流制）。
# 锁现在跨整段生成持有（先排队后发言），本地慢模型一轮生成可达数分钟，
# 残留判定放宽到 15 分钟，避免正常生成中被同伴当僵尸锁接管。
SPEAK_TURN_LOCK_STALE_SECONDS = 900.0
SPEAK_TURN_POLL_SECONDS = 1.0
SPEAK_TURN_REPLY_LIMIT = 2


def speak_turn_lock_path(thread_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(thread_id or ""))[:80] or "thread"
    root = resolve_collaboration_root() / "speak_turn"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe}.lock"


def acquire_speak_turn_lock(agent: str, thread_id: str) -> Path | None:
    """抢 thread 发言权；超时放行（返回 None 照常发布），持有者残留过久则接管。"""
    if not str(thread_id or "").strip():
        return None
    path = speak_turn_lock_path(thread_id)
    deadline = time.time() + speak_queue_timeout_seconds()
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > SPEAK_TURN_LOCK_STALE_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                return None
            time.sleep(SPEAK_TURN_POLL_SECONDS)
            continue
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"agent": normalize_agent(agent), "acquired_at": time.time()}, handle)
        return path


def release_speak_turn_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def fetch_thread_replies_since(
    api: str,
    thread_id: str,
    *,
    since: float,
    exclude_agent: str,
    limit: int = SPEAK_TURN_REPLY_LIMIT,
) -> list[tuple[str, str]]:
    """比 since 更新的其他模型定稿发言（人类消息、自己、抖救内容除外），按时间升序取最近 limit 条。"""
    normalized_thread = str(thread_id or "").strip()
    if not normalized_thread or since <= 0:
        return []
    me = normalize_agent(exclude_agent)
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {"action": "list_messages", "thread_id": normalized_thread, "limit": 100},
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return []
    messages = result.get("messages")
    if not isinstance(messages, list):
        return []
    replies: list[tuple[float, str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        created = message.get("created_at")
        if not isinstance(created, (int, float)) or float(created) <= since:
            continue
        sender = normalize_agent(message_sender(message))
        if not sender or sender == me or is_human_collaboration_agent(sender):
            continue
        content = message_content(message)
        if content and not is_salvaged_reply_content(content):
            replies.append((float(created), sender, content))
    replies.sort(key=lambda item: item[0])
    return [(sender, content) for _, sender, content in replies[-max(1, limit):]]


def try_acquire_speak_turn_lock(agent: str, thread_id: str) -> Path | None:
    """非阻塞抢 thread 发言权：抢到返回锁路径；被持有（且未 stale）立即返回 None。"""
    if not str(thread_id or "").strip():
        return None
    path = speak_turn_lock_path(thread_id)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > SPEAK_TURN_LOCK_STALE_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
        except OSError:
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"agent": normalize_agent(agent), "acquired_at": time.time()}, handle)
        return path


def enter_speak_turn(
    api: str,
    agent: str,
    message: dict[str, Any],
    *,
    transport: str,
) -> tuple[Path | None, list[tuple[str, str]], bool]:
    """互聊轮伪轮流制（不阻塞思考）：生成前尝试抢发言权。
    抢到 → 把比来件更新的定稿带回作生成上下文，一次成稿。
    没抢到 → 返回 deferred=True：先并行起草（思考泳道），草稿完成后再等锁修正成稿。
    返回 (持有的锁或 None, 更新的定稿列表, 是否延后抢锁)。"""
    if not api or not collaboration_speak_queue_enabled():
        return None, [], False
    thread_id = message_thread_id(message)
    if not thread_id:
        return None, [], False
    lock = try_acquire_speak_turn_lock(agent, thread_id)
    if lock is None:
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=False,
            error="",
            output="Speaking turn is held by a peer; drafting in parallel.",
            stream="lifecycle",
            metadata={"lifecycle": "turn_wait"},
        )
        return None, [], True
    created = message.get("created_at")
    since = float(created) if isinstance(created, (int, float)) and float(created) > 0 else 0.0
    replies = fetch_thread_replies_since(api, thread_id, since=since, exclude_agent=agent)
    if replies:
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=False,
            error="",
            output=f"Drafting with {len(replies)} newer finalized replies as context.",
            stream="lifecycle",
            metadata={"lifecycle": "turn_context", "sources": [sender for sender, _ in replies]},
        )
    return lock, replies, False


def failure_reply_recipients(agent: str, parent: dict[str, Any]) -> list[str]:
    """失败回执只发给人类，绝不扇出给其他模型。

    否则双工开启时，其他模型会把失败通知当成可回复的内容继续追问，
    坏掉的参与者又对每条追问再失败一次，形成刷屏循环。
    """
    sender = normalize_agent(message_sender(parent))
    if sender and sender != normalize_agent(agent) and is_human_collaboration_agent(sender):
        return [sender]
    return ["human_desktop"]


def post_worker_failure_reply(api: str, agent: str, parent: dict[str, Any], error: str) -> None:
    participant = normalize_agent(agent)
    content = (
        f"{participant} 暂时无法处理这条协作消息。\n\n"
        f"错误：{error}\n\n"
        "这不是主模型回复；消息已经进入协作路由，但对应 worker 没能完成执行。"
    )
    post_reply(api, agent, parent, content, recipients=failure_reply_recipients(agent, parent))


def collaboration_self_heal_enabled() -> bool:
    return str(os.environ.get("SPIRITKIN_COLLABORATION_SELF_HEAL", "")).strip().lower() in {"1", "true", "yes", "on"}


def collaboration_self_heal_threshold() -> int:
    raw = str(os.environ.get("SPIRITKIN_COLLABORATION_SELF_HEAL_THRESHOLD", "")).strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    return value if value > 0 else 2


def collaboration_self_heal_agent(failing_agent: str) -> str:
    me = normalize_agent(failing_agent)
    preferred = normalize_agent(os.environ.get("SPIRITKIN_COLLABORATION_SELF_HEAL_AGENT", "") or "main_text")
    if preferred and preferred != me:
        return preferred
    return "programming" if me == "main_text" else "main_text"


def maybe_post_self_heal_request(
    api: str,
    agent: str,
    parent: dict[str, Any],
    error: str,
    consecutive_failures: int,
) -> bool:
    """连续失败达到阈值且用户允许时，把错误摘要发给诊断参与者请求修复建议。

    只产出诊断建议，不自动改代码；默认关闭，由桌面“允许外部模型协助诊断”开关
    通过 SPIRITKIN_COLLABORATION_SELF_HEAL 环境变量打开。
    """
    if not collaboration_self_heal_enabled():
        return False
    if consecutive_failures < collaboration_self_heal_threshold():
        return False
    me = normalize_agent(agent)
    target = collaboration_self_heal_agent(agent)
    if not target or target == me:
        return False
    error_summary = str(error or "").strip()
    if len(error_summary) > 2000:
        error_summary = error_summary[:2000] + "…(截断)"
    content = (
        f"[自愈诊断请求] 协作 worker {me} 已连续失败 {consecutive_failures} 次，请求诊断协助。\n\n"
        f"最近一次错误：\n{error_summary}\n\n"
        "请分析可能的根因，并给出修复建议（配置、依赖、网络或代码层面均可）。"
        "只需给出建议，不要执行任何修改。"
    )
    try:
        request_json(
            api,
            "/desktop/collaboration",
            {
                "action": "post_message",
                "task_id": message_task_id(parent),
                "thread_id": message_thread_id(parent),
                "from_agent": me,
                "to_agents": [target],
                "parent_message_id": parent.get("message_id", ""),
                "role": "question",
                "content": content,
                "include_collaboration": False,
            },
        )
        print(f"self-heal diagnosis requested: {me} -> {target}", file=sys.stderr, flush=True)
        return True
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        print(f"self-heal request failed {me} -> {target}: {exc}", file=sys.stderr, flush=True)
        return False


def mark_read(api: str, agent: str, message_id: str) -> None:
    request_json(api, "/desktop/collaboration", {"action": "mark_message_read", "message_id": message_id, "reader": normalize_agent(agent)})


def ack_route_bus_message(api: str, agent: str, message_id: str) -> None:
    request_json(
        api,
        "/desktop/collaboration",
        {
            "action": "ack_agent_route_bus_message",
            "message_id": message_id,
            "consumer": normalize_agent(agent),
            "note": "collaboration_agent_worker_consumed",
        },
    )


def mark_consumed(api: str, agent: str, message: dict[str, Any], transport: str) -> None:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return
    if transport == "legacy_inbox":
        mark_read(api, agent, message_id)
    else:
        ack_route_bus_message(api, agent, message_id)


def record_worker_event(
    api: str,
    agent: str,
    message: dict[str, Any],
    *,
    status: str,
    transport: str,
    dry_run: bool,
    error: str = "",
    thread_id: str = "",
    task_id: str = "",
    output: str = "",
    stream: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if transport != "route_bus":
        return
    event_metadata = {
        "script": "collaboration_agent_worker",
        "sender": message_sender(message),
        "message_type": message_type(message),
        "stream": stream,
        "output": output,
    }
    if metadata:
        event_metadata.update(metadata)
    try:
        request_json(
            api,
            "/desktop/collaboration",
            {
                "action": "record_agent_route_bus_worker_event",
                "agent": normalize_agent(agent),
                "status": status,
                "message_id": tool_call_origin_message_id(message),
                "thread_id": thread_id or message_thread_id(message),
                "task_id": task_id or message_task_id(message),
                "transport": transport,
                "dry_run": bool(dry_run),
                "error": error,
                "metadata": event_metadata,
            },
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        print(f"worker event record failed: {exc}", file=sys.stderr, flush=True)


def load_external_assistant(config_path: str, assistant_id: str) -> dict[str, Any]:
    synthesized = synthesize_model_assistant(assistant_id)
    if synthesized:
        return synthesized
    synthesized = synthesize_local_agent_assistant(assistant_id)
    if synthesized:
        return synthesized
    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return {"assistant_id": assistant_id, "command": "", "working_directory": "", "enabled": False, "configured": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = {}
    for assistant in data.get("external_assistants") or []:
        if str(assistant.get("assistant_id") or "") == assistant_id:
            loaded = dict(assistant)
            loaded["command"] = normalize_external_assistant_command(
                assistant_id,
                str(loaded.get("command") or ""),
            )
            return loaded
    return {"assistant_id": assistant_id, "command": "", "working_directory": "", "enabled": False, "configured": False}


def assert_external_assistant_enabled(assistant: dict[str, Any]) -> None:
    assistant_id = str(assistant.get("assistant_id") or "assistant").strip()
    if not bool(assistant.get("enabled", False)):
        raise WorkerConfigurationError(f"assistant is not enabled: {assistant_id}")
    if str(assistant.get("kind") or "").strip().lower() == "api":
        if not bool(assistant.get("configured", True)):
            raise WorkerConfigurationError(f"assistant is not configured: {assistant_id}")
        return
    command = str(assistant.get("command") or "").strip()
    if not command:
        raise WorkerConfigurationError(f"assistant has no command: {assistant_id}")


def resolve_codex_executable() -> Path | None:
    """Resolve the current Codex binary instead of a stale PATH shim."""
    configured = str(os.getenv("SPIRITKIN_CODEX_EXECUTABLE") or "").strip().strip('"')
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()

    if os.name == "nt":
        local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
        if local_app_data:
            bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
            try:
                candidates = [path for path in bin_root.glob("*/codex.exe") if path.is_file()]
                if candidates:
                    return max(candidates, key=lambda path: path.stat().st_mtime).resolve()
            except OSError:
                pass

    discovered = shutil.which("codex.exe") or shutil.which("codex")
    return Path(discovered).resolve() if discovered else None


def resolve_external_assistant_command(assistant_id: str, command: str) -> str:
    """Bind a bare Codex command to the live desktop binary when available."""
    normalized = normalize_external_assistant_command(assistant_id, command)
    assistant_key = str(assistant_id or "").strip().lower().replace("-", "_")
    if assistant_key not in {"codex", "codex_cli"}:
        return normalized
    match = re.match(r"^\s*codex(?:\.exe|\.cmd|\.ps1)?(?=\s|$)", normalized, flags=re.IGNORECASE)
    if match is None:
        return normalized
    executable = resolve_codex_executable()
    if executable is None:
        return normalized
    quoted = subprocess.list2cmdline([str(executable)])
    return re.sub(
        r"^\s*codex(?:\.exe|\.cmd|\.ps1)?(?=\s|$)",
        lambda _match: quoted,
        normalized,
        count=1,
        flags=re.IGNORECASE,
    )


def synthesize_model_assistant(assistant_id: str) -> dict[str, Any] | None:
    normalized = normalize_agent(assistant_id)
    if normalized == "cloud_model":
        return {
            "assistant_id": "cloud_model",
            "label": "Cloud Model",
            "kind": "api",
            "enabled": True,
            "configured": True,
            "provider": "",
            "model": "",
            "command": "",
            "working_directory": "",
        }
    if normalized.startswith("provider_"):
        provider = normalized.removeprefix("provider_")
        return {
            "assistant_id": normalized,
            "label": provider,
            "kind": "api",
            "enabled": True,
            "configured": True,
            "provider": provider,
            "model": "",
            "command": "",
            "working_directory": "",
        }
    if normalized.startswith("model_"):
        model_id = normalized.removeprefix("model_")
        for model in load_assist_models():
            if str(model.model_id or "").strip().lower().replace("-", "_") == model_id:
                return {
                    "assistant_id": normalized,
                    "label": model.display_name,
                    "kind": "api",
                    "enabled": bool(model.enabled),
                    "configured": bool(model.configured),
                    "provider": model.provider,
                    "model": model.model,
                    "command": "",
                    "working_directory": "",
                    "request_params": dict(getattr(model, "request_params", {}) or {}),
                }
    return None


def synthesize_local_agent_assistant(assistant_id: str) -> dict[str, Any] | None:
    normalized = normalize_agent(assistant_id)
    try:
        state = load_agent_management_state()
    except Exception:
        return None
    for managed in state.agents:
        agent_id = normalize_agent(str(getattr(managed, "agent_id", "") or ""))
        if agent_id != normalized:
            continue
        provider = str(getattr(managed, "provider", "") or "").strip()
        model = str(getattr(managed, "model", "") or "").strip()
        return {
            "assistant_id": agent_id,
            "label": str(getattr(managed, "label", "") or agent_id),
            "kind": "api",
            "enabled": bool(getattr(managed, "enabled", False)),
            "configured": True,
            "provider": provider,
            "model": model,
            "command": "",
            "working_directory": "",
            "local_agent": True,
            "domain": str(getattr(managed, "domain", "") or ""),
            "adapter": str(getattr(managed, "adapter", "") or ""),
            "capabilities": list(getattr(managed, "capabilities", ()) or ()),
        }
    return None


COLLABORATION_CONTEXT_DOC = "docs/ai_collaboration_context.md"
COLLABORATION_CONTEXT_MAX_CHARS = 8000


def _collaboration_context_disabled() -> bool:
    return str(os.getenv("SPIRITKIN_DISABLE_COLLABORATION_CONTEXT") or "").strip().lower() in {"1", "true", "yes", "on"}


def load_collaboration_context_brief(*, max_chars: int = COLLABORATION_CONTEXT_MAX_CHARS) -> str:
    if _collaboration_context_disabled():
        return ""
    target = ROOT / COLLABORATION_CONTEXT_DOC
    try:
        text = target.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[collaboration context truncated; read the full doc at docs/ai_collaboration_context.md]"
    return text


def fetch_thread_history(
    api: str,
    thread_id: str,
    *,
    exclude_message_id: str = "",
    limit: int = 12,
    max_chars: int = 4000,
) -> str:
    """线程近况摘要：给模型看清对话上下文，互聊才有实质内容可推进。

    没有历史时模型只看得到单条来件，只能寒暄或复述；这里取该线程最近若干条
    消息（含人类原始问题和其他模型的回答）拼成时间序摘要。查询失败返回空串，
    prompt 退化为单条消息模式，不影响主流程。
    """
    normalized_thread = str(thread_id or "").strip()
    if not api or not normalized_thread:
        return ""
    try:
        result = request_json(
            api,
            "/desktop/collaboration",
            {"action": "list_messages", "thread_id": normalized_thread, "limit": max(1, limit)},
        )
    except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
        return ""
    messages = result.get("messages")
    if not isinstance(messages, list):
        return ""
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        if exclude_message_id and str(item.get("message_id") or "").strip() == exclude_message_id:
            continue
        content = message_content(item)
        if not content or is_salvaged_reply_content(content):
            continue
        sender = message_sender(item) or "unknown"
        if len(content) > 500:
            content = content[:500].rstrip() + "…"
        lines.append(f"[{sender}] {content}")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = "…" + text[-max_chars:]
    return text


# 本地小上下文模型（如 n_ctx=4096 的 llama.cpp）装不下完整提示词时返回 400；
# 命中这些标记则用紧凑提示词（去掉协作文档简报与仓库快照、截短历史）重试一次。
CONTEXT_OVERFLOW_MARKERS = (
    "n_ctx",
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "prompt is too long",
)


def is_context_overflow_error(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    return any(marker in lowered for marker in CONTEXT_OVERFLOW_MARKERS)


def local_prompt_soft_limit() -> int:
    """本地小上下文模型（LM Studio 等）的提示词字符软上限。

    中文约 1 字符 = 1 token，默认 3200 给 4096 的 n_ctx 留出生成余量；
    上下文更大的本地模型可通过环境变量调高。
    """
    raw = str(os.environ.get("SPIRITKIN_LOCAL_PROMPT_SOFT_LIMIT") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    return value if value > 0 else 3200


MINIMAL_MESSAGE_HEAD_CHARS = 1600
MINIMAL_MESSAGE_TAIL_CHARS = 800


def build_compact_prompt(
    message: dict[str, Any],
    assistant: dict[str, Any] | None,
    history: str,
    *,
    minimal: bool = False,
) -> str:
    if not minimal:
        compact_history = history[-1500:] if history else ""
        return build_prompt(message, assistant, history=compact_history, compact=True)
    # 最小档：连历史都不带，消息本体（如发言队列重修消息）超长时保头尾截中间。
    content = message_content(message)
    if len(content) > MINIMAL_MESSAGE_HEAD_CHARS + MINIMAL_MESSAGE_TAIL_CHARS:
        content = (
            content[:MINIMAL_MESSAGE_HEAD_CHARS]
            + "\n…（消息过长，中间部分已截断）…\n"
            + content[-MINIMAL_MESSAGE_TAIL_CHARS:]
        )
    clipped = dict(message)
    clipped["content"] = content
    envelope = message.get("agent_envelope")
    if isinstance(envelope, dict):
        env = dict(envelope)
        env["content"] = content
        clipped["agent_envelope"] = env
    return build_prompt(clipped, assistant, history="", compact=True)


def is_debate_or_stance_message(message: dict[str, Any]) -> bool:
    text = user_request_text(message).lower()
    markers = (
        "debate",
        "stance",
        "position",
        "counterpoint",
        "affirmative",
        "negative",
        "辩论",
        "立场",
        "正方",
        "反方",
        "观点",
        "论点",
        "争论",
        "反驳",
    )
    return any(marker in text for marker in markers)


def build_prompt(
    message: dict[str, Any],
    assistant: dict[str, Any] | None = None,
    *,
    history: str = "",
    compact: bool = False,
) -> str:
    workspace = str(workspace_root_from_message(message) or workspace_path_from_message(message))
    recipient = message_recipient(message)
    sender = message_sender(message)
    assistant = assistant or {}
    agent_id = normalize_agent(str(assistant.get("assistant_id") or recipient or "collaboration_agent"))
    agent_label = str(assistant.get("label") or agent_id or "collaboration Agent").strip()
    request_params = assistant.get("request_params") if isinstance(assistant.get("request_params"), dict) else {}
    persona = str(request_params.get("persona") or "").strip()
    is_local_agent = bool(assistant.get("local_agent"))
    lines: list[str] = []
    context_brief = "" if compact else load_collaboration_context_brief()
    if context_brief:
        lines.extend(
            [
                "Shared collaboration context (docs/ai_collaboration_context.md). Read this before proposing changes; verify any completion claim against real files, tests, and command output:",
                "",
                context_brief,
                "",
                "---",
                "",
            ]
        )
    identity_lines = [
        f"你是 {agent_label}（agent_id={agent_id}）。你只能以这个身份发言，禁止扮演或复述其他参与者的身份/立场。",
        # 协作气泡按纯文本渲染，Markdown 标记会原样露出（2026-07-08 用户反馈）。
        "输出格式：纯文本发言，禁止使用 Markdown 标记（#、*、**、`、>、- 列表符等）；需要分点时用「1. 2. 3.」或「一、二、」；只有贴代码时才允许 ``` 围栏。",
    ]
    if persona:
        identity_lines.append(f"你的固定人设：{persona}")
    if is_debate_or_stance_message(message):
        identity_lines.append("你的立场自始至终不变；不要输出对系统故障的诊断分析，除非人类明确要求。")
    if message.get("_derail_retry"):
        identity_lines.append(
            "警告：上一次生成偏离了对话，输出了对提示词/输出格式/系统故障的分析。"
            "这次只输出对来件内容本身的正式回应正文，禁止提及提示词、输出规则或系统诊断。"
        )
    lines.extend(identity_lines)
    lines.append("")
    lines += [
        "You are replying as a SpiritKin collaboration Agent.",
        f"Current target agent: {recipient or '--'}.",
        "Answer only as the current target agent. Do not claim to be another named model or agent unless that is the current target.",
        "If the user asks whether another agent/model is present, describe the routing target and say you cannot speak for the other agent unless the message is routed to it.",
        "Use the workspace path below. If a repository snapshot is included, use it for code or file questions instead of guessing.",
        f"Thread: {message_thread_id(message) or '--'}",
        f"Context: {message_context_id(message) or '--'}",
        f"From: {sender or '--'}",
        f"To: {recipient or '--'}",
        f"Role: {message_type(message) or 'question'}",
        "Computer Use is available through the governed SpiritKin tool bus. When the user asks to inspect or control the current PC, do not merely describe the action and do not claim it already happened. Add one fenced JSON object using this exact shape: {\"spiritkin_tool_call\":{\"target\":\"local_pc\",\"operation\":\"screen_understand\",\"params\":{\"query\":\"...\"},\"reason\":\"...\"}}.",
        "Supported local_pc operations include screen_understand, screen_capture, screen_extract_text, window_list, clipboard_read, file_read, file_search, list_installed_apps, list_hardware_devices, launch_app, close_app, browser_open_url, browser_search, clipboard_write, window_activate, window_close, window_move, window_resize, file_open, file_write, move_pointer, click_pointer, enter_text, and press_keys. For launch_app and close_app, put the executable or application name in params.app_name (for example, {\"app_name\":\"cmd\"}). Read-only calls execute automatically; state-changing calls wait for human approval. The executor result will return as a separate event.",
    ]
    if is_tool_result_message(message):
        lines.extend(
            [
                "The latest incoming message is the governed result of a tool call you requested for the original user turn.",
                "Use the returned result data to continue or answer the user. Do not issue the identical tool call again.",
                "Only request another tool when the result proves that a distinct follow-up action is necessary.",
            ]
        )
    if sender and not is_human_collaboration_agent(sender):
        # 双工互聊：来件是另一个模型的发言。若不加约束，模型会互相寒暄"已就绪/收到"
        # 空转掉轮次配额；但也不能强行把闲聊拽回项目——话题由人类用户与线程既有内容决定。
        lines.extend(
            [
                f"This message was written by another AI participant ({sender}), not the human user.",
                "Stay on the topic the human user set and the thread is currently about. Respond to what "
                "was actually said: build on it, answer the open question, or offer a concrete counterpoint. "
                "If the topic is casual (jokes, stories, chat), keep playing along in that spirit — do NOT "
                "steer the conversation to project or work matters unless the thread is already about them.",
                "Do NOT reply with pleasantries, readiness confirmations, or restatements of what was said. "
                "If you have nothing substantive to add, state your final conclusion in one or two sentences.",
                "自动互聊轮次有限，请让每一轮都有信息增量，同时紧跟当前话题、不要自行转移话题。",
            ]
        )
    if is_local_agent:
        lines.extend(
            [
                f"Local Agent profile: {assistant.get('label') or recipient or assistant.get('assistant_id')}.",
                f"Domain: {assistant.get('domain') or '--'}.",
                f"Adapter: {assistant.get('adapter') or '--'}.",
                f"Capabilities: {', '.join(str(item) for item in assistant.get('capabilities') or []) or '--'}.",
                "Act as this local specialist Agent in the collaboration thread. You may ask another participant for review, but do not impersonate it.",
            ]
        )
    if workspace:
        lines.append(f"Workspace: {workspace}")
    context_pack = message_context_pack_path(message)
    if context_pack:
        lines.append(f"Context pack: {context_pack}")
    snapshot = "" if compact else (build_repository_snapshot(workspace) if should_include_repository_snapshot(message) else "")
    if snapshot:
        lines.extend(["", "Repository snapshot:", snapshot])
    if history:
        lines.extend(
            [
                "",
                "Recent thread messages (oldest first; [sender] content):",
                history,
                "",
                "Latest incoming message you must respond to:",
            ]
        )
    lines.extend(["", message_content(message)])
    return "\n".join(lines).strip()


def should_include_repository_snapshot(message: dict[str, Any]) -> bool:
    text = user_request_text(message).lower()
    english_triggers = (
        "workspace",
        "repository",
        "repo",
        "code",
        "file",
        "files",
        "project",
        "path",
        "diff",
        "test",
        "tests",
        "build",
        "compile",
        "implement",
        "modify",
        "read",
        "inspect",
        "check",
    )
    chinese_triggers = (
        "检查",
        "查看",
        "读取",
        "文件",
        "代码",
        "仓库",
        "项目",
        "路径",
        "修改",
        "实现",
        "测试",
        "编译",
    )
    return any(trigger in text for trigger in chinese_triggers) or any(
        re.search(rf"\b{re.escape(trigger)}\b", text) for trigger in english_triggers
    )


def user_request_text(message: dict[str, Any]) -> str:
    content = message_content(message)
    match = re.search(r"(?is)\bUser request:\s*(?P<body>.+)\s*$", content)
    if match:
        return match.group("body").strip()
    lines = [line for line in content.splitlines() if line.strip()]
    return lines[-1].strip() if lines else content.strip()


def workspace_root_from_message(message: dict[str, Any]) -> Path | None:
    workspace = workspace_path_from_message(message)
    if not workspace:
        return None
    root = Path(workspace).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root if root.exists() and root.is_dir() else None


def workspace_path_from_message(message: dict[str, Any]) -> str:
    content = message_content(message)
    for pattern in (
        r"(?im)^\s*Workspace path:\s*(?P<path>.+?)\s*$",
        r"(?im)^\s*Workspace:\s*(?P<path>.+?)\s*$",
    ):
        match = re.search(pattern, content)
        if match:
            path = match.group("path").strip().strip('"')
            if path and path != "--":
                return path
    context_pack = message_context_pack_path(message)
    if context_pack:
        path = Path(context_pack)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                workspace = str(data.get("workspace_path") or data.get("workspace") or "").strip()
                if workspace:
                    return workspace
            except (OSError, json.JSONDecodeError):
                pass
    return ""


def build_repository_snapshot(workspace: str, *, max_files: int = 120, max_chars: int = 9000) -> str:
    if not workspace:
        return ""
    root = Path(workspace).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.exists() or not root.is_dir():
        return f"Workspace path is not a readable directory: {root}"
    ignore_dirs = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache", "bin", "obj", "runtime"}
    interesting = {
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "tsconfig.json",
        "vite.config.ts",
        "SpiritKinDesktop.csproj",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_dir():
                continue
            relative = path.relative_to(root)
            if any(part in ignore_dirs for part in relative.parts):
                continue
            if len(files) >= max_files:
                break
            files.append(relative)
        except OSError:
            continue
    files = sorted(files, key=lambda item: (0 if item.name in interesting else 1, str(item).lower()))
    lines = [f"Root: {root}", "Files:"]
    lines.extend(f"- {path.as_posix()}" for path in files[:max_files])
    for rel in files:
        if rel.name not in interesting:
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt = text[:1600].strip()
        if not excerpt:
            continue
        lines.extend(["", f"Excerpt: {rel.as_posix()}", excerpt])
    snapshot = "\n".join(lines).strip()
    return snapshot[:max_chars]


def run_external_assistant(
    assistant: dict[str, Any],
    message: dict[str, Any],
    *,
    api: str = "",
    agent: str = "",
    transport: str = "route_bus",
    dry_run: bool = False,
) -> str:
    assert_external_assistant_enabled(assistant)
    if str(assistant.get("kind") or "").strip().lower() == "api":
        return run_api_assistant(
            assistant,
            message,
            api=api,
            agent=agent or str(assistant.get("assistant_id") or "cloud_model"),
            transport=transport,
            dry_run=dry_run,
        )
    command = resolve_external_assistant_command(
        str(assistant.get("assistant_id") or agent),
        str(assistant.get("command") or "").strip(),
    )
    workspace_root = workspace_root_from_message(message)
    configured_cwd = str(assistant.get("working_directory") or "").strip()
    cwd = str(workspace_root or configured_cwd or Path.cwd())
    cwd_path = Path(cwd).expanduser()
    if not cwd_path.is_absolute():
        cwd_path = Path.cwd() / cwd_path
    history = fetch_thread_history(
        api,
        message_thread_id(message),
        exclude_message_id=str(message.get("message_id") or "").strip(),
    )
    prompt = build_prompt(message, assistant, history=history)
    if not api or transport != "route_bus":
        # Trust boundary: `command` is the operator-configured assistant command line
        # from Agent Management (e.g. "codex exec --json"), never route-bus message
        # content. shell=True is required to honor full command lines with arguments.
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            shell=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        output = (completed.stdout or "").strip()
        error = (completed.stderr or "").strip()
        if completed.returncode != 0 and not output:
            output = f"Assistant command failed with exit code {completed.returncode}.\n{error}".strip()
        elif completed.returncode != 0 and error:
            output = f"{output}\n\n[stderr]\n{error}".strip()
        elif error:
            print(f"assistant stderr ({assistant.get('assistant_id', 'assistant')}):\n{error}", file=sys.stderr, flush=True)
        return output or f"Assistant command exited with code {completed.returncode} and produced no output."
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        output=f"Prompt ready for {normalize_agent(agent)}.",
        stream="lifecycle",
        metadata={
            "lifecycle": "prompt_ready",
            "assistant_id": str(assistant.get("assistant_id") or ""),
            "cwd": str(cwd_path),
            "workspace": str(workspace_root or workspace_path_from_message(message) or ""),
            "prompt_chars": len(prompt),
            "repository_snapshot": "Repository snapshot:" in prompt,
        },
    )
    return run_external_assistant_streaming(
        command,
        prompt,
        cwd_path,
        assistant,
        message,
        api=api,
        agent=agent or str(assistant.get("assistant_id") or "assistant"),
        transport=transport,
        dry_run=dry_run,
    )


class StreamTokenBatcher:
    """Aggregate streamed tokens so each flush becomes one worker event.

    Per-token HTTP posts throttled the provider stream to the gateway's
    round-trip latency; batching keeps the reasoning chain near real time.
    token 通道（正文气泡）用更细的粒度：辩论回复往往只有一两百字，
    0.7s/400 字会把整段正文攒成一批，"流式"退化成一次性弹出（2026-07-07 实测）。
    reasoning 通道进工作卡"思考"步骤（桌面同泳道原地归并，不会刷屏），
    同样调细让思考链线性生长（2026-07-08 用户要求逐字观感）。

    2026-07-08 关键修复：emit（record_worker_event 的网关 HTTP 往返）耗时 1-2s+，
    原来在读流回调里同步调用会把读流整段堵住——token 在 socket 缓冲区攒满
    flush_chars 上限才一次性冲出，"0.3s 定时"形同虚设（实测批次恒 120 字/隔 2-4s）。
    现在 flush 只入队，由后台线程串行上报，读流零阻塞，定时冲刷才真正生效。

    2026-07-08 二修：本地推理模型（LM Studio Qwen 等）把 <think>…</think> 直接混进
    content 通道流出，草稿气泡先被整段思考文本撑大、最终稿剥掉 think 后又缩回——
    观感"突然撑大再恢复"。token 通道现按累计全文实时分离：think 内文改道 reasoning
    泳道，气泡只收干净正文（对跨 token 撕裂的标签免疫，因为分离基于累计全文而非单批）。
    """

    def __init__(
        self,
        emit,
        *,
        flush_interval: float = 0.3,
        flush_chars: int = 120,
        token_flush_interval: float = 0.25,
        token_flush_chars: int = 80,
    ) -> None:
        self._emit = emit
        self._flush_interval = flush_interval
        self._flush_chars = flush_chars
        self._token_flush_interval = token_flush_interval
        self._token_flush_chars = token_flush_chars
        self._parts: list[str] = []
        self._channel = ""
        self._meta: dict[str, Any] = {}
        # 每通道累计全文 + flush 序号：随每批事件下发，桌面端用 accumulated 整体覆盖草稿，
        # 对事件重复投递/乱序/状态同步回滚免疫（增量追加曾把旧片段拼到结尾，2026-07-08 实测）。
        self._accumulated: dict[str, str] = {}
        self._flush_index: dict[str, int] = {}
        # token 通道 think 分离状态：已下发的干净正文 / 已下发的 think 内文。
        self._visible_token = ""
        self._think_seen = ""
        self._last_flush = time.monotonic()
        self._queue: queue.Queue[tuple[str, str, dict[str, Any]] | None] = queue.Queue()
        self._sender = threading.Thread(target=self._drain, name="stream-batch-sender", daemon=True)
        self._sender.start()

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            text, channel, meta = item
            try:
                self._emit(text, channel, meta)
            except Exception:
                # 上报失败只丢这一批展示文本，权威正文仍由最终 post_message 落库。
                pass
            finally:
                self._queue.task_done()

    def add(self, token: str, meta: dict[str, Any]) -> None:
        channel = "reasoning" if meta.get("channel") == "reasoning" else "token"
        if self._parts and channel != self._channel:
            self.flush()
        self._channel = channel
        self._meta = meta
        self._parts.append(token)
        interval = self._token_flush_interval if channel == "token" else self._flush_interval
        chars = self._token_flush_chars if channel == "token" else self._flush_chars
        if time.monotonic() - self._last_flush >= interval or sum(len(part) for part in self._parts) >= chars:
            self.flush()

    def _enqueue(self, channel: str, text: str, accumulated: str, meta: dict[str, Any]) -> None:
        self._flush_index[channel] = self._flush_index.get(channel, 0) + 1
        meta["accumulated"] = accumulated
        meta["stream_index"] = self._flush_index[channel]
        self._queue.put((text, channel, meta))

    @staticmethod
    def _trim_partial_tag(text: str, tag: str) -> str:
        """截断流可能把 <think>/</think> 撕成半个标签留在尾部；先扣住不下发，等下一批凑全再判。"""
        max_len = min(len(tag) - 1, len(text))
        for length in range(max_len, 0, -1):
            if text[-length:].lower() == tag[:length]:
                return text[:-length]
        return text

    def _flush_token_channel(self) -> None:
        raw = self._accumulated.get("token", "")
        cleaned = self._trim_partial_tag(strip_think_blocks(raw), "<think>")
        think = self._trim_partial_tag(extract_think_text(raw), "</think>")
        if len(think) > len(self._think_seen):
            delta = think[len(self._think_seen):]
            self._think_seen = think
            self._accumulated["reasoning"] = self._accumulated.get("reasoning", "") + delta
            meta = dict(self._meta)
            meta["channel"] = "reasoning"
            self._enqueue("reasoning", delta, self._accumulated["reasoning"], meta)
        if len(cleaned) > len(self._visible_token):
            delta = cleaned[len(self._visible_token):]
            self._visible_token = cleaned
            self._enqueue("token", delta, cleaned, dict(self._meta))

    def flush(self) -> None:
        if self._parts:
            text = "".join(self._parts)
            self._parts = []
            channel = self._channel
            self._accumulated[channel] = self._accumulated.get(channel, "") + text
            if channel == "token":
                # token 通道先做 think 分离再下发：气泡只收干净正文。
                self._flush_token_channel()
            else:
                self._enqueue(channel, text, self._accumulated[channel], dict(self._meta))
        self._last_flush = time.monotonic()

    def reset(self) -> None:
        """整轮重试重新生成前清空累计文本，避免 accumulated 把两次生成拼在一起。"""
        self._parts = []
        self._accumulated = {}
        self._flush_index = {}
        self._visible_token = ""
        self._think_seen = ""

    def close(self) -> None:
        """Flush 剩余文本并等后台队列清空（保证流式事件先于最终回帖落地）。"""
        self.flush()
        self._queue.put(None)
        self._sender.join(timeout=15)


THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# 协作气泡按纯文本渲染：模型发言里的 #/*/加粗等 Markdown 装饰会原样露出（2026-07-08 用户反馈）。
# 提示词约束对小模型不可靠，出口统一清洗兜底；代码围栏内的内容原样保留（工具调用/代码示例）。
_MD_FENCE_RE = re.compile(r"```[^\n]*\n[\s\S]*?```|```[\s\S]*?```")


def _strip_markdown_segment(text: str) -> str:
    body = text
    # 标题 ## 与引用 > 前缀、列表符号 */-/+ 转中文顿号圆点。
    body = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", body)
    body = re.sub(r"(?m)^\s{0,3}>\s?", "", body)
    body = re.sub(r"(?m)^(\s*)[*+-]\s+", r"\1• ", body)
    # 粗体/斜体/删除线/行内代码：去标记留内容。
    body = re.sub(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", r"\1", body)
    body = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"\1", body)
    body = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", body)
    body = re.sub(r"__(?!\s)(.+?)(?<!\s)__", r"\1", body)
    body = re.sub(r"~~(?!\s)(.+?)(?<!\s)~~", r"\1", body)
    body = re.sub(r"`([^`\n]+)`", r"\1", body)
    # 分隔线整行删掉。
    body = re.sub(r"(?m)^\s{0,3}([-*_])\s*(\1\s*){2,}$", "", body)
    return body


def strip_markdown_decorations(text: str) -> str:
    """把发言正文清洗成纯文本；```代码围栏```内的内容不动。"""
    body = str(text or "")
    if not any(marker in body for marker in ("#", "*", "`", "~~", "__", ">")):
        return body
    parts: list[str] = []
    last = 0
    for match in _MD_FENCE_RE.finditer(body):
        parts.append(_strip_markdown_segment(body[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_strip_markdown_segment(body[last:]))
    return "".join(parts)


def strip_think_blocks(text: str) -> str:
    """部分本地推理模型把 <think>…</think> 直接混进 content 通道；对话里只保留答案正文。"""
    body = str(text or "")
    if "<think" not in body.lower():
        return body
    stripped = THINK_BLOCK_RE.sub("", body)
    unclosed = stripped.lower().find("<think")
    if unclosed >= 0:
        stripped = stripped[:unclosed]
    return stripped


_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)


# 修O（批次十返工）：小模型长上下文双工偶发把"对提示词/自身故障的自我诊断"当正式回帖
# （2026-07-09 实测：main_text 输出"问题归因/最小修复步骤/数据集样本"报告，并近似回显提示词约束句）。
# 单一标记可能只是正常话题（用户就在聊提示词工程），命中两个及以上标记才判脱轨。
_META_DERAIL_MARKERS = (
    "问题归因",
    "最小修复步骤",
    "系统提示词",
    "system prompt",
    "停止序列",
    "output_start",
    "output_end",
    "all good. proceed",
    "禁止使用markdown",
    "禁止使用 markdown",
    "你只能以这个身份发言",
    "自我检查标记",
)


def looks_like_meta_derailed_reply(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    return sum(1 for marker in _META_DERAIL_MARKERS if marker in lowered) >= 2


def extract_think_text(text: str) -> str:
    """与 strip_think_blocks 互补：抽出 <think>…</think> 的内文（含末尾未闭合块）。

    流式期间标签可能尚未闭合，未闭合块之后的全部内容都算思考文本，
    供 StreamTokenBatcher 把 think 改道 reasoning 泳道（气泡只收干净正文）。
    """
    body = str(text or "")
    if "<think" not in body.lower():
        return ""
    parts: list[str] = []
    for block in THINK_BLOCK_RE.findall(body):
        parts.append(re.sub(r"</?think>", "", block, flags=re.IGNORECASE))
    remainder = THINK_BLOCK_RE.sub("", body)
    open_match = _THINK_OPEN_RE.search(remainder)
    if open_match:
        parts.append(remainder[open_match.end():])
    else:
        # 兜底：截断成 "<thi" 这类半个开标签时，正文侧由 strip_think_blocks 截掉，这里无内文可取。
        pass
    return "".join(parts)


def model_request_params(assistant: dict[str, Any]) -> dict[str, Any]:
    params = assistant.get("request_params") if isinstance(assistant.get("request_params"), dict) else {}
    return {str(key): value for key, value in params.items() if str(key) != "persona"}


def salvage_reply_from_reasoning(reasoning: str, max_chars: int = 800) -> str:
    """推理模型偶发把答案全写进 reasoning 通道、content 为空。

    直接回"没有返回内容"会让参与者看起来掉线；这里取推理文本的收尾段
    （通常是模型最终敲定的回复草稿）作为兜底回复。
    """
    text = re.sub(r"</?think>", "", str(reasoning or ""), flags=re.IGNORECASE).strip()
    if not text:
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return ""
    tail = paragraphs[-1]
    if len(tail) < 80 and len(paragraphs) >= 2:
        tail = f"{paragraphs[-2]}\n{tail}"
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


# 抖救内容本质是思考链片段：只允许人类看到，绝不能流回其他模型
# （否则对方会把英文推理草稿当成正式发言来回应，实测已发生）。
SALVAGED_REPLY_PREFIX = "〔正文缺失，以下为思考通道摘录，仅供参考〕"


def mark_salvaged_reply(message: dict[str, Any], salvaged: str) -> str:
    message["_salvaged_reply"] = True
    return f"{SALVAGED_REPLY_PREFIX}\n{salvaged}"


def is_salvaged_reply_content(content: str) -> bool:
    return str(content or "").lstrip().startswith(SALVAGED_REPLY_PREFIX)


FINALIZE_REASONING_TAIL_CHARS = 2000


def finalize_reply_from_reasoning(
    message: dict[str, Any],
    reasoning: str,
    *,
    provider: str,
    model: str,
    agent: str,
) -> str:
    """正文缺失时，让模型基于自己的思考草稿重新生成一份可扇出的正式回复。

    抖救摘录只能给人类看，会让该参与者在多模型辩论里"消失"；
    这里先尝试重新生成正文，成功则按正常回复走扇出，失败才降级到抖救。
    """
    tail = re.sub(r"</?think>", "", str(reasoning or ""), flags=re.IGNORECASE).strip()
    if not tail:
        return ""
    if len(tail) > FINALIZE_REASONING_TAIL_CHARS:
        tail = tail[-FINALIZE_REASONING_TAIL_CHARS:]
    problem = (
        "你刚才处理下面这条协作消息时，只输出了思考过程、没有输出回复正文。\n"
        "请基于你的思考草稿直接给出最终回复正文：只输出发言内容本身，"
        "不要输出思考过程、自我检查或任何元说明。\n\n"
        f"【原始消息】\n{message_content(message)[:1200]}\n\n"
        f"【你的思考草稿（结尾部分）】\n{tail}"
    )
    try:
        result = request_model_review(
            problem,
            skill_name=f"collaboration:{normalize_agent(agent)}:finalize",
            provider=provider,
            model=model,
            timeout=120,
        )
    except Exception:
        return ""
    if not result.ok:
        return ""
    return strip_think_blocks(str(result.response_text or "")).strip()


def run_api_assistant(
    assistant: dict[str, Any],
    message: dict[str, Any],
    *,
    api: str,
    agent: str,
    transport: str,
    dry_run: bool,
) -> str:
    history = fetch_thread_history(
        api,
        message_thread_id(message),
        exclude_message_id=str(message.get("message_id") or "").strip(),
    )
    prompt = build_prompt(message, assistant, history=history)
    provider = str(assistant.get("provider") or "").strip()
    model = str(assistant.get("model") or "").strip()
    # 0=完整 1=紧凑（去简报/快照、截历史） 2=最小（无历史、消息本体截中间）
    prompt_level = 0
    if bool(assistant.get("local_agent")) and len(prompt) > local_prompt_soft_limit():
        # 本地模型上下文窗口小（实测 n_ctx 4096 装不下 8000 字符简报），
        # 与其等 HTTP 400 再重试，不如超过软上限就直接预压缩。
        prompt_level = 1
        prompt = build_compact_prompt(message, assistant, history)
        if len(prompt) > local_prompt_soft_limit():
            prompt_level = 2
            prompt = build_compact_prompt(message, assistant, history, minimal=True)
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        output=f"Prompt ready for API participant {normalize_agent(agent)}.",
        stream="lifecycle",
        metadata={
            "lifecycle": "prompt_ready",
            "assistant_id": str(assistant.get("assistant_id") or ""),
            "provider": provider,
            "model": model,
            "prompt_chars": len(prompt),
            "compact_level": prompt_level,
            "repository_snapshot": "Repository snapshot:" in prompt,
        },
    )
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        output=f"Calling model API participant {normalize_agent(agent)}.",
        stream="lifecycle",
        metadata={
            "lifecycle": "request_started",
            "assistant_id": str(assistant.get("assistant_id") or ""),
            "provider": provider,
            "model": model,
        },
    )
    # 发言泳道分流（2026-07-08 用户裁决"谁先想完谁先发言"；2026-07-09 草稿正文单开 draft 泳道，
    # 不再与真思考链混在 reasoning）：
    # - reasoning 通道（模型真思考链）：始终走 reasoning 泳道；
    # - _background_draft（互聊轮没抢到发言权）：正文 token 全程改道起草泳道；
    # - _speak_slot（同轮多收件人队列）：首个可见正文 token 产出时现场判定——
    #   抢到席位直播上气泡，否则整轮转后台草稿；已上屏的发言绝不回收。
    background_draft = bool(message.get("_background_draft"))
    speak_slot = message.get("_speak_slot")

    def token_lane(channel: str) -> str:
        if channel == "reasoning":
            return "reasoning"
        if background_draft:
            return "draft"
        if isinstance(speak_slot, SpeakSlot):
            return speak_slot.lane()
        return "token"

    batcher = StreamTokenBatcher(
        lambda text, channel, meta: record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output=text,
            stream=token_lane(channel),
            metadata=stream_token_metadata(meta, channel, provider, model),
        )
    )
    def call_stream(current_prompt: str) -> dict[str, Any]:
        return request_streaming_model_reply(
            current_prompt,
            assistant,
            provider=provider,
            model=model,
            timeout=120,
            on_token=lambda token, meta: batcher.add(token, meta),
            on_event=lambda lifecycle, output, meta: record_worker_event(
                api,
                agent,
                message,
                status="stream",
                transport=transport,
                dry_run=dry_run,
                output=output,
                stream="lifecycle",
                metadata={"lifecycle": lifecycle, **meta},
            ),
        )

    streamed = call_stream(prompt)
    while (
        not streamed.get("ok")
        and is_context_overflow_error(str(streamed.get("error") or ""))
        and prompt_level < 2
    ):
        # 上下文窗口装不下（HTTP 400 n_ctx 类错误）：逐级压缩后重试，
        # 紧凑档还不够就升到最小档，避免直接报"参与者不可用"。
        prompt_level += 1
        prompt = build_compact_prompt(message, assistant, history, minimal=prompt_level >= 2)
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output=f"Prompt exceeded the model context window; retrying with a level-{prompt_level} compact prompt ({len(prompt)} chars).",
            stream="lifecycle",
            metadata={
                "lifecycle": "prompt_compacted",
                "provider": provider,
                "model": model,
                "prompt_chars": len(prompt),
                "compact_level": prompt_level,
            },
        )
        batcher.reset()
        streamed = call_stream(prompt)
    batcher.close()
    salvaged = ""
    reasoning_full = ""
    if streamed["ok"]:
        response = strip_think_blocks(str(streamed.get("response_text") or "")).strip()
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output=f"Model API participant {normalize_agent(agent)} completed.",
            stream="lifecycle",
            metadata={
                "lifecycle": "request_completed",
                "provider": streamed.get("provider") or provider,
                "model": streamed.get("model") or model,
                "reply_chars": len(response),
                "streamed": True,
            },
        )
        if response:
            return response
        # content 通道为空（常见于推理模型答案全落在 reasoning）：先记兜底，再走非流式重试。
        reasoning_full = str(streamed.get("reasoning_text") or "")
        salvaged = salvage_reply_from_reasoning(reasoning_full)
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output="Streamed response was empty; retrying with a non-stream request.",
            stream="lifecycle",
            metadata={
                "lifecycle": "empty_stream_response",
                "provider": streamed.get("provider") or provider,
                "model": streamed.get("model") or model,
                "reasoning_chars": len(reasoning_full),
            },
        )

    result = request_model_review(
        message_content(message),
        skill_name=f"collaboration:{normalize_agent(agent)}",
        context=prompt,
        provider=provider,
        model=model,
        timeout=120,
    )
    if not result.ok:
        if salvaged:
            finalized = finalize_reply_from_reasoning(
                message, reasoning_full, provider=provider, model=model, agent=agent
            )
            if finalized:
                record_worker_event(
                    api,
                    agent,
                    message,
                    status="stream",
                    transport=transport,
                    dry_run=dry_run,
                    output="Reply body was missing; regenerated it from the reasoning draft.",
                    stream="lifecycle",
                    metadata={
                        "lifecycle": "finalized_from_reasoning",
                        "provider": provider,
                        "model": model,
                        "reply_chars": len(finalized),
                    },
                )
                return finalized
            return mark_salvaged_reply(message, salvaged)
        record_worker_event(
            api,
            agent,
            message,
            status="failed",
            transport=transport,
            dry_run=dry_run,
            error=result.error or result.status,
            output=result.error or result.status,
            stream="stderr",
            metadata={"lifecycle": "request_failed", "provider": result.provider, "model": result.model},
        )
        return f"模型参与者暂时不可用：{result.error or result.status}"
    response = strip_think_blocks(result.response_text).strip()
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        output=f"Model API participant {normalize_agent(agent)} completed.",
        stream="lifecycle",
        metadata={"lifecycle": "request_completed", "provider": result.provider, "model": result.model, "reply_chars": len(response), "streamed": False},
    )
    if response:
        return response
    if salvaged:
        finalized = finalize_reply_from_reasoning(
            message, reasoning_full, provider=provider, model=model, agent=agent
        )
        if finalized:
            record_worker_event(
                api,
                agent,
                message,
                status="stream",
                transport=transport,
                dry_run=dry_run,
                output="Reply body was missing; regenerated it from the reasoning draft.",
                stream="lifecycle",
                metadata={
                    "lifecycle": "finalized_from_reasoning",
                    "provider": provider,
                    "model": model,
                    "reply_chars": len(finalized),
                },
            )
            return finalized
        return mark_salvaged_reply(message, salvaged)
    return "模型参与者没有返回内容。"


def request_streaming_model_reply(
    prompt: str,
    assistant: dict[str, Any],
    *,
    provider: str,
    model: str,
    timeout: float,
    on_token,
    on_event,
) -> dict[str, Any]:
    selected = resolve_streaming_provider(assistant, provider=provider, model=model)
    if selected is None:
        on_event(
            "stream_unavailable",
            "Model API streaming is unavailable for this participant; falling back to non-stream request.",
            {"provider": provider, "model": model, "reason": "provider_not_configured"},
        )
        return {"ok": False, "reason": "provider_not_configured"}

    canonical = str(selected.provider or "").strip().lower().replace("-", "_")
    selected_model = model or selected.model
    request_params = model_request_params(assistant)
    if canonical in {"openai_compatible", "cloud_openai_compatible", "yundun", "yundun_openai_compatible", "lmstudio", "llamacpp"}:
        return stream_openai_compatible_reply(prompt, selected, selected_model, timeout=timeout, on_token=on_token, on_event=on_event, request_params=request_params)
    if canonical == "ollama":
        return stream_ollama_reply(prompt, selected, selected_model, timeout=timeout, on_token=on_token, on_event=on_event, request_params=request_params)

    on_event(
        "stream_unavailable",
        f"Provider {canonical or selected.provider} does not support collaboration token streaming yet; falling back to non-stream request.",
        {"provider": selected.provider, "model": selected_model, "reason": "unsupported_provider"},
    )
    return {"ok": False, "reason": "unsupported_provider", "provider": selected.provider, "model": selected_model}


STREAMING_OPENAI_COMPATIBLE_FAMILY = {"openai_compatible", "cloud_openai_compatible", "yundun", "yundun_openai_compatible", "lmstudio", "llamacpp"}


def resolve_streaming_provider(assistant: dict[str, Any], *, provider: str, model: str) -> ModelProviderConfig | None:
    provider_key = str(provider or "").strip().lower().replace("-", "_")
    requested_model = str(model or "").strip()
    candidates = discover_model_providers()
    if requested_model:
        # 同一 provider 名可能对应多个端点（本地 LM Studio 与云端 DeepSeek 都可注册为
        # openai_compatible）。优先选 model 精确匹配的候选，避免把模型名发到错误端点。
        for candidate in candidates:
            if not candidate.configured or str(candidate.model or "").strip() != requested_model:
                continue
            candidate_provider = str(candidate.provider or "").strip().lower().replace("-", "_")
            if not provider_key or candidate_provider == provider_key or (provider_key in STREAMING_OPENAI_COMPATIBLE_FAMILY and candidate_provider in STREAMING_OPENAI_COMPATIBLE_FAMILY):
                return candidate
    if provider_key or requested_model:
        for candidate in candidates:
            candidate_provider = str(candidate.provider or "").strip().lower().replace("-", "_")
            if provider_key and candidate_provider != provider_key:
                continue
            selected_model = requested_model or str(candidate.model or "").strip()
            return ModelProviderConfig(
                candidate.provider,
                selected_model,
                candidate.configured,
                candidate.endpoint,
                candidate.env_key,
                candidate.display_name,
                candidate.source,
                candidate.api_key,
            ) if candidate.configured else None
    for preferred in ("openai_compatible", "cloud_openai_compatible", "ollama", "llamacpp", "lmstudio"):
        match = next((item for item in candidates if str(item.provider or "").strip().lower().replace("-", "_") == preferred and item.configured), None)
        if match is not None:
            return match
    return next((item for item in candidates if item.configured), None)


def stream_openai_compatible_reply(prompt: str, provider: ModelProviderConfig, model: str, *, timeout: float, on_token, on_event, request_params: dict[str, Any] | None = None) -> dict[str, Any]:
    api_key = provider.api_key or os.getenv("OPENAI_API_KEY") or os.getenv("SPIRITKIN_OPENAI_API_KEY") or ""
    endpoint = f"{provider.endpoint.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a SpiritKin collaboration participant. Reply directly and concisely."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "stream": True,
    }
    if request_params:
        payload.update(request_params)
        payload["stream"] = True
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    on_event("request_stream_started", f"Streaming model API participant {provider.provider}.", {"provider": provider.provider, "model": model})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content_token, reasoning_token = openai_stream_delta_parts(data)
                if reasoning_token:
                    reasoning_chunks.append(reasoning_token)
                    on_token(reasoning_token, {"provider": provider.provider, "model": model, "source": "provider_stream", "channel": "reasoning"})
                if content_token:
                    chunks.append(content_token)
                    on_token(content_token, {"provider": provider.provider, "model": model, "source": "provider_stream"})
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, urllib.error.HTTPError):
            # 4xx 的真实原因（模型名不对/上下文超限等）在响应体里。
            try:
                error_text += f" | {exc.read().decode('utf-8', errors='replace')[:300]}"
            except OSError:
                pass
        on_event(
            "stream_unavailable",
            f"Streaming request failed: {error_text}",
            {"provider": provider.provider, "model": model, "reason": "stream_request_failed"},
        )
        return {"ok": False, "reason": "stream_request_failed", "error": error_text, "provider": provider.provider, "model": model}
    return {"ok": True, "provider": provider.provider, "model": model, "response_text": "".join(chunks), "reasoning_text": "".join(reasoning_chunks)}


def provider_reasoning_visibility(provider: str, model: str) -> str:
    """Classify whether a provider's reasoning stream is intended for callers."""
    identity = f"{provider or ''} {model or ''}".strip().lower()
    # A local runtime is not proof that raw reasoning_content is intended for
    # display. Only providers/models with an explicit caller-visible process
    # contract are exposed; all other reasoning becomes a progress summary.
    return "process" if "deepseek" in identity else "private"


def stream_token_metadata(meta: dict[str, Any], channel: str, provider: str, model: str) -> dict[str, Any]:
    """Build token metadata from the provider that actually owns the stream."""
    result = {"lifecycle": "token", **meta}
    if channel == "reasoning":
        # The assistant may be configured as generic openai_compatible and only
        # resolve to LM Studio after routing. Prefer the resolved stream identity.
        resolved_provider = str(meta.get("provider") or provider)
        resolved_model = str(meta.get("model") or model)
        result["reasoning_visibility"] = provider_reasoning_visibility(resolved_provider, resolved_model)
    return result


def openai_stream_delta_parts(data: dict[str, Any]) -> tuple[str, str]:
    """Return (content_token, reasoning_token) from one SSE chunk.

    Reasoning models (DeepSeek reasoner, Qwen thinking, etc.) stream hidden
    thinking as ``delta.reasoning_content`` before any ``delta.content``.
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    reasoning = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
    if "content" in delta:
        return str(delta.get("content") or ""), reasoning
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    return str(message.get("content") or ""), reasoning


def stream_ollama_reply(prompt: str, provider: ModelProviderConfig, model: str, *, timeout: float, on_token, on_event, request_params: dict[str, Any] | None = None) -> dict[str, Any]:
    endpoint = f"{provider.endpoint.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": "You are a SpiritKin collaboration participant. Reply directly and concisely."},
            {"role": "user", "content": prompt},
        ],
    }
    if request_params:
        payload.update(request_params)
        payload["stream"] = True
    req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    on_event("request_stream_started", f"Streaming Ollama participant {model}.", {"provider": provider.provider, "model": model})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message_part = data.get("message") if isinstance(data.get("message"), dict) else {}
                thinking = str(message_part.get("thinking") or "")
                if thinking:
                    reasoning_chunks.append(thinking)
                    on_token(thinking, {"provider": provider.provider, "model": model, "source": "provider_stream", "channel": "reasoning"})
                token = str(message_part.get("content") or data.get("response") or "")
                if token:
                    chunks.append(token)
                    on_token(token, {"provider": provider.provider, "model": model, "source": "provider_stream"})
                if data.get("done") is True:
                    break
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, urllib.error.HTTPError):
            # 4xx 的真实原因（模型名不对/上下文超限等）在响应体里。
            try:
                error_text += f" | {exc.read().decode('utf-8', errors='replace')[:300]}"
            except OSError:
                pass
        on_event(
            "stream_unavailable",
            f"Streaming request failed: {error_text}",
            {"provider": provider.provider, "model": model, "reason": "stream_request_failed"},
        )
        return {"ok": False, "reason": "stream_request_failed", "error": error_text, "provider": provider.provider, "model": model}
    return {"ok": True, "provider": provider.provider, "model": model, "response_text": "".join(chunks), "reasoning_text": "".join(reasoning_chunks)}


def structured_cli_output(command: str) -> bool:
    lowered = str(command or "").lower()
    return "--json" in lowered or "stream-json" in lowered


def classify_structured_cli_line(line: str) -> list[tuple[str, str, dict[str, Any]]] | None:
    """把 codex exec --json / claude -p stream-json 的事件行翻译成工作链步骤。

    返回 None 表示这行不是 JSON（按普通 stdout 处理）；返回空列表表示是
    已识别但无需展示的事件（丢弃，避免原始 JSON 刷屏）。
    步骤流名：command=执行命令，edit=编辑文件，reasoning=推理，token=回复正文。
    CLI 真正执行的工具事件必须携带稳定 tool_call_id + lifecycle，桌面才能
    把开始/结果配成同一条真实调用链；缺少原生 id 时保持普通事件，不伪造调用。
    """
    text = str(line or "").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    events: list[tuple[str, str, dict[str, Any]]] = []
    etype = str(data.get("type") or "").strip().lower()

    # ── Codex exec --json：item.* 事件 ──
    item = data.get("item") if isinstance(data.get("item"), dict) else None
    if item is not None:
        itype = str(item.get("item_type") or item.get("type") or "").strip().lower()
        if itype == "command_execution":
            command = str(item.get("command") or "").strip()
            tool_call_id = str(item.get("id") or item.get("item_id") or "").strip()
            tool_meta = {
                "tool_call_id": tool_call_id,
                "target": "external_cli",
                "operation": "command_execution",
                "command": command,
            } if tool_call_id else {}
            if etype.endswith("started") and command:
                events.append(("command", f"$ {command}", {"lifecycle": "tool_running", **tool_meta}))
            elif etype.endswith("completed"):
                summary = f"$ {command}" if command else "$ (command)"
                exit_code = item.get("exit_code")
                if exit_code is not None:
                    summary += f"\nexit {exit_code}"
                output = str(item.get("aggregated_output") or "").strip()
                if output:
                    summary += "\n" + output[-800:]
                lifecycle = "tool_failed" if exit_code not in (None, 0) else "tool_completed"
                events.append((
                    "command",
                    summary,
                    {
                        "lifecycle": lifecycle,
                        "exit_code": exit_code,
                        "command_output": output[-800:] if output else "",
                        **tool_meta,
                    },
                ))
            return events
        if itype in {"file_change", "patch_apply", "patch"}:
            if etype and not etype.endswith("completed"):
                return events
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            parts: list[str] = []
            for change in changes[:8]:
                if isinstance(change, dict):
                    path = str(change.get("path") or "").strip()
                    change_kind = str(change.get("kind") or "edit").strip()
                    if path:
                        parts.append(f"{change_kind} {path}")
            summary = "; ".join(parts) or str(item.get("path") or "").strip() or "files"
            tool_call_id = str(item.get("id") or item.get("item_id") or "").strip()
            metadata = {"lifecycle": "tool_completed" if tool_call_id else "file_edited"}
            if tool_call_id:
                metadata.update({
                    "tool_call_id": tool_call_id,
                    "target": "workspace",
                    "operation": itype,
                })
            events.append(("edit", f"Edited {summary}", metadata))
            return events
        if itype in {"reasoning", "agent_reasoning"}:
            body = str(item.get("text") or "").strip()
            if body and (not etype or etype.endswith("completed")):
                # Codex JSON reasoning items are user-facing summaries, not raw hidden CoT.
                events.append(("reasoning", body, {"reasoning_visibility": "summary"}))
            return events
        if itype in {"agent_message", "assistant_message"}:
            body = str(item.get("text") or "").strip()
            if body and (not etype or etype.endswith("completed")):
                events.append(("token", body, {}))
            return events
        return events

    # ── Claude Code stream-json：assistant 消息里的 text / thinking / tool_use 块 ──
    if etype == "assistant":
        message_part = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = message_part.get("content") if isinstance(message_part.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type") or "").strip().lower()
            if btype == "text":
                body = str(block.get("text") or "").strip()
                if body:
                    events.append(("token", body, {}))
            elif btype == "thinking":
                body = str(block.get("thinking") or "").strip()
                if body:
                    events.append(("reasoning", body, {"reasoning_visibility": "private"}))
            elif btype == "tool_use":
                name = str(block.get("name") or "tool").strip()
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                tool_call_id = str(block.get("id") or block.get("tool_use_id") or "").strip()
                tool_meta = {
                    "lifecycle": "tool_running",
                    "tool_call_id": tool_call_id,
                    "target": "external_cli",
                    "operation": name,
                } if tool_call_id else {"lifecycle": "tool_use", "tool": name}
                lowered = name.lower()
                if lowered == "bash":
                    command_text = str(tool_input.get("command") or "").strip()
                    events.append(("command", f"$ {command_text}", {**tool_meta, "command": command_text}))
                elif lowered in {"edit", "write", "multiedit", "notebookedit"}:
                    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "").strip()
                    events.append(("edit", f"Edited {path}" if path else f"Edited file ({name})", tool_meta))
                else:
                    brief = json.dumps(tool_input, ensure_ascii=False)[:200] if tool_input else ""
                    events.append(("command", f"{name} {brief}".strip(), tool_meta))
        return events
    if etype == "user":
        message_part = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = message_part.get("content") if isinstance(message_part.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict) or str(block.get("type") or "").strip().lower() != "tool_result":
                continue
            tool_call_id = str(block.get("tool_use_id") or block.get("id") or "").strip()
            body = block.get("content")
            if isinstance(body, list):
                body = "\n".join(
                    str(part.get("text") or "")
                    for part in body
                    if isinstance(part, dict) and str(part.get("text") or "").strip()
                )
            body_text = str(body or "").strip() or "Tool returned no visible output."
            lifecycle = "tool_failed" if bool(block.get("is_error")) else "tool_completed"
            metadata = {"lifecycle": lifecycle, "command_output": body_text[:1600]}
            if tool_call_id:
                metadata.update({
                    "tool_call_id": tool_call_id,
                    "target": "external_cli",
                    "operation": "tool_result",
                })
            events.append(("command", body_text[:1600], metadata))
        return events
    if etype == "result":
        body = str(data.get("result") or "").strip()
        if body:
            events.append(("token", body, {}))
        return events
    return events


def run_external_assistant_streaming(
    command: str,
    prompt: str,
    cwd_path: Path,
    assistant: dict[str, Any],
    message: dict[str, Any],
    *,
    api: str,
    agent: str,
    transport: str,
    dry_run: bool,
) -> str:
    timeout_seconds = max(1.0, float(assistant.get("timeout_seconds") or 120.0))
    # Trust boundary: same as run_external_assistant — `command` is operator
    # configuration, not message content. Do not pass route-bus text here.
    process = subprocess.Popen(
        command,
        cwd=str(cwd_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
        **hidden_subprocess_kwargs(new_process_group=True),
    )
    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        output=f"Started external assistant process for {normalize_agent(agent)}.",
        stream="lifecycle",
        metadata={
            "lifecycle": "process_started",
            "assistant_id": str(assistant.get("assistant_id") or ""),
            "cwd": str(cwd_path),
            "pid": process.pid,
        },
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    structured = structured_cli_output(command)
    structured_messages: list[str] = []

    def publish_stream(stream_name: str, line: str) -> None:
        text = line.strip()
        if not text:
            return
        # 发言泳道分流（见 run_api_assistant 同款）：后台草稿轮正文 token 改道起草泳道
        # （与真思考链分离）；队列轮由席位在首个可见正文产出时现场判定（谁先想完谁直播）。
        background_draft = bool(message.get("_background_draft"))
        speak_slot = message.get("_speak_slot")

        def cli_token_lane() -> str:
            if background_draft:
                return "draft"
            if isinstance(speak_slot, SpeakSlot):
                return speak_slot.lane()
            return "token"

        if structured and stream_name == "stdout":
            classified = classify_structured_cli_line(text)
            if classified is not None:
                for stream_kind, body, extra in classified:
                    if not body:
                        continue
                    if stream_kind == "token":
                        structured_messages.append(body)
                    record_worker_event(
                        api,
                        agent,
                        message,
                        status="stream",
                        transport=transport,
                        dry_run=dry_run,
                        output=body[:1600],
                        stream=cli_token_lane() if stream_kind == "token" else stream_kind,
                        metadata=extra or None,
                    )
                return
        outputs = text.split() if stream_name == "stderr" and len(text) <= 80 and len(text.split()) > 1 else [text]
        for output in outputs:
            record_worker_event(
                api,
                agent,
                message,
                status="stream",
                transport=transport,
                dry_run=dry_run,
                output=output[:1200],
                stream=stream_name,
            )

    def read_stream(pipe, stream_name: str, chunks: list[str], *, publish: bool, flush_on_whitespace: bool, max_buffer_chars: int = 240) -> None:
        if pipe is None:
            return
        buffer: list[str] = []

        def flush_buffer() -> None:
            if not buffer:
                return
            text = "".join(buffer)
            buffer.clear()
            if publish:
                publish_stream(stream_name, text)

        try:
            while True:
                chunk = pipe.read(1)
                if chunk == "":
                    break
                chunks.append(chunk)
                buffer.append(chunk)
                if chunk in "\r\n" or (flush_on_whitespace and chunk.isspace()) or len(buffer) >= max_buffer_chars:
                    flush_buffer()
            flush_buffer()
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    # 结构化 JSON CLI（codex --json / claude stream-json）按整行解析，普通 CLI 保持逐词流式。
    stdout_thread = threading.Thread(
        target=read_stream,
        args=(process.stdout, "stdout", stdout_chunks),
        kwargs={"publish": True, "flush_on_whitespace": not structured, "max_buffer_chars": 1_000_000 if structured else 240},
    )
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, "stderr", stderr_chunks), kwargs={"publish": False, "flush_on_whitespace": False})
    stdin_errors: list[str] = []

    def write_stdin() -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            stdin_errors.append(str(exc))
            try:
                process.stdin.close()
            except OSError:
                pass

    stdin_thread = threading.Thread(target=write_stdin, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        close_process_pipes(process)
        return_code = process.wait(timeout=10)
        timeout_message = f"assistant command timed out after {int(timeout_seconds)} seconds"
        record_worker_event(
            api,
            agent,
            message,
            status="failed",
            transport=transport,
            dry_run=dry_run,
            error=timeout_message,
            output=timeout_message,
            stream="stderr",
            metadata={"lifecycle": "process_timeout", "pid": process.pid},
        )
    stdin_thread.join(timeout=1)
    stdout_thread.join()
    stderr_thread.join()

    output = "".join(stdout_chunks).strip()
    if structured:
        parsed_reply = "\n\n".join(part for part in structured_messages if part).strip()
        if parsed_reply:
            # 结构化 CLI 的最终回复取解析出的 agent 消息，避免把原始 JSON 事件流发进对话。
            output = parsed_reply
    error = "".join(stderr_chunks).strip()
    stdin_error = "; ".join(stdin_errors)
    stderr_summary = summarize_stderr(error)
    if stdin_error and (return_code != 0 or not output):
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            error=stdin_error,
            output=f"Assistant process closed stdin before the prompt was fully delivered: {stdin_error}",
            stream="stderr",
            metadata={"lifecycle": "stdin_closed", "pid": process.pid, "return_code": return_code},
        )
    if error and len(error) <= 240:
        publish_stream("stderr", error)
    elif stderr_summary and return_code != 0:
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output=stderr_summary,
            stream="stderr",
            metadata={"lifecycle": "stderr_summary", "pid": process.pid, "return_code": return_code},
        )
    elif stderr_summary:
        record_worker_event(
            api,
            agent,
            message,
            status="stream",
            transport=transport,
            dry_run=dry_run,
            output=stderr_summary,
            stream="stderr",
            metadata={"lifecycle": "stderr_summary", "pid": process.pid, "return_code": return_code},
        )

    record_worker_event(
        api,
        agent,
        message,
        status="stream",
        transport=transport,
        dry_run=dry_run,
        error="" if return_code == 0 else f"assistant command exited with code {return_code}",
        output=f"External assistant process exited with code {return_code}.",
        stream="lifecycle",
        metadata={
            "lifecycle": "process_exited",
            "assistant_id": str(assistant.get("assistant_id") or ""),
            "cwd": str(cwd_path),
            "pid": process.pid,
            "return_code": return_code,
        },
    )

    if return_code != 0 and "timeout_message" in locals() and not output:
        output = timeout_message
    elif return_code != 0 and "timeout_message" in locals():
        output = f"{output}\n\n[stderr]\n{timeout_message}".strip()
    elif return_code != 0 and not output and stdin_error and error:
        output = f"Assistant command failed with exit code {return_code} after closing stdin.\n{error}".strip()
    elif return_code != 0 and not output and stdin_error:
        output = f"Assistant command failed with exit code {return_code} after closing stdin: {stdin_error}".strip()
    elif return_code != 0 and not output:
        output = f"Assistant command failed with exit code {return_code}.\n{error}".strip()
    elif return_code != 0 and error:
        output = f"{output}\n\n[stderr]\n{error}".strip()
    elif not output and stdin_error:
        output = f"Assistant process closed stdin before the prompt was fully delivered: {stdin_error}"
    elif error:
        print(f"assistant stderr ({assistant.get('assistant_id', 'assistant')}):\n{error}", file=sys.stderr, flush=True)
    return output or f"Assistant command exited with code {return_code} and produced no output."


def build_dry_run_reply(agent: str, message: dict[str, Any]) -> str:
    content = message_content(message)
    return f"[dry-run:{normalize_agent(agent)}] received {message.get('message_id', '')}: {content[:400]}"


def chunk_text(text: str, size: int) -> list[str]:
    body = str(text or "")
    if not body:
        return []
    width = max(20, int(size))
    return [body[index : index + width] for index in range(0, len(body), width)]


def summarize_stderr(text: str, max_chars: int = 1000) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    interesting = [line for line in lines if re.search(r"\b(error|warn|failed|exception|timeout)\b|错误|失败|警告|超时", line, re.IGNORECASE)]
    selected = interesting[-8:] if interesting else lines[-8:]
    summary = "\n".join(selected).strip()
    if len(summary) <= max_chars:
        return summary
    return summary[-max_chars:].strip()


def normalize_tool_call_params(target: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Normalize common model aliases to the executor's governed parameter schema."""

    normalized = dict(params or {})
    if str(target or "").strip().lower() != "local_pc" or str(operation or "").strip().lower() != "launch_app":
        return normalized
    if str(normalized.get("app_name") or "").strip():
        return normalized
    candidate = normalized.get("command") or normalized.get("app") or normalized.get("application")
    if isinstance(candidate, (list, tuple)):
        candidate = candidate[0] if candidate else ""
    candidate_text = str(candidate or "").strip()
    if candidate_text:
        normalized["app_name"] = candidate_text
    return normalized


def submit_tool_calls_from_reply(
    api: str,
    agent: str,
    message: dict[str, Any],
    reply: str,
    *,
    transport: str,
    dry_run: bool,
) -> int:
    calls = extract_tool_calls(reply)
    if not calls:
        return 0
    submitted = 0
    envelope = message_envelope(message)
    origin_metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
    permission_metadata = {
        "permission_mode": origin_metadata.get("permission_mode"),
        "full_access_granted": origin_metadata.get("full_access_granted", False),
    }
    for call in calls[:8]:
        target = str(call.get("target") or "").strip()
        operation = str(call.get("operation") or call.get("name") or "").strip()
        if not target or not operation:
            continue
        params = call.get("params") if isinstance(call.get("params"), dict) else call.get("arguments")
        if not isinstance(params, dict):
            params = {}
        params = normalize_tool_call_params(target, operation, params)
        try:
            requested = request_json(
                api,
                "/desktop/collaboration",
                {
                    "action": "request_tool_call",
                    "agent": normalize_agent(agent),
                    "message_id": tool_call_origin_message_id(message),
                    "thread_id": message_thread_id(message),
                    "task_id": message_task_id(message),
                    "target": target,
                    "operation": operation,
                    "params": params,
                    "reason": str(call.get("reason") or call.get("summary") or "requested by collaboration participant"),
                    "metadata": {
                        "source": "assistant_reply_json",
                        "transport": transport,
                        "dry_run": bool(dry_run),
                        **permission_metadata,
                    },
                },
            )
            submitted += 1
            requested_result = requested.get("agent_route_bus_tool_call") if isinstance(requested, dict) else {}
            requested_result = requested_result if isinstance(requested_result, dict) else {}
            tool_call = requested_result.get("tool_call") if isinstance(requested_result.get("tool_call"), dict) else {}
            requires_review = bool(requested_result.get("requires_review", tool_call.get("requires_review", True)))
            is_new_request = bool(requested_result.get("requested", not requested_result.get("deduplicated", False)))
            if is_new_request and not requires_review and str(tool_call.get("tool_call_id") or "").strip() and not dry_run:
                request_json(
                    api,
                    "/desktop/collaboration",
                    {
                        "action": "execute_tool_call",
                        "tool_call_id": str(tool_call.get("tool_call_id") or ""),
                        "actor": normalize_agent(agent),
                    },
                )
        except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            record_worker_event(
                api,
                agent,
                message,
                status="failed",
                transport=transport,
                dry_run=dry_run,
                error=f"tool call submission failed: {exc}",
                output=f"Tool call submission failed: {exc}",
                stream="stderr",
            )
    return submitted


def strip_submitted_tool_call_payloads(text: str, submitted_count: int) -> str:
    """Keep the human reply clean after the machine-readable call was consumed."""

    body = str(text or "")

    def remove_fence(match: re.Match[str]) -> str:
        candidate = match.group("body")
        return "" if extract_tool_calls(candidate) else match.group(0)

    body = re.sub(
        r"```(?:json)?\s*(?P<body>[\s\S]*?)```",
        remove_fence,
        body,
        flags=re.IGNORECASE,
    )
    for start, end, _candidate in reversed(_inline_tool_json_candidates(body)):
        body = body[:start] + body[end:]
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body or f"已提交 {submitted_count} 个 Computer Use 调用，执行状态会实时回传。"


def _tool_calls_from_candidate(candidate: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if isinstance(candidate, dict):
        if isinstance(candidate.get("tool_call"), dict):
            calls.append(dict(candidate["tool_call"]))
        elif isinstance(candidate.get("spiritkin_tool_call"), dict):
            calls.append(dict(candidate["spiritkin_tool_call"]))
        elif isinstance(candidate.get("tool_calls"), list):
            calls.extend(dict(item) for item in candidate["tool_calls"] if isinstance(item, dict))
        elif "target" in candidate and ("operation" in candidate or "name" in candidate):
            calls.append(dict(candidate))
    elif isinstance(candidate, list):
        calls.extend(dict(item) for item in candidate if isinstance(item, dict))
    return calls


def _inline_tool_json_candidates(text: str) -> list[tuple[int, int, Any]]:
    body = str(text or "")
    decoder = json.JSONDecoder()
    matches: list[tuple[int, int, Any]] = []
    cursor = 0
    while cursor < len(body):
        start = body.find("{", cursor)
        if start < 0:
            break
        try:
            candidate, consumed = decoder.raw_decode(body[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        end = start + consumed
        if _tool_calls_from_candidate(candidate):
            matches.append((start, end, candidate))
            cursor = end
        else:
            cursor = start + 1
    return matches


def extract_tool_calls(text: str) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    body = str(text or "")
    for match in re.finditer(r"```(?:json)?\s*(?P<body>[\s\S]*?)```", body, flags=re.IGNORECASE):
        parsed = _parse_json_candidate(match.group("body"))
        if parsed is not None:
            candidates.append(parsed)
    stripped = body.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        parsed = _parse_json_candidate(stripped)
        if parsed is not None:
            candidates.append(parsed)
    candidates.extend(candidate for _start, _end, candidate in _inline_tool_json_candidates(body))

    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        for call in _tool_calls_from_candidate(candidate):
            signature = json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if signature in seen:
                continue
            seen.add(signature)
            calls.append(call)
    return calls


def _parse_json_candidate(text: str) -> Any:
    try:
        return json.loads(str(text or "").strip())
    except json.JSONDecodeError:
        return None


def message_envelope(message: dict[str, Any]) -> dict[str, Any]:
    envelope = message.get("agent_envelope")
    if isinstance(envelope, dict):
        return dict(envelope)
    if str(message.get("schema_version") or "") == "spiritkin.agent_protocol.v1":
        return dict(message)
    if "sender" in message or "recipient" in message or "message_type" in message or "context_id" in message:
        return dict(message)
    return {}


def message_content(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    return str(envelope.get("content") or message.get("content") or "").strip()


def message_sender(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    return str(envelope.get("sender") or message.get("from_agent") or message.get("from_model") or "").strip()


def message_recipient(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    recipient = str(envelope.get("recipient") or message.get("to_agent") or message.get("to_model") or "").strip()
    if recipient:
        return recipient
    recipients = message.get("to_agents")
    if isinstance(recipients, (list, tuple)):
        return ", ".join(str(item).strip() for item in recipients if str(item).strip())
    return ""


def message_type(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    return str(envelope.get("message_type") or message.get("role") or "").strip()


def message_context_id(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    return str(envelope.get("context_id") or message.get("thread_id") or message.get("task_id") or "").strip()


def message_thread_id(message: dict[str, Any]) -> str:
    thread_id = str(message.get("thread_id") or "").strip()
    if thread_id:
        return thread_id
    envelope = message_envelope(message)
    metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
    metadata_thread = str(metadata.get("thread_id") or "").strip()
    if metadata_thread:
        return metadata_thread
    return message_context_id(message) or message_task_id(message)


def message_task_id(message: dict[str, Any]) -> str:
    return str(message.get("task_id") or "").strip()


def message_context_pack_path(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    for artifact in envelope.get("artifacts") or []:
        if isinstance(artifact, dict) and str(artifact.get("kind") or "") == "context_pack":
            path = str(artifact.get("path") or "").strip()
            if path:
                return path
    return str(message.get("context_pack_path") or "").strip()


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def close_process_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is None:
            continue
        try:
            pipe.close()
        except OSError:
            pass


def hidden_subprocess_kwargs(*, new_process_group: bool = False) -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"startupinfo": startupinfo, "creationflags": creationflags}


if __name__ == "__main__":
    raise SystemExit(main())
