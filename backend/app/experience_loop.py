from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.runtime import InteractionInput, SpiritKinRuntime


@dataclass(frozen=True)
class ExperienceCheck:
    stage: str
    name: str
    ok: bool
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperienceReport:
    checks: list[ExperienceCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, stage: str, name: str, ok: bool, detail: str, payload: dict[str, Any] | None = None) -> None:
        self.checks.append(ExperienceCheck(stage, name, bool(ok), detail, dict(payload or {})))

    def by_stage(self) -> dict[str, list[ExperienceCheck]]:
        grouped: dict[str, list[ExperienceCheck]] = {}
        for check in self.checks:
            grouped.setdefault(check.stage, []).append(check)
        return grouped

    def to_markdown(self) -> str:
        lines = ["# SpiritKin 体验闭环验证报告", ""]
        lines.append(f"总体结果：{'PASS' if self.passed else 'FAIL'}")
        for stage, checks in self.by_stage().items():
            lines.extend(["", f"## {stage}"])
            for check in checks:
                status = "PASS" if check.ok else "FAIL"
                lines.append(f"- [{status}] {check.name}：{check.detail}")
        return "\n".join(lines)


def _event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("type", "")) for event in events]


def _find_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("type") == event_type), None)


def verify_feishu_experience_loop(
    runtime: SpiritKinRuntime,
    *,
    transcript: str = "给张三发飞书，说会议改到三点",
    visual_context: str = "桌面可见飞书窗口，用户希望发送一条工作通知",
    confirmation_text: str = "确认执行",
) -> ExperienceReport:
    """Verify the practical ear-eye-mouth-face-hand loop with Feishu dry-run."""
    report = ExperienceReport()
    normalized = transcript.strip()

    first_input = InteractionInput(text=normalized, channel="voice", visual_context=visual_context)
    input_payload = runtime.build_input_payload(first_input)
    report.add("耳朵/ASR", "语音文本进入统一入口", bool(normalized), f"transcript={normalized!r}")
    report.add(
        "眼睛/视觉上下文",
        "视觉上下文随输入进入 Runtime",
        input_payload.get("visual_context") == visual_context,
        f"visual_context={input_payload.get('visual_context')!r}",
    )

    try:
        confirmation = runtime.handle_input(first_input)
    except Exception as exc:
        report.add("脑/意图解析", "飞书意图解析不应退回异常", False, repr(exc))
        return report

    confirm_events = runtime.build_response_events(confirmation) if confirmation else []
    metadata = dict(getattr(confirmation, "metadata", {}) or {}) if confirmation else {}
    intent_ok = bool(
        confirmation
        and confirmation.requires_confirmation
        and metadata.get("pending_target") == "feishu"
        and metadata.get("pending_operation") == "send_message"
    )
    report.add(
        "脑/意图解析",
        "口语飞书请求识别为 feishu.send_message",
        intent_ok,
        f"pending={metadata.get('pending_target')}.{metadata.get('pending_operation')}",
    )

    confirm_event = _find_event(confirm_events, "assistant.confirmation_requested")
    report.add(
        "安全/确认门",
        "高风险发送动作要求二次确认",
        bool(confirm_event),
        f"events={_event_types(confirm_events)}",
    )

    confirm_payload = SpiritKinRuntime.build_output_payload(confirmation) if confirmation else {}
    report.add(
        "嘴/反馈话术",
        "确认阶段有可播报 spoken_text",
        bool(confirm_payload.get("spoken_text")),
        str(confirm_payload.get("spoken_text", "")),
    )
    avatar_event = _find_event(confirm_events, "avatar.state")
    avatar_payload = dict((avatar_event or {}).get("payload", {}) or {})
    report.add(
        "脸/Live2D",
        "确认阶段头像进入等待确认状态",
        avatar_payload.get("requires_confirmation") is True and avatar_payload.get("action") == "await_confirmation",
        f"emotion={avatar_payload.get('emotion')} action={avatar_payload.get('action')}",
    )

    try:
        execution = runtime.handle_input(InteractionInput(text=confirmation_text, channel="voice"))
    except Exception as exc:
        report.add("手脚/执行器", "确认后执行不应抛异常", False, repr(exc))
        return report

    execution_events = runtime.build_response_events(execution) if execution else []
    execution_metadata = dict(getattr(execution, "metadata", {}) or {}) if execution else {}
    execution_payload = dict(execution_metadata.get("execution", {}) or {})
    execution_data = dict(execution_payload.get("data", {}) or {})
    hand_ok = bool(
        execution
        and execution_metadata.get("response_kind") == "execution_result"
        and execution_payload.get("target") == "feishu"
        and execution_payload.get("operation") == "send_message"
        and execution_data.get("dry_run") is True
        and execution_data.get("recipient")
        and execution_data.get("text")
    )
    report.add(
        "手脚/执行器",
        "FeishuExecutor 完成 dry-run 发送",
        hand_ok,
        f"recipient={execution_data.get('recipient')} text={execution_data.get('text')!r} dry_run={execution_data.get('dry_run')}",
    )

    report.add(
        "事件/前端",
        "执行结果事件可驱动前端状态面板",
        bool(_find_event(execution_events, "assistant.execution_updated")),
        f"events={_event_types(execution_events)}",
    )
    execution_output = SpiritKinRuntime.build_output_payload(execution) if execution else {}
    report.add(
        "嘴/反馈话术",
        "执行阶段有可播报结果",
        bool(execution_output.get("spoken_text")),
        str(execution_output.get("spoken_text", "")),
    )
    done_avatar_event = _find_event(execution_events, "avatar.state")
    done_avatar_payload = dict((done_avatar_event or {}).get("payload", {}) or {})
    report.add(
        "脸/Live2D",
        "执行成功后头像进入任务完成反馈",
        done_avatar_payload.get("emotion") == "happy" and done_avatar_payload.get("action") == "execute_task",
        f"emotion={done_avatar_payload.get('emotion')} action={done_avatar_payload.get('action')}",
    )
    return report