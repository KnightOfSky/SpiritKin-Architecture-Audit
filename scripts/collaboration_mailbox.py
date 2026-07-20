from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API = os.getenv("SPIRITKIN_DESKTOP_API", "http://127.0.0.1:8788")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SpiritKin desktop collaboration mailbox")
    parser.add_argument("--api", default=DEFAULT_API, help="Desktop command gateway URL")
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    send_parser = subparsers.add_parser("send", help="Post an Agent collaboration message")
    add_message_args(send_parser)

    reply_parser = subparsers.add_parser("reply", help="Reply to an existing message")
    reply_parser.add_argument("--message-id", required=True)
    reply_parser.add_argument("--from-agent", "--from-model", "--from", dest="from_agent", required=True)
    reply_parser.add_argument("--content", default="")
    reply_parser.add_argument("--content-file", default="")
    reply_parser.add_argument("--role", default="answer")
    reply_parser.add_argument("--context-pack-path", default="")

    inbox_parser = subparsers.add_parser("inbox", help="List messages for a model")
    inbox_parser.add_argument("--agent", "--model", dest="agent", default="claude_code")
    inbox_parser.add_argument("--thread-id", default="")
    inbox_parser.add_argument("--task-id", default="")
    inbox_parser.add_argument("--transport", choices=["route_bus", "legacy_inbox"], default="route_bus")
    inbox_parser.add_argument("--unread", action="store_true")
    inbox_parser.add_argument("--include-acked", "--include-consumed", dest="include_acked", action="store_true")
    inbox_parser.add_argument("--limit", type=int, default=40)

    read_parser = subparsers.add_parser("read", help="Mark a message as read")
    read_parser.add_argument("--message-id", required=True)
    read_parser.add_argument("--reader", "--agent", "--model", dest="reader", required=True)
    read_parser.add_argument("--transport", choices=["route_bus", "legacy_inbox"], default="route_bus")

    watch_parser = subparsers.add_parser("watch", help="Poll inbox and print new messages")
    watch_parser.add_argument("--agent", "--model", dest="agent", default="claude_code")
    watch_parser.add_argument("--thread-id", default="")
    watch_parser.add_argument("--task-id", default="")
    watch_parser.add_argument("--transport", choices=["route_bus", "legacy_inbox"], default="route_bus")
    watch_parser.add_argument("--interval", type=float, default=3.0)
    watch_parser.add_argument("--include-read", "--include-acked", "--include-consumed", dest="include_read", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show route bus worker status without consuming messages")
    status_parser.add_argument("--agent", "--model", dest="agent", action="append", default=[])
    status_parser.add_argument("--thread-id", default="")
    status_parser.add_argument("--task-id", default="")
    status_parser.add_argument("--limit", type=int, default=200)

    pack_parser = subparsers.add_parser("pack", help="Build a task context pack")
    pack_parser.add_argument("--task-id", default="")
    pack_parser.add_argument("--include-file", action="append", default=[])
    pack_parser.add_argument("--max-chars-per-file", type=int, default=3000)

    subparsers.add_parser("snapshot", help="Print collaboration snapshot")

    args = parser.parse_args(argv)
    api = str(args.api).rstrip("/")

    try:
        if args.command == "send":
            content = read_content(args)
            if not content:
                raise SystemExit("message content is required")
            payload = {
                "action": "request_model_review" if args.role == "review_request" else "post_message",
                "task_id": args.task_id,
                "thread_id": args.thread_id or args.task_id,
                "from_agent": args.from_agent,
                "to_agents": args.to_agent or ["claude_code"],
                "role": args.role,
                "content": content,
                "context_pack_path": args.context_pack_path,
            }
            result = post(api, "/desktop/collaboration", payload)
            return print_result(result, args.json)

        if args.command == "reply":
            snapshot = get(api, "/desktop/collaboration")
            parent = find_message(snapshot.get("collaboration", {}), args.message_id)
            if parent is None:
                raise SystemExit(f"message not found: {args.message_id}")
            content = read_content(args)
            if not content:
                raise SystemExit("reply content is required")
            payload = {
                "action": "post_message",
                "task_id": parent.get("task_id", ""),
                "thread_id": message_thread_id(parent),
                "from_agent": args.from_agent,
                "to_agents": [message_sender(parent) or "all"],
                "role": args.role,
                "content": content,
                "context_pack_path": args.context_pack_path or message_context_pack_path(parent),
                "parent_message_id": args.message_id,
            }
            result = post(api, "/desktop/collaboration", payload)
            return print_result(result, args.json)

        if args.command == "inbox":
            messages = list_mailbox_messages(
                api,
                args.agent,
                args.task_id,
                args.thread_id,
                transport=args.transport,
                include_consumed=bool(args.include_acked or (args.transport == "legacy_inbox" and not args.unread)),
                limit=args.limit,
            )
            return print_messages(messages, args.json)

        if args.command == "read":
            result = mark_message_consumed(api, args.reader, args.message_id, transport=args.transport)
            return print_result(result, args.json)

        if args.command == "watch":
            return watch_inbox(api, args.agent, args.task_id, args.thread_id, args.transport, args.interval, args.include_read, args.json)

        if args.command == "status":
            result = post(
                api,
                "/desktop/collaboration",
                {
                    "action": "agent_route_bus_worker_status",
                    "agents": args.agent,
                    "task_id": args.task_id,
                    "thread_id": args.thread_id,
                    "limit": args.limit,
                },
            )
            return print_worker_status(result, args.json)

        if args.command == "pack":
            result = post(
                api,
                "/desktop/collaboration",
                {
                    "action": "build_context_pack",
                    "task_id": args.task_id,
                    "include_files": args.include_file,
                    "max_chars_per_file": args.max_chars_per_file,
                },
            )
            return print_result(result, args.json)

        if args.command == "snapshot":
            result = get(api, "/desktop/collaboration")
            return print_result(result, args.json)

    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"desktop collaboration API unavailable: {exc}", file=sys.stderr)
        return 2
    return 1


