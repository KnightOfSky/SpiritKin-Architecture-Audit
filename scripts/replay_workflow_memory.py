from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.settings import resolve_audit_log_path, resolve_workflow_memory_path
from backend.evaluation import (
    SkillVerificationPolicy,
    build_replay_report,
    build_replay_report_with_audit_correlation,
    verify_all_candidate_readiness,
)
from backend.memory import build_workflow_memory
from backend.security import build_audit_log
from backend.skills import SkillRegistry
from backend.tools import build_default_tool_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run replay report for SpiritKin Workflow Memory")
    parser.add_argument("--path", default="", help="Workflow memory path; defaults to runtime settings")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--require-known-tool", action="store_true")
    parser.add_argument("--correlate-audit", action="store_true", help="Correlate replay records with audit log")
    parser.add_argument("--verify-skills", action="store_true", help="Verify candidate skills against tool registry")
    parser.add_argument("--min-skill-replayable-rate", type=float, default=1.0, help="Minimum replayable rate required for candidate Skill readiness")
    parser.add_argument("--max-skill-failures", type=int, default=0, help="Maximum expected failures allowed for candidate Skill readiness")
    parser.add_argument("--require-skill-audit", action="store_true", help="Require at least one audit correlation per candidate Skill")
    parser.add_argument("--output-failures-to", default="", help="Write failure samples to JSONL path")
    parser.add_argument("--output-report-to", default="", help="Write replay/eval report JSON to this path")
    args = parser.parse_args()

    path = args.path or resolve_workflow_memory_path()
    memory = build_workflow_memory(path, limit=max(1, args.limit))
    records = memory.query(limit=args.limit, include_archived=args.include_archived) if hasattr(memory, "query") else memory.recent(args.limit)
    tool_registry = build_default_tool_registry()
    tools = tool_registry.list_specs()

    if args.correlate_audit:
        audit_log = build_audit_log(resolve_audit_log_path())
        report = build_replay_report_with_audit_correlation(records, tools=tools, audit_log=audit_log, require_known_tool=args.require_known_tool)
    else:
        report = build_replay_report(records, tools=tools, require_known_tool=args.require_known_tool)

    output = report.snapshot()

    if args.verify_skills:
        registry = SkillRegistry()
        from backend.skills.workflow import build_workflow_skill_specs

        candidates = memory.skill_candidates() if hasattr(memory, "skill_candidates") else []
        for skill in build_workflow_skill_specs(candidates, tools):
            if registry.get(skill.name) is None:
                registry.register(skill)
        policy = SkillVerificationPolicy(
            min_replayable_rate=max(0.0, min(1.0, args.min_skill_replayable_rate)),
            max_expected_failures=max(0, args.max_skill_failures),
            require_audit_correlation=bool(args.require_skill_audit),
        )
        verifications = verify_all_candidate_readiness(registry, tool_registry, replay_report=report, policy=policy)
        output["skill_verifications"] = [v.snapshot() for v in verifications]
        output["skill_verification_summary"] = {
            "total": len(verifications),
            "passed": sum(1 for v in verifications if v.passed),
            "failed": sum(1 for v in verifications if not v.passed),
        }

    if args.output_failures_to:
        from backend.evaluation.failure_db import build_failure_sample_db

        failure_db = build_failure_sample_db(args.output_failures_to)
        for record in report.records:
            if not record.expected_success:
                error_code = record.metadata.get("source_error_code", "") or "unknown"
                failure_db.record(
                    tool_name=record.tool_name,
                    target=getattr(record.request, "target", "") if record.request else "",
                    operation=getattr(record.request, "operation", "") if record.request else "",
                    error_code=error_code,
                    user_input_snippet=record.user_input[:200],
                )
        output["failure_samples_written"] = True

    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output_report_to:
        report_path = Path(args.output_report_to).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
        output["report_path"] = str(report_path)
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
    print(rendered)
    return 0 if report.replayable_count == report.total else 1


if __name__ == "__main__":
    raise SystemExit(main())