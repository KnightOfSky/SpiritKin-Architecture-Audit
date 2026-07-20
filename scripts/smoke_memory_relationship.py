from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.agents.base import AgentContext
from backend.knowledge.embedding import get_embedding_service
from backend.memory.long_term import JsonlLongTermMemoryStore
from backend.memory.relationship import RelationshipStore
from backend.orchestrator.prompt_context import build_long_term_memory_context, build_relationship_context
from backend.orchestrator.response_phase import SoulResponsePhase


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_smoke(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    embedding = get_embedding_service("llamacpp", refresh=True)
    memory_path = output_dir / "long-term-memory.jsonl"
    for generated in (
        memory_path,
        output_dir / "long-term-memory.conflicts.jsonl",
        output_dir / "relationship.json",
        output_dir / "smoke-report.json",
    ):
        generated.unlink(missing_ok=True)
    store = JsonlLongTermMemoryStore(memory_path, embedding_provider=embedding)
    preference = store.add(
        "preference",
        "用户偏好先给结论，再给必要步骤，避免冗长铺垫。",
        importance=1.0,
        metadata={"source": "m1_seven_day_smoke"},
    )
    seven_days_ago = time.time() - 7 * 24 * 60 * 60
    aged = replace(preference, timestamp=seven_days_ago, last_recalled=seven_days_ago)
    with memory_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(aged.snapshot(), ensure_ascii=False) + "\n")

    reloaded_memory = JsonlLongTermMemoryStore(memory_path, embedding_provider=embedding)
    recalled = reloaded_memory.recall("回答时请先给结论", top_k=5)
    if not recalled or recalled[0].entry_id != preference.entry_id:
        raise RuntimeError("seven-day preference was not recalled as the top memory")
    memory_hits = [item.snapshot() for item in recalled]
    memory_context = build_long_term_memory_context({"long_term_memory_hits": memory_hits})
    memory_prompts: list[str] = []
    SoulResponsePhase(
        lambda prompt, **_: memory_prompts.append(prompt) or "我会先给结论。<emotion:neutral><action:nod>"
    ).respond(AgentContext(user_input="以后回答怎么组织？", metadata={"long_term_memory_hits": memory_hits}))
    if not memory_prompts or recalled[0].content not in memory_prompts[0]:
        raise RuntimeError("recalled preference was not injected into the Soul prompt")

    relationship_path = output_dir / "relationship.json"
    relationship = RelationshipStore(relationship_path)
    created = relationship.observe_user_input("以后请不要再叫我审计昵称")
    reloaded_relationship = RelationshipStore(relationship_path)
    repeated = reloaded_relationship.observe_user_input("请不要再叫我审计昵称")
    boundary_snapshot = reloaded_relationship.context_snapshot()
    relationship_context = build_relationship_context({"relationship": boundary_snapshot})
    relationship_prompts: list[str] = []
    SoulResponsePhase(
        lambda prompt, **_: relationship_prompts.append(prompt) or "明白。<emotion:neutral><action:nod>"
    ).respond(AgentContext(user_input="继续", metadata={"relationship": boundary_snapshot}))
    if not relationship_prompts or "叫我审计昵称" not in relationship_prompts[0]:
        raise RuntimeError("active relationship boundary was not injected into the Soul prompt")
    released = reloaded_relationship.observe_user_input("现在可以再叫我审计昵称")
    final_relationship = RelationshipStore(relationship_path).snapshot()
    if final_relationship["active_boundary_count"] != 0:
        raise RuntimeError("released relationship boundary remained active after reload")

    embedding_state = embedding.snapshot()
    if embedding_state.get("degraded") or int(embedding_state.get("dimensions") or 0) != 768:
        raise RuntimeError(f"real llama.cpp embedding was not used: {embedding_state}")
    report: dict[str, object] = {
        "ok": True,
        "memory": {
            "entry_id": preference.entry_id,
            "age_days": 7,
            "top_recalled_entry_id": recalled[0].entry_id,
            "activation": recalled[0].activation,
            "state": recalled[0].memory_state,
            "prompt_injected": recalled[0].content in memory_context and recalled[0].content in memory_prompts[0],
        },
        "embedding": {
            "provider": embedding_state.get("provider"),
            "dimensions": embedding_state.get("dimensions"),
            "degraded": embedding_state.get("degraded"),
            "calls": embedding_state.get("calls"),
        },
        "relationship": {
            "created_signal": created.get("signal"),
            "deduplicated": repeated.get("created") is False,
            "repeated_count": boundary_snapshot["boundaries"][0]["repeated_count"],
            "prompt_injected": "叫我审计昵称" in relationship_context and "叫我审计昵称" in relationship_prompts[0],
            "release_signal": released.get("signal"),
            "active_after_release_reload": final_relationship["active_boundary_count"],
        },
    }
    _write_json(output_dir / "smoke-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test semantic memory and relationship persistence without user state.")
    parser.add_argument("--output-dir", type=Path, default=Path("tmp/memory-relationship-smoke-20260718"))
    args = parser.parse_args()
    report = run_smoke(args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
