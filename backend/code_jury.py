from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.prompts.review import CODE_JURY_PROMPT
from backend.state_store import resolve_state_path

CODE_JURY_SCHEMA_VERSION = "spiritkin.code_jury.v1"
DEFAULT_CODE_JURY_AUDIT_LOG = "state/evolution/code_jury.jsonl"
CODE_REVIEW_CRITERIA = ("architecture", "maintainability", "performance", "security", "testability")
UI_REVIEW_CRITERIA = ("usability", "visual_hierarchy", "accessibility", "consistency", "discoverability")
VALID_REVIEW_TYPES = {"code", "ui", "pr"}
HIGH_SEVERITIES = {"critical", "high"}


@dataclass(frozen=True)
class CodeReviewPackage:
    package_id: str
    review_type: str
    requirement: str
    candidate_diff: str = ""
    patch_format: str = "unified_diff"
    files_changed: tuple[str, ...] = ()
    before_context: str = ""
    after_context: str = ""
    build_results: tuple[dict[str, Any], ...] = ()
    unit_test_results: tuple[dict[str, Any], ...] = ()
    static_analysis_results: tuple[dict[str, Any], ...] = ()
    sandbox_logs: tuple[dict[str, Any], ...] = ()
    screenshots: tuple[dict[str, Any], ...] = ()
    pr_metadata: dict[str, Any] = field(default_factory=dict)
    capability_ids: tuple[str, ...] = ()
    risk_level: str = "medium"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CODE_JURY_SCHEMA_VERSION,
            "package_id": self.package_id,
            "review_type": self.review_type,
            "requirement": self.requirement,
            "candidate_diff": self.candidate_diff,
            "patch_format": self.patch_format,
            "files_changed": list(self.files_changed),
            "before_context": self.before_context,
            "after_context": self.after_context,
            "build_results": [dict(item) for item in self.build_results],
            "unit_test_results": [dict(item) for item in self.unit_test_results],
            "static_analysis_results": [dict(item) for item in self.static_analysis_results],
            "sandbox_logs": [dict(item) for item in self.sandbox_logs],
            "screenshots": [dict(item) for item in self.screenshots],
            "pr_metadata": dict(self.pr_metadata),
            "capability_ids": list(self.capability_ids),
            "risk_level": self.risk_level,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "evidence_summary": {
                "file_count": len(self.files_changed),
                "has_diff": bool(self.candidate_diff.strip()),
                "build_result_count": len(self.build_results),
                "unit_test_result_count": len(self.unit_test_results),
                "static_analysis_result_count": len(self.static_analysis_results),
                "sandbox_log_count": len(self.sandbox_logs),
                "screenshot_count": len(self.screenshots),
            },
        }


@dataclass(frozen=True)
class JuryFinding:
    severity: str
    category: str
    title: str
    detail: str
    file_path: str = ""
    line: int = 0
    evidence: str = ""
    suggested_fix: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "file_path": self.file_path,
            "line": self.line,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
        }


@dataclass(frozen=True)
class ModelJuryReview:
    reviewer_id: str
    provider: str
    model: str
    ok: bool
    status: str
    raw_text: str = ""
    structured: bool = False
    decision: str = "needs_human_review"
    scores: dict[str, int] = field(default_factory=dict)
    findings: tuple[JuryFinding, ...] = ()
    suggestions: tuple[str, ...] = ()
    confidence: float = 0.0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    parsed_payload: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "status": self.status,
            "raw_text": self.raw_text,
            "structured": self.structured,
            "decision": self.decision,
            "scores": dict(self.scores),
            "findings": [finding.snapshot() for finding in self.findings],
            "suggestions": list(self.suggestions),
            "confidence": self.confidence,
            "error": self.error,
            "created_at": self.created_at,
            "parsed_payload": dict(self.parsed_payload),
        }


