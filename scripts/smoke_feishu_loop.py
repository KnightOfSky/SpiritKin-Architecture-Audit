from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.runtime import InteractionInput, SpiritKinRuntime
from backend.orchestrator.agent_cluster import AgentCluster


def _offline_llm(prompt: str) -> str:
    raise RuntimeError("smoke should not call the LLM for Feishu send intents")


def _print_events(runtime: SpiritKinRuntime, label: str, reply) -> None:
    print(f"\n[{label}] reply: {reply.text}")
    print(f"[{label}] spoken: {runtime._build_speech_output(reply)}")
    for event in runtime.build_response_events(reply):
        payload = event.get("payload", {})
        if event["type"] == "assistant.execution_updated":
            data = payload.get("data", {})
            print(f"[{label}] event={event['type']} dry_run={data.get('dry_run')} recipient={data.get('recipient')}")
        elif event["type"] == "assistant.confirmation_requested":
            print(f"[{label}] event={event['type']} target={payload.get('pending_target')} op={payload.get('pending_operation')}")
        else:
            print(f"[{label}] event={event['type']}")


def main() -> int:
    os.environ.setdefault("SPIRIT_FEISHU_DRY_RUN", "1")
    os.environ.setdefault("SPIRIT_FEISHU_CONTACTS_JSON", '{"张三":"user_id:demo_zhangsan"}')

    cluster = AgentCluster(llm_client=_offline_llm)
    runtime = SpiritKinRuntime(agent=cluster, emit_runtime_events=False)

    first = InteractionInput(text="给张三发飞书，说会议改到三点", channel="voice", visual_context="桌面可见飞书窗口")
    print("[input]", runtime.build_input_payload(first))
    confirmation = runtime.handle_input(first)
    if confirmation is None or not confirmation.requires_confirmation:
        print("[FAIL] expected confirmation request")
        return 1
    _print_events(runtime, "confirm", confirmation)

    second = InteractionInput(text="确认执行", channel="voice")
    print("\n[input]", runtime.build_input_payload(second))
    execution = runtime.handle_input(second)
    if execution is None or execution.metadata.get("response_kind") != "execution_result":
        print("[FAIL] expected execution result")
        return 1
    _print_events(runtime, "execute", execution)

    data = execution.metadata["execution"]["data"]
    if not data.get("dry_run") or data.get("recipient") != "张三":
        print("[FAIL] unexpected Feishu execution payload:", data)
        return 1

    print("\n[OK] Feishu voice-style dry-run loop completed: ear(text/ASR output) -> intent -> confirmation -> hand(API executor) -> mouth/face events")
    return 0


if __name__ == "__main__":
    sys.exit(main())