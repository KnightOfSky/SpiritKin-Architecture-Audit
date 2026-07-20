from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.getenv("SPIRITKIN_WORKSPACE_ROOT", str(ROOT))).resolve()
SOURCE_CANDIDATES = WORKSPACE_ROOT / "state" / "ecommerce_tasks" / "skill_candidates.jsonl"


def _ensure_import_path() -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def patch_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    patched = json.loads(json.dumps(candidate, ensure_ascii=False))
    name = str(patched.get("name") or "")
    if name == "ecommerce.pdd_mobile_link_intake.workflow":
        patched["steps"] = [
            {
                "tool_name": "ecommerce.task_queue.ingest_mobile_links",
                "arguments": {"include_latest": "{{include_latest}}", "include_test_links": False},
                "description": "调用结构化电商队列工具导入手机链接。",
                "optional": False,
            }
        ]
        patched["tool_allowlist"] = ["ecommerce.task_queue.ingest_mobile_links"]
    elif name == "ecommerce.ocr_artifact_cleanup.workflow":
        patched["steps"] = [
            {
                "tool_name": "ecommerce.task_queue.cleanup_temp",
                "arguments": {"older_than_hours": "{{older_than_hours}}", "dry_run": "{{dry_run}}"},
                "description": "调用结构化电商队列工具清理临时 OCR 产物。",
                "optional": False,
            }
        ]
        patched["tool_allowlist"] = ["ecommerce.task_queue.cleanup_temp"]
    elif name == "ecommerce.browser_extension_productdata.workflow":
        patched["steps"] = [
            {
                "tool_name": "ecommerce.task_queue.attach_productdata",
                "arguments": {
                    "task_id": "{{task_id}}",
                    "product_data_json": "{{product_data_json}}",
                    "control_plane_artifact_id": "{{control_plane_artifact_id}}",
                    "project_root": "{{project_root}}",
                    "state_dir": "{{state_dir}}",
                },
                "description": "挂载浏览器扩展 productData，并同步任务完整性门禁。",
                "optional": False,
            }
        ]
        patched["tool_allowlist"] = ["ecommerce.task_queue.attach_productdata"]
        metadata = dict(patched.get("metadata") or {})
        metadata.pop("blocked_reason", None)
        metadata["native_tool_ready"] = True
        patched["metadata"] = metadata
    metadata = dict(patched.get("metadata") or {})
    metadata["imported_by"] = "scripts/import_ecommerce_rpa_skills.py"
    metadata["import_source"] = str(SOURCE_CANDIDATES)
    metadata["status"] = metadata.get("status") or "candidate"
    patched["metadata"] = metadata
    return patched


def spec_from_dict(snapshot: dict[str, Any]):
    from backend.skills.base import SkillSpec, SkillStepSpec

    steps = tuple(
        SkillStepSpec(
            tool_name=str(step.get("tool_name") or ""),
            arguments=dict(step.get("arguments") or {}),
            description=str(step.get("description") or ""),
            optional=bool(step.get("optional", False)),
        )
        for step in snapshot.get("steps") or []
        if isinstance(step, dict) and str(step.get("tool_name") or "").strip()
    )
    return SkillSpec(
        name=str(snapshot.get("name") or ""),
        description=str(snapshot.get("description") or ""),
        trigger_intents=tuple(str(item) for item in snapshot.get("trigger_intents") or []),
        input_schema=dict(snapshot.get("input_schema") or {}),
        preconditions=tuple(str(item) for item in snapshot.get("preconditions") or []),
        steps=steps,
        tool_allowlist=tuple(str(item) for item in snapshot.get("tool_allowlist") or []),
        risk_level=str(snapshot.get("risk_level") or "low"),
        confirmation_policy=str(snapshot.get("confirmation_policy") or "risk_based"),
        rollback_strategy=str(snapshot.get("rollback_strategy") or "manual_review"),
        success_criteria=tuple(str(item) for item in snapshot.get("success_criteria") or []),
        memory_policy=str(snapshot.get("memory_policy") or "record_summary"),
        eval_cases=tuple(str(item) for item in snapshot.get("eval_cases") or []),
        version=str(snapshot.get("version") or "0.1.0"),
        usage_count=int(snapshot.get("usage_count") or 0),
        metadata=dict(snapshot.get("metadata") or {}),
    )


def import_candidates(source: Path, store_path: Path) -> dict[str, Any]:
    _ensure_import_path()
    from backend.skills.persistence import JsonlSkillSpecStore

    store = JsonlSkillSpecStore(store_path)
    imported: list[str] = []
    for candidate in load_jsonl(source):
        patched = patch_candidate(candidate)
        spec = spec_from_dict(patched)
        if not spec.name:
            continue
        store.delete(spec.name)
        store.save(spec)
        imported.append(spec.name)
    return {"store_path": str(store_path.resolve()), "source": str(source.resolve()), "imported": imported, "count": len(imported)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(SOURCE_CANDIDATES))
    parser.add_argument("--store", default=str(ROOT / "state" / "skills.jsonl"))
    args = parser.parse_args()
    result = import_candidates(Path(args.source), Path(args.store))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