@dataclass(frozen=True)
class JuryReport:
    report_id: str
    package: CodeReviewPackage
    decision: str
    overall_score: int
    criteria_scores: dict[str, int]
    model_reviews: tuple[ModelJuryReview, ...]
    findings: tuple[JuryFinding, ...]
    suggestions: tuple[str, ...]
    pass_threshold: int = 80
    min_structured_reviews: int = 1
    human_final_required: bool = True
    created_at: float = field(default_factory=time.time)
    promotion_gate: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CODE_JURY_SCHEMA_VERSION,
            "report_id": self.report_id,
            "package": self.package.snapshot(),
            "decision": self.decision,
            "overall_score": self.overall_score,
            "criteria_scores": dict(self.criteria_scores),
            "model_reviews": [review.snapshot() for review in self.model_reviews],
            "findings": [finding.snapshot() for finding in self.findings],
            "suggestions": list(self.suggestions),
            "pass_threshold": self.pass_threshold,
            "min_structured_reviews": self.min_structured_reviews,
            "human_final_required": self.human_final_required,
            "created_at": self.created_at,
            "promotion_gate": dict(self.promotion_gate),
            "summary": {
                "structured_review_count": sum(1 for review in self.model_reviews if review.structured and review.ok),
                "review_count": len(self.model_reviews),
                "finding_count": len(self.findings),
                "high_finding_count": sum(1 for finding in self.findings if finding.severity in HIGH_SEVERITIES),
            },
        }


@dataclass(frozen=True)
class PatchSynthesisPlan:
    plan_id: str
    package_id: str
    report_id: str
    status: str
    patch_scope: tuple[str, ...] = ()
    instructions: tuple[dict[str, Any], ...] = ()
    follow_up_tests: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    reviewer_constraints: tuple[str, ...] = (
        "Reviewer model output is advisory evidence only.",
        "Only the Patch Synthesizer may propose the controlled follow-up patch.",
        "No patch is applied automatically from this plan.",
    )
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": CODE_JURY_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "package_id": self.package_id,
            "report_id": self.report_id,
            "status": self.status,
            "patch_scope": list(self.patch_scope),
            "instructions": [dict(item) for item in self.instructions],
            "follow_up_tests": list(self.follow_up_tests),
            "blocked_reasons": list(self.blocked_reasons),
            "reviewer_constraints": list(self.reviewer_constraints),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class JuryGateDecision:
    allowed: bool
    gate_id: str
    reason: str
    required: bool = True
    report_id: str = ""
    subject: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "gate_id": self.gate_id,
            "reason": self.reason,
            "required": self.required,
            "report_id": self.report_id,
            "subject": self.subject,
            "evidence": dict(self.evidence),
        }


def resolve_code_jury_audit_path(path: str | os.PathLike[str] | None = None) -> Path:
    return resolve_state_path("SPIRITKIN_CODE_JURY_AUDIT_LOG", DEFAULT_CODE_JURY_AUDIT_LOG, path)