def add_message_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-id", default="")
    parser.add_argument("--thread-id", default="")
    parser.add_argument("--from-agent", "--from-model", "--from", dest="from_agent", required=True)
    parser.add_argument("--to-agent", "--to-model", "--to", dest="to_agent", action="append", default=[])
    parser.add_argument("--role", default="question", choices=["question", "answer", "review_request", "review_result", "note"])
    parser.add_argument("--content", default="")
    parser.add_argument("--content-file", default="")
    parser.add_argument("--context-pack-path", default="")


def request(api: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{api}{path}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    token = os.getenv("SPIRITKIN_MOBILE_TOKEN", "").strip()
    if token:
        req.add_header("X-SpiritKin-Token", token)
    with urllib.request.urlopen(req, timeout=15) as response:
        text = response.read().decode("utf-8", errors="replace")
    result = json.loads(text)
    if not result.get("ok", True):
        raise SystemExit(f"{result.get('error', 'request failed')}: {result.get('detail', '')}".strip())
    return result


def get(api: str, path: str) -> dict[str, Any]:
    return request(api, path)


def post(api: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request(api, path, payload)


def read_content(args: argparse.Namespace) -> str:
    if getattr(args, "content_file", ""):
        with open(args.content_file, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    content = str(getattr(args, "content", "") or "")
    if content == "-":
        return sys.stdin.read().strip()
    if content:
        return content.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def find_message(collaboration: dict[str, Any], message_id: str) -> dict[str, Any] | None:
    for message in collaboration.get("recent_messages") or []:
        if str(message.get("message_id") or "") == message_id:
            return dict(message)
    return None


def print_result(result: dict[str, Any], raw_json: bool) -> int:
    if raw_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if "message" in result:
        print_message(result["message"])
    elif "agent_route_bus_ack" in result:
        ack = result["agent_route_bus_ack"].get("ack", {}) if isinstance(result.get("agent_route_bus_ack"), dict) else {}
        print("acked {message_id} for {consumer}".format(message_id=ack.get("message_id", ""), consumer=ack.get("consumer", "")))
    elif "context_pack" in result:
        print(result["context_pack"].get("pack_path", ""))
    elif "collaboration" in result:
        overview = result["collaboration"].get("overview", {})
        print(
            "tasks={active}/{total} messages={unread}/{messages} claims={claims}".format(
                active=overview.get("active_task_count", 0),
                total=overview.get("task_count", 0),
                unread=overview.get("unread_message_count", 0),
                messages=overview.get("message_count", 0),
                claims=overview.get("active_file_claim_count", 0),
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def list_mailbox_messages(
    api: str,
    agent: str,
    task_id: str,
    thread_id: str,
    *,
    transport: str,
    include_consumed: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if transport == "legacy_inbox":
        result = post(
            api,
            "/desktop/collaboration",
            {
                "action": "list_messages",
                "to_agent": agent,
                "task_id": task_id,
                "thread_id": thread_id,
                "include_read": include_consumed,
                "limit": limit,
            },
        )
        return [dict(item) for item in result.get("messages") or [] if isinstance(item, dict)]
    result = post(
        api,
        "/desktop/collaboration",
        {
            "action": "list_agent_route_bus_messages",
            "to_agent": agent,
            "consumer": agent,
            "task_id": task_id,
            "thread_id": thread_id,
            "include_acked": include_consumed,
            "include_audit": False,
            "limit": limit,
        },
    )
    route_bus = result.get("agent_route_bus_messages") if isinstance(result.get("agent_route_bus_messages"), dict) else {}
    return [dict(item) for item in route_bus.get("messages") or [] if isinstance(item, dict)]


def mark_message_consumed(api: str, reader: str, message_id: str, *, transport: str) -> dict[str, Any]:
    if transport == "legacy_inbox":
        return post(api, "/desktop/collaboration", {"action": "mark_message_read", "message_id": message_id, "reader": reader})
    return post(
        api,
        "/desktop/collaboration",
        {
            "action": "ack_agent_route_bus_message",
            "message_id": message_id,
            "consumer": reader,
            "note": "collaboration_mailbox_read",
        },
    )


def print_messages(messages: list[dict[str, Any]], raw_json: bool) -> int:
    if raw_json:
        print(json.dumps({"messages": messages}, ensure_ascii=False, indent=2))
        return 0
    if not messages:
        print("no messages")
        return 0
    for message in messages:
        print_message(message)
    return 0


def print_worker_status(result: dict[str, Any], raw_json: bool) -> int:
    if raw_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    status = result.get("agent_route_bus_worker_status") if isinstance(result.get("agent_route_bus_worker_status"), dict) else {}
    if not status:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(
        "route_bus_worker real={real} pending={pending} acked={ack} dry_run={dry}".format(
            real=status.get("real_worker_status", "unknown"),
            pending=status.get("pending_count", 0),
            ack=status.get("ack_count", 0),
            dry=status.get("dry_run_available", False),
        )
    )
    for item in status.get("agents") or []:
        if not isinstance(item, dict):
            continue
        external = item.get("external_worker") if isinstance(item.get("external_worker"), dict) else {}
        assistant = external.get("external_assistant") if isinstance(external.get("external_assistant"), dict) else {}
        latest = item.get("latest_pending_message") if isinstance(item.get("latest_pending_message"), dict) else {}
        latest_event = item.get("latest_worker_event") if isinstance(item.get("latest_worker_event"), dict) else {}
        latest_id = str(latest.get("message_id") or "")
        latest_from = message_sender(latest) if latest else ""
        event_status = str(latest_event.get("status") or "--")
        event_error = str(latest_event.get("error") or "")
        print(
            "{agent}: real={real} pending={pending} acked={ack} assistant={assistant_status} command_found={found} latest={latest}{sender} last_event={event}{error}".format(
                agent=item.get("agent", ""),
                real=item.get("real_worker_status", "unknown"),
                pending=item.get("pending_count", 0),
                ack=item.get("ack_count", 0),
                assistant_status=assistant.get("status", "unknown"),
                found=assistant.get("command_executable_found", False),
                latest=latest_id or "--",
                sender=f" from={latest_from}" if latest_from else "",
                event=event_status,
                error=f" error={event_error[:160]}" if event_error else "",
            )
        )
    return 0


def print_message(message: dict[str, Any]) -> None:
    from_agent = message_sender(message)
    to_text = message_recipient_text(message)
    print(
        "[{status}] {message_id} thread={thread_id} task={task_id} {from_agent}->{to_agents} role={role}".format(
            status=message.get("status", "open"),
            message_id=message.get("message_id", ""),
            thread_id=message_thread_id(message),
            task_id=message.get("task_id", ""),
            from_agent=from_agent,
            to_agents=to_text,
            role=message_type(message),
        )
    )
    context_pack = message_context_pack_path(message)
    if context_pack:
        print(f"context_pack={context_pack}")
    print(message_content(message))
    print()


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


def message_type(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    return str(envelope.get("message_type") or message.get("role") or "").strip()


def message_thread_id(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    metadata = envelope.get("metadata") if isinstance(envelope.get("metadata"), dict) else {}
    return str(message.get("thread_id") or metadata.get("thread_id") or envelope.get("context_id") or message.get("task_id") or "").strip()


def message_recipient_text(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    recipient = str(envelope.get("recipient") or "").strip()
    if recipient:
        return recipient
    to_agents = message.get("to_agents") or [message.get("to_model", "")]
    return ",".join(str(item) for item in to_agents if str(item))


def message_context_pack_path(message: dict[str, Any]) -> str:
    envelope = message_envelope(message)
    for artifact in envelope.get("artifacts") or []:
        if isinstance(artifact, dict) and str(artifact.get("kind") or "") == "context_pack":
            path = str(artifact.get("path") or "").strip()
            if path:
                return path
    return str(message.get("context_pack_path") or "").strip()


def watch_inbox(
    api: str,
    agent: str,
    task_id: str,
    thread_id: str,
    transport: str,
    interval: float,
    include_consumed: bool,
    raw_json: bool,
) -> int:
    seen: set[str] = set()
    while True:
        messages = list_mailbox_messages(
            api,
            agent,
            task_id,
            thread_id,
            transport=transport,
            include_consumed=include_consumed,
            limit=80,
        )
        fresh = [message for message in messages if str(message.get("message_id") or "") not in seen]
        for message in messages:
            message_id = str(message.get("message_id") or "")
            if message_id:
                seen.add(message_id)
        if fresh:
            print_messages(fresh, raw_json)
            sys.stdout.flush()
        time.sleep(max(0.5, interval))


if __name__ == "__main__":
    raise SystemExit(main())