def build_code_review_package(payload: dict[str, Any]) -> CodeReviewPackage:
    review_type = _review_type(str(payload.get("review_type") or payload.get("type") or "code"))
    files = _string_tuple(payload.get("files_changed") or payload.get("files") or payload.get("paths") or ())
    package_id = str(payload.get("package_id") or "").strip()
    if not package_id:
        identity = json.dumps(
            {
                "review_type": review_type,
                "requirement": str(payload.get("requirement") or payload.get("task") or ""),
                "candidate_diff": str(payload.get("candidate_diff") or payload.get("diff") or ""),
                "files": files,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        package_id = _stable_id("jury_pkg", identity, str(time.time()))
    return CodeReviewPackage(
        package_id=package_id,
        review_type=review_type,
        requirement=str(payload.get("requirement") or payload.get("task") or payload.get("problem") or "").strip(),
        candidate_diff=str(payload.get("candidate_diff") or payload.get("diff") or payload.get("patch") or ""),
        patch_format=str(payload.get("patch_format") or "unified_diff"),
        files_changed=files,
        before_context=str(payload.get("before_context") or ""),
        after_context=str(payload.get("after_context") or ""),
        build_results=_dict_tuple(payload.get("build_results") or payload.get("build") or ()),
        unit_test_results=_dict_tuple(payload.get("unit_test_results") or payload.get("tests") or ()),
        static_analysis_results=_dict_tuple(payload.get("static_analysis_results") or payload.get("static_analysis") or ()),
        sandbox_logs=_dict_tuple(payload.get("sandbox_logs") or payload.get("logs") or ()),
        screenshots=_dict_tuple(payload.get("screenshots") or ()),
        pr_metadata=dict(payload.get("pr_metadata") or payload.get("pull_request") or {}),
        capability_ids=_string_tuple(payload.get("capability_ids") or ()),
        risk_level=_risk(str(payload.get("risk_level") or "medium")),
        created_at=float(payload.get("created_at") or time.time()),
        metadata=dict(payload.get("metadata") or {}),
    )


def build_jury_prompt(package: CodeReviewPackage) -> str:
    criteria = UI_REVIEW_CRITERIA if package.review_type == "ui" else CODE_REVIEW_CRITERIA
    return CODE_JURY_PROMPT.substitute(
        criteria=", ".join(criteria),
        review_type=package.review_type,
        requirement=package.requirement,
        files_changed=json.dumps(list(package.files_changed), ensure_ascii=False),
        candidate_diff=package.candidate_diff[:12000],
        build_results=json.dumps(list(package.build_results), ensure_ascii=False),
        unit_static_results=json.dumps(
            {"unit": list(package.unit_test_results), "static": list(package.static_analysis_results)},
            ensure_ascii=False,
        ),
        screenshots=json.dumps(list(package.screenshots), ensure_ascii=False),
    ).strip()


def parse_model_jury_review(raw_review: Any, *, index: int = 1, review_type: str = "code") -> ModelJuryReview:
    data = _review_dict(raw_review)
    raw_text = str(data.get("response_text") or data.get("raw_text") or data.get("text") or "")
    provider = str(data.get("provider") or "")
    model = str(data.get("model") or data.get("model_id") or "")
    reviewer_id = str(data.get("reviewer_id") or data.get("model_id") or model or provider or f"reviewer_{index}").strip()
    ok = bool(data.get("ok", True))
    status = str(data.get("status") or ("ok" if ok else "request_failed"))
    error = str(data.get("error") or "")
    if not ok:
        return ModelJuryReview(
            reviewer_id=reviewer_id,
            provider=provider,
            model=model,
            ok=False,
            status=status or "request_failed",
            raw_text=raw_text,
            error=error,
        )
    parsed = _extract_json_object(raw_text)
    if not isinstance(parsed, dict):
        return ModelJuryReview(
            reviewer_id=reviewer_id,
            provider=provider,
            model=model,
            ok=True,
            status="unstructured_evidence",
            raw_text=raw_text,
            structured=False,
            decision="needs_human_review",
            error="Reviewer response did not contain a structured JuryReport JSON object.",
        )
    criteria = UI_REVIEW_CRITERIA if _review_type(review_type) == "ui" else CODE_REVIEW_CRITERIA
    scores = _scores_from_payload(parsed, criteria)
    findings = tuple(_finding_from_dict(item) for item in _list_dicts(parsed.get("findings") or parsed.get("issues") or ()))
    suggestions = _suggestions_from_payload(parsed)
    decision = _decision(str(parsed.get("decision") or parsed.get("vote") or ""), scores=scores, findings=findings)
    structured = bool(scores or findings or decision in {"approved", "changes_requested", "blocked"})
    return ModelJuryReview(
        reviewer_id=reviewer_id,
        provider=provider,
        model=model,
        ok=True,
        status="structured" if structured else "unstructured_evidence",
        raw_text=raw_text,
        structured=structured,
        decision=decision,
        scores=scores,
        findings=findings,
        suggestions=suggestions,
        confidence=_float_0_1(parsed.get("confidence")),
        parsed_payload=parsed,
    )


def build_jury_report(
    package: CodeReviewPackage,
    reviews: list[Any] | tuple[Any, ...],
    *,
    pass_threshold: int = 80,
    min_structured_reviews: int = 1,
    human_final_required: bool = True,
) -> JuryReport:
    parsed_reviews = tuple(
        parse_model_jury_review(raw_review, index=index, review_type=package.review_type)
        for index, raw_review in enumerate(reviews, start=1)
    )
    structured_reviews = [review for review in parsed_reviews if review.ok and review.structured]
    criteria = UI_REVIEW_CRITERIA if package.review_type == "ui" else CODE_REVIEW_CRITERIA
    criteria_scores = _aggregate_scores(structured_reviews, criteria)
    all_findings: list[JuryFinding] = []
    suggestions: list[str] = []
    for review in structured_reviews:
        all_findings.extend(review.findings)
        suggestions.extend(review.suggestions)
    overall_score = round(sum(criteria_scores.values()) / max(1, len(criteria_scores))) if criteria_scores else 0
    threshold = max(0, min(100, int(pass_threshold)))
    min_reviews = max(1, int(min_structured_reviews or 1))
    if len(structured_reviews) < min_reviews:
        decision = "insufficient_evidence"
        reason = "not enough structured reviewer reports"
    elif any(finding.severity == "critical" for finding in all_findings):
        decision = "blocked"
        reason = "critical findings require repair before promotion"
    elif any(review.decision == "blocked" for review in structured_reviews):
        decision = "blocked"
        reason = "at least one structured reviewer blocked the package"
    elif any(finding.severity == "high" for finding in all_findings):
        decision = "changes_requested"
        reason = "high-severity findings require a controlled follow-up patch"
    elif any(review.decision == "changes_requested" for review in structured_reviews):
        decision = "changes_requested"
        reason = "at least one structured reviewer requested changes"
    elif overall_score < threshold:
        decision = "changes_requested"
        reason = f"overall score {overall_score} is below threshold {threshold}"
    else:
        decision = "approved"
        reason = "structured reviewer reports passed threshold"
    promotion_gate = {
        "operation": f"{package.review_type}_jury.promote",
        "eligible": decision == "approved",
        "allowed": decision == "approved" and not human_final_required,
        "requires_human_final": human_final_required,
        "auto_apply_allowed": False,
        "reason": reason,
        "review_gate_payload": {
            "core_review_required": True,
            "core_review_approved": False,
            "subject": package.package_id,
        },
    }
    report_id = _stable_id("jury_report", package.package_id, decision, json.dumps(criteria_scores, sort_keys=True), len(parsed_reviews))
    return JuryReport(
        report_id=report_id,
        package=package,
        decision=decision,
        overall_score=overall_score,
        criteria_scores=criteria_scores,
        model_reviews=parsed_reviews,
        findings=tuple(all_findings),
        suggestions=tuple(_dedupe_text(suggestions)),
        pass_threshold=threshold,
        min_structured_reviews=min_reviews,
        human_final_required=human_final_required,
        promotion_gate=promotion_gate,
    )


def synthesize_patch_plan(report: JuryReport) -> PatchSynthesisPlan:
    package = report.package
    findings = list(report.findings)
    if report.decision == "insufficient_evidence":
        status = "blocked"
        blocked = ("structured JuryReport evidence is required before patch synthesis",)
        instructions: tuple[dict[str, Any], ...] = ()
    elif report.decision == "approved" and not findings:
        status = "no_changes"
        blocked = ()
        instructions = ()
    elif findings:
        status = "proposal_ready"
        blocked = ()
        instructions = tuple(
            {
                "severity": finding.severity,
                "category": finding.category,
                "title": finding.title,
                "detail": finding.detail,
                "file_path": finding.file_path,
                "line": finding.line,
                "suggested_fix": finding.suggested_fix,
            }
            for finding in sorted(findings, key=lambda item: _severity_order(item.severity))
        )
    else:
        status = "blocked"
        blocked = ("no actionable findings were provided",)
        instructions = ()
    files_from_findings = [finding.file_path for finding in findings if finding.file_path]
    patch_scope = tuple(_dedupe_text([*files_from_findings, *package.files_changed]))
    follow_up_tests = tuple(_dedupe_text(_tests_from_package(package)))
    plan_id = _stable_id("patch_plan", report.report_id, status, json.dumps(list(patch_scope), ensure_ascii=False))
    return PatchSynthesisPlan(
        plan_id=plan_id,
        package_id=package.package_id,
        report_id=report.report_id,
        status=status,
        patch_scope=patch_scope,
        instructions=instructions,
        follow_up_tests=follow_up_tests,
        blocked_reasons=blocked,
        metadata={
            "jury_decision": report.decision,
            "overall_score": report.overall_score,
            "controlled_patch_only": True,
            "candidate_diff_retained": bool(package.candidate_diff.strip()),
        },
    )


def run_code_jury(payload: dict[str, Any]) -> dict[str, Any]:
    package_payload = payload.get("package") if isinstance(payload.get("package"), dict) else payload
    package = build_code_review_package(package_payload)
    subject = str(payload.get("subject") or payload.get("skill_name") or package.metadata.get("skill_name") or package.package_id).strip()
    reviews = _payload_reviews(payload)
    requested_review = None
    if not reviews and bool(payload.get("use_review_committee") or payload.get("request_review")):
        requested_review = _request_committee_review(package, payload)
        reviews = [review for review in requested_review.get("reviews") or [] if isinstance(review, dict)]
    report = build_jury_report(
        package,
        reviews,
        pass_threshold=int(payload.get("pass_threshold") or 80),
        min_structured_reviews=int(payload.get("min_structured_reviews") or 1),
        human_final_required=bool(payload.get("human_final_required", True)),
    )
    patch_plan = synthesize_patch_plan(report)
    audit_event = _append_code_jury_audit(
        {
            "action": "jury_review",
            "actor": str(payload.get("actor") or payload.get("reviewer") or "desktop"),
            "package_id": package.package_id,
            "report_id": report.report_id,
            "plan_id": patch_plan.plan_id,
            "subject": subject,
            "review_type": package.review_type,
            "decision": report.decision,
            "overall_score": report.overall_score,
            "structured_review_count": report.snapshot()["summary"]["structured_review_count"],
            "promotion_gate": report.promotion_gate,
            "patch_plan": patch_plan.snapshot(),
            "committee_review": requested_review or {},
        }
    )
    return {
        "ok": report.decision not in {"blocked"},
        "schema_version": CODE_JURY_SCHEMA_VERSION,
        "package": package.snapshot(),
        "jury_report": report.snapshot(),
        "patch_synthesis": patch_plan.snapshot(),
        "audit_event": audit_event,
        "code_jury": build_code_jury_snapshot(),
    }


def evaluate_jury_gate(
    payload: dict[str, Any],
    operation: str,
    *,
    subject: str = "",
    default_required: bool = False,
    default_review_type: str = "code",
) -> JuryGateDecision:
    gate = payload.get("jury_gate") if isinstance(payload.get("jury_gate"), dict) else {}
    required = _payload_bool(payload.get("jury_required"), _payload_bool(gate.get("required"), default_required))
    normalized_subject = str(subject or gate.get("subject") or payload.get("subject") or "").strip()
    min_score = _score_0_100(payload.get("jury_min_score", gate.get("min_score", 80)))
    min_structured_reviews = max(1, int(payload.get("jury_min_structured_reviews") or gate.get("min_structured_reviews") or 1))
    review_type = _review_type(str(payload.get("jury_review_type") or gate.get("review_type") or default_review_type))

    if not required:
        return JuryGateDecision(
            True,
            operation,
            "jury review not required",
            required=False,
            subject=normalized_subject,
            evidence={"required": False},
        )

    report = _report_summary_from_payload(payload, gate, subject=normalized_subject)
    if not report:
        return JuryGateDecision(
            False,
            operation,
            "passing JuryReport required",
            subject=normalized_subject,
            evidence={
                "required": True,
                "missing": "jury_report or jury_report_id",
                "min_score": min_score,
                "min_structured_reviews": min_structured_reviews,
            },
        )

    report_id = str(report.get("report_id") or "")
    decision = str(report.get("decision") or "").strip().lower()
    overall_score = int(report.get("overall_score") or 0)
    structured_count = int(report.get("structured_review_count") or 0)
    promotion_eligible = bool(report.get("promotion_eligible", decision == "approved"))
    report_review_type = _review_type(str(report.get("review_type") or review_type))
    report_subject = str(report.get("subject") or "").strip()
    package_id = str(report.get("package_id") or "").strip()
    subject_match = not normalized_subject or normalized_subject in {report_subject, package_id, report_id}
    type_match = report_review_type == review_type or review_type == "code" and report_review_type == "pr"

    if decision != "approved":
        reason = f"JuryReport decision is {decision or 'missing'}, not approved"
    elif not promotion_eligible:
        reason = "JuryReport promotion gate is not eligible"
    elif overall_score < min_score:
        reason = f"JuryReport score {overall_score} is below {min_score}"
    elif structured_count < min_structured_reviews:
        reason = f"JuryReport has {structured_count} structured reviews, requires {min_structured_reviews}"
    elif not subject_match:
        reason = "JuryReport subject does not match promotion subject"
    elif not type_match:
        reason = "JuryReport review type does not match required gate type"
    else:
        return JuryGateDecision(
            True,
            operation,
            "JuryReport approved",
            report_id=report_id,
            subject=normalized_subject,
            evidence={
                **report,
                "required": True,
                "min_score": min_score,
                "min_structured_reviews": min_structured_reviews,
                "subject_match": subject_match,
                "type_match": type_match,
            },
        )

    return JuryGateDecision(
        False,
        operation,
        reason,
        report_id=report_id,
        subject=normalized_subject,
        evidence={
            **report,
            "required": True,
            "min_score": min_score,
            "min_structured_reviews": min_structured_reviews,
            "subject_match": subject_match,
            "type_match": type_match,
        },
    )


def list_code_jury_audit_events(*, limit: int = 80, path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    target = resolve_code_jury_audit_path(path)
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events[-max(1, int(limit or 80)):]


def build_code_jury_snapshot(*, limit: int = 20) -> dict[str, Any]:
    events = list_code_jury_audit_events(limit=limit)
    decisions: dict[str, int] = {}
    review_types: dict[str, int] = {}
    for event in events:
        decision = str(event.get("decision") or "recorded")
        decisions[decision] = decisions.get(decision, 0) + 1
        review_type = str(event.get("review_type") or "code")
        review_types[review_type] = review_types.get(review_type, 0) + 1
    return {
        "schema_version": CODE_JURY_SCHEMA_VERSION,
        "generated_at": time.time(),
        "audit_log": str(resolve_code_jury_audit_path()),
        "criteria": {
            "code": list(CODE_REVIEW_CRITERIA),
            "ui": list(UI_REVIEW_CRITERIA),
        },
        "capabilities": {
            "code_review_package": True,
            "ui_review_package": True,
            "structured_jury_report": True,
            "unstructured_text_is_evidence_only": True,
            "patch_synthesizer": True,
            "auto_apply_reviewer_patch": False,
            "promotion_gate_linked": True,
        },
        "summary": {
            "audit_count": len(events),
            "decision_counts": decisions,
            "review_type_counts": review_types,
            "latest_decision": str(events[-1].get("decision") or "") if events else "",
        },
        "recent": events[-limit:],
    }


def handle_code_jury_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "snapshot").strip().lower()
    if action in {"snapshot", "refresh"}:
        return {"ok": True, "code_jury": build_code_jury_snapshot()}
    if action == "build_package":
        package_payload = payload.get("package") if isinstance(payload.get("package"), dict) else payload
        package = build_code_review_package(package_payload)
        return {"ok": True, "package": package.snapshot(), "review_prompt": build_jury_prompt(package), "code_jury": build_code_jury_snapshot()}
    if action == "review_prompt":
        package_payload = payload.get("package") if isinstance(payload.get("package"), dict) else payload
        package = build_code_review_package(package_payload)
        return {"ok": True, "prompt": build_jury_prompt(package), "package": package.snapshot()}
    if action in {"review", "run_jury", "evaluate"}:
        return run_code_jury(payload)
    if action == "parse_review":
        review = parse_model_jury_review(payload.get("review") if "review" in payload else payload, review_type=str(payload.get("review_type") or "code"))
        return {"ok": review.structured, "model_review": review.snapshot(), "code_jury": build_code_jury_snapshot()}
    raise ValueError(f"unsupported code jury action: {action}")


def _request_committee_review(package: CodeReviewPackage, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.app.learning_workflow import request_multi_model_review

    review = request_multi_model_review(
        build_jury_prompt(package),
        skill_name=f"{package.review_type}_jury",
        context=json.dumps(package.snapshot(), ensure_ascii=False),
        model_ids=[str(item) for item in payload.get("model_ids") or []] if isinstance(payload.get("model_ids"), list) else None,
    )
    return review.snapshot()


def _report_summary_from_payload(payload: dict[str, Any], gate: dict[str, Any], *, subject: str) -> dict[str, Any]:
    report_payload = payload.get("jury_report") if isinstance(payload.get("jury_report"), dict) else gate.get("jury_report")
    if isinstance(report_payload, dict):
        return _report_summary_from_snapshot(report_payload, subject=subject)
    report_id = str(payload.get("jury_report_id") or payload.get("code_jury_report_id") or gate.get("report_id") or gate.get("jury_report_id") or "").strip()
    if not report_id:
        return {}
    for event in reversed(list_code_jury_audit_events(limit=500)):
        if report_id not in {str(event.get("report_id") or ""), str(event.get("event_id") or "")}:
            continue
        return _report_summary_from_audit_event(event, subject=subject)
    return {"report_id": report_id, "decision": "missing", "subject": subject}


def _report_summary_from_snapshot(report: dict[str, Any], *, subject: str) -> dict[str, Any]:
    package = report.get("package") if isinstance(report.get("package"), dict) else {}
    promotion_gate = report.get("promotion_gate") if isinstance(report.get("promotion_gate"), dict) else {}
    review_gate_payload = promotion_gate.get("review_gate_payload") if isinstance(promotion_gate.get("review_gate_payload"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    package_metadata = package.get("metadata") if isinstance(package.get("metadata"), dict) else {}
    return {
        "report_id": str(report.get("report_id") or ""),
        "package_id": str(package.get("package_id") or ""),
        "subject": str(package_metadata.get("skill_name") or report.get("subject") or review_gate_payload.get("subject") or package.get("package_id") or subject),
        "decision": str(report.get("decision") or ""),
        "overall_score": int(report.get("overall_score") or 0),
        "structured_review_count": int(summary.get("structured_review_count") or 0),
        "promotion_eligible": bool(promotion_gate.get("eligible", str(report.get("decision") or "") == "approved")),
        "review_type": str(package.get("review_type") or "code"),
        "source": "jury_report_payload",
    }


def _report_summary_from_audit_event(event: dict[str, Any], *, subject: str) -> dict[str, Any]:
    promotion_gate = event.get("promotion_gate") if isinstance(event.get("promotion_gate"), dict) else {}
    return {
        "report_id": str(event.get("report_id") or ""),
        "package_id": str(event.get("package_id") or ""),
        "subject": str(event.get("subject") or event.get("package_id") or subject),
        "decision": str(event.get("decision") or ""),
        "overall_score": int(event.get("overall_score") or 0),
        "structured_review_count": int(event.get("structured_review_count") or 0),
        "promotion_eligible": bool(promotion_gate.get("eligible", str(event.get("decision") or "") == "approved")),
        "review_type": str(event.get("review_type") or "code"),
        "source": "code_jury_audit",
        "event_id": str(event.get("event_id") or ""),
    }


def _append_code_jury_audit(event: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = resolve_code_jury_audit_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": CODE_JURY_SCHEMA_VERSION,
        "event_id": event.get("event_id") or _stable_id("jury_event", time.time(), json.dumps(event, sort_keys=True, default=str)),
        "at": time.time(),
        **event,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return payload


def _payload_reviews(payload: dict[str, Any]) -> list[Any]:
    for key in ("model_reviews", "reviews", "jury_reviews"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    review = payload.get("review")
    if review is not None:
        return [review]
    return []


def _payload_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


def _review_dict(raw_review: Any) -> dict[str, Any]:
    if isinstance(raw_review, dict):
        return dict(raw_review)
    if hasattr(raw_review, "snapshot"):
        snapshot = raw_review.snapshot()
        return dict(snapshot) if isinstance(snapshot, dict) else {"response_text": str(snapshot)}
    return {"response_text": str(raw_review or "")}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    decoder = json.JSONDecoder()
    candidates = [raw]
    if "```" in raw:
        parts = raw.split("```")
        candidates.extend(part[4:].strip() if part.lstrip().startswith("json") else part.strip() for part in parts)
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "{[":
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"findings": parsed}
    return None


def _scores_from_payload(payload: dict[str, Any], criteria: tuple[str, ...]) -> dict[str, int]:
    raw_scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    raw_criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
    scores: dict[str, int] = {}
    for criterion in criteria:
        value = raw_scores.get(criterion, raw_criteria.get(criterion))
        if isinstance(value, dict):
            value = value.get("score")
        if value is None:
            continue
        scores[criterion] = _score_0_100(value)
    return scores


def _aggregate_scores(reviews: list[ModelJuryReview], criteria: tuple[str, ...]) -> dict[str, int]:
    aggregate: dict[str, int] = {}
    for criterion in criteria:
        values = [review.scores[criterion] for review in reviews if criterion in review.scores]
        if values:
            aggregate[criterion] = round(sum(values) / len(values))
    return aggregate


def _finding_from_dict(data: dict[str, Any]) -> JuryFinding:
    severity = _severity(str(data.get("severity") or data.get("risk") or "medium"))
    return JuryFinding(
        severity=severity,
        category=str(data.get("category") or data.get("criterion") or "general").strip() or "general",
        title=str(data.get("title") or data.get("summary") or severity).strip()[:160],
        detail=str(data.get("detail") or data.get("description") or data.get("message") or "").strip(),
        file_path=str(data.get("file_path") or data.get("file") or data.get("path") or "").strip(),
        line=max(0, int(data.get("line") or data.get("line_number") or 0)),
        evidence=str(data.get("evidence") or data.get("quote") or "").strip(),
        suggested_fix=str(data.get("suggested_fix") or data.get("fix") or data.get("suggestion") or "").strip(),
    )


def _suggestions_from_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("suggestions") or payload.get("recommendations") or ()
    values: list[str] = []
    if isinstance(raw, str):
        values.append(raw)
    elif isinstance(raw, list):
        values.extend(str(item) for item in raw if str(item).strip())
    for item in _list_dicts(payload.get("findings") or payload.get("issues") or ()):
        fix = str(item.get("suggested_fix") or item.get("fix") or "").strip()
        if fix:
            values.append(fix)
    return tuple(_dedupe_text(values))


def _decision(value: str, *, scores: dict[str, int], findings: tuple[JuryFinding, ...]) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"approved", "approve", "pass", "passed", "ok"}:
        return "approved"
    if normalized in {"blocked", "block", "fail", "failed"}:
        return "blocked"
    if normalized in {"changes_requested", "request_changes", "needs_changes", "reject", "rejected"}:
        return "changes_requested"
    if any(finding.severity in HIGH_SEVERITIES for finding in findings):
        return "blocked" if any(finding.severity == "critical" for finding in findings) else "changes_requested"
    if scores and min(scores.values()) >= 80:
        return "approved"
    if scores:
        return "changes_requested"
    return "needs_human_review"


def _tests_from_package(package: CodeReviewPackage) -> list[str]:
    tests: list[str] = []
    for item in [*package.unit_test_results, *package.build_results, *package.static_analysis_results]:
        command = str(item.get("command") or item.get("cmd") or "").strip()
        if command:
            tests.append(command)
    if not tests and package.review_type in {"code", "pr"}:
        tests.append("run the focused unit/static checks listed in the review package")
    if package.review_type == "ui":
        tests.append("capture before/after screenshots and verify accessibility/visual hierarchy")
    return tests


def _dict_tuple(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, dict):
        return (dict(value),)
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, dict))


def _list_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.replace("\n", ",").split(",") if part.strip())
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _dedupe_text(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _risk(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"low", "medium", "high", "critical"} else "medium"


def _review_type(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in VALID_REVIEW_TYPES else "code"


def _severity(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in {"low", "medium", "high", "critical"} else "medium"


def _severity_order(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(value, 9)


def _score_0_100(value: Any) -> int:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    if 0 <= score <= 1:
        score *= 100
    elif 0 <= score <= 5:
        score *= 20
    return int(max(0, min(100, round(score))))


def _float_0_1(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "\n".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
