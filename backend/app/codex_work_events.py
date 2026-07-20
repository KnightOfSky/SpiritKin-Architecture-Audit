from __future__ import annotations

import re
import subprocess
import time
from typing import Any

TRACE_SCHEMA_VERSION = "spiritkin.assistant_work_trace.v1"



def _trace_fragment(value: object, fallback: str = "span") -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = text.strip("_")[:80]
    return text or fallback


def execution_command_preview(request: Any, execution: Any = None) -> str:
    """Return the executable value shown in the Codex-style command row."""

    def command_text(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            parts = [str(item) for item in value if str(item)]
            return subprocess.list2cmdline(parts) if parts else ""
        return str(value or "").strip()

    def preview_from_mapping(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        for key in ("command", "cmd", "app_name", "app", "path", "url"):
            candidate = command_text(value.get(key))
            if candidate:
                return candidate
        for key in ("arguments", "params", "data", "result"):
            nested = preview_from_mapping(value.get(key))
            if nested:
                return nested
        return ""

    for field in ("arguments", "params"):
        preview = preview_from_mapping(getattr(request, field, None))
        if preview:
            return preview
    preview = preview_from_mapping(execution)
    if preview:
        return preview
    target = str(getattr(request, "target", "") or "").strip()
    operation = str(getattr(request, "operation", "") or "").strip()
    return f"{target}.{operation}" if target and operation else operation or target


def execution_output_preview(execution: Any, fallback: str = "") -> str:
    """Return actual process output before falling back to the friendly reply."""

    def output_from_mapping(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        stdout = str(value.get("stdout") or "").strip()
        stderr = str(value.get("stderr") or "").strip()
        if stdout or stderr:
            return "\n".join(part for part in (stdout, stderr) if part)
        for key in ("data", "result", "execution", "metadata"):
            nested = output_from_mapping(value.get(key))
            if nested:
                return nested
        for key in ("output", "result_text"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
        return ""

    return output_from_mapping(execution) or str(fallback or "").strip()


def install() -> None:
    try:
        from backend.app.runtime import EVENT_SCHEMA_VERSION, InteractionInput, SpiritKinRuntime
    except Exception:
        return

    if getattr(SpiritKinRuntime, "_codex_work_events_installed", False):
        return
    SpiritKinRuntime._codex_work_events_installed = True

    original_handle_input = SpiritKinRuntime.handle_input
    original_process_user_input = SpiritKinRuntime._process_user_input

    try:
        from backend.orchestrator.agent_cluster import AgentCluster
    except Exception:
        AgentCluster = None  # type: ignore[assignment]

    def trace_state(runtime: SpiritKinRuntime, interaction: InteractionInput) -> dict[str, Any]:
        states = getattr(runtime, "_codex_work_trace_states", None)
        if not isinstance(states, dict):
            states = {}
            runtime._codex_work_trace_states = states
        key = id(interaction)
        state = states.get(key)
        if isinstance(state, dict):
            return state

        active_state = getattr(runtime, "_codex_active_work_trace_state", None)
        if isinstance(active_state, dict):
            states[key] = active_state
            return active_state

        metadata = dict(interaction.metadata or {})
        run_id = str(metadata.get("run_id") or metadata.get("request_id") or "").strip()
        if not run_id:
            run_id = f"run-{int(time.time() * 1000)}-{key:x}"
        state = {
            "run_id": run_id,
            "seq": 0,
            "root_span_id": f"{run_id}:root",
        }
        states[key] = state
        return state

    def root_span_id(runtime: SpiritKinRuntime, interaction: InteractionInput) -> str:
        return str(trace_state(runtime, interaction)["root_span_id"])

    def named_span_id(runtime: SpiritKinRuntime, interaction: InteractionInput, *parts: object) -> str:
        state = trace_state(runtime, interaction)
        suffix = ":".join(_trace_fragment(part) for part in parts if str(part or "").strip())
        return f"{state['run_id']}:{suffix or 'span'}"

    def finish_trace_state(runtime: SpiritKinRuntime, interaction: InteractionInput) -> None:
        states = getattr(runtime, "_codex_work_trace_states", None)
        if isinstance(states, dict):
            state = states.pop(id(interaction), None)
            if isinstance(state, dict):
                run_id = state.get("run_id")
                for key, value in list(states.items()):
                    if value is state or (run_id and isinstance(value, dict) and value.get("run_id") == run_id):
                        states.pop(key, None)
                if getattr(runtime, "_codex_active_work_trace_state", None) is state:
                    try:
                        delattr(runtime, "_codex_active_work_trace_state")
                    except Exception:
                        pass

    def next_trace_payload(
        runtime: SpiritKinRuntime,
        interaction: InteractionInput,
        *,
        phase: str,
        span_id: str = "",
        parent_id: str = "",
        agent_id: str = "runtime",
        status: str = "running",
        is_terminal: bool = False,
    ) -> dict[str, Any]:
        state = trace_state(runtime, interaction)
        state["seq"] = int(state.get("seq") or 0) + 1
        seq = int(state["seq"])
        resolved_span_id = span_id or f"{state['run_id']}:event:{seq}"
        return {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "event_id": f"{state['run_id']}:{seq:06d}",
            "run_id": state["run_id"],
            "seq": seq,
            "span_id": resolved_span_id,
            "parent_id": parent_id,
            "agent_id": agent_id or "runtime",
            "status": status or "running",
            "is_terminal": bool(is_terminal),
            "detail": {"phase": phase or "runtime"},
        }

    def emit_runtime_work(
        runtime: SpiritKinRuntime,
        interaction: InteractionInput,
        text: str,
        *,
        kind: str = "thought",
        detail: dict[str, Any] | None = None,
        phase: str = "runtime",
        span_id: str = "",
        parent_id: str = "",
        agent_id: str = "runtime",
        status: str = "running",
        is_terminal: bool = False,
    ) -> None:
        metadata = dict(interaction.metadata or {})
        state = trace_state(runtime, interaction)
        resolved_span_id = span_id or ""
        normalized_status = str(status or "running").strip().lower()
        active_span = state.get("active_span") if isinstance(state.get("active_span"), dict) else None
        if active_span and str(active_span.get("span_id") or "") != resolved_span_id:
            closing_trace = next_trace_payload(
                runtime,
                interaction,
                phase=str(active_span.get("phase") or "runtime"),
                span_id=str(active_span.get("span_id") or ""),
                parent_id=str(active_span.get("parent_id") or ""),
                agent_id=str(active_span.get("agent_id") or "runtime"),
                status="completed",
                is_terminal=False,
            )
            closing_detail = dict(closing_trace.pop("detail"))
            closing_detail.update(dict(active_span.get("detail") or {}))
            try:
                runtime._emit_runtime_event(
                    {
                        "type": "assistant.work_updated",
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "payload": {
                            **closing_trace,
                            "text": str(active_span.get("text") or ""),
                            "kind": str(active_span.get("kind") or "thought"),
                            "channel": interaction.channel,
                            "request_id": str(metadata.get("request_id") or ""),
                            "session_id": str(metadata.get("session_id") or ""),
                            "detail": closing_detail,
                        },
                    }
                )
            except Exception:
                pass
            state.pop("active_span", None)

        trace_payload = next_trace_payload(
            runtime,
            interaction,
            phase=phase,
            span_id=span_id,
            parent_id=parent_id,
            agent_id=agent_id,
            status=status,
            is_terminal=is_terminal,
        )
        trace_detail = dict(trace_payload.pop("detail"))
        trace_detail.update(dict(detail or {}))
        if normalized_status in {"started", "running", "in_progress", "processing", "stream"}:
            state["active_span"] = {
                "span_id": str(trace_payload.get("span_id") or resolved_span_id),
                "parent_id": parent_id,
                "agent_id": agent_id,
                "phase": phase,
                "text": text,
                "kind": kind,
                "detail": dict(detail or {}),
            }
        elif active_span and str(active_span.get("span_id") or "") == str(trace_payload.get("span_id") or resolved_span_id):
            state.pop("active_span", None)
        try:
            runtime._emit_runtime_event(
                {
                    "type": "assistant.work_updated",
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "payload": {
                        **trace_payload,
                        "text": text,
                        "kind": kind,
                        "channel": interaction.channel,
                        "request_id": str(metadata.get("request_id") or ""),
                        "session_id": str(metadata.get("session_id") or ""),
                        "detail": trace_detail,
                    },
                }
            )
        except Exception:
            return

    def trim(value: object, limit: int = 240) -> str:
        text = " ".join(str(value or "").replace("\r", "\n").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def visible_stream_text(value: object) -> str:
        """Remove model-control tags from the in-progress user-visible reply."""

        text = str(value or "")
        text = re.sub(r"<(?:emotion|action)\s*:[^>]*>", "", text, flags=re.IGNORECASE)
        lowered = text.lower()
        for marker in ("<emotion", "<action"):
            index = lowered.rfind(marker)
            if index >= 0 and ">" not in text[index:]:
                text = text[:index]
                lowered = text.lower()
        return text

    def current_interaction(runtime: SpiritKinRuntime) -> InteractionInput | None:
        return getattr(runtime, "_codex_current_interaction", None)

    def route_label(route: str) -> str:
        return {
            "general": "普通回答",
            "agent": "专业 agent",
            "executor": "桌面执行",
            "tool": "工具调用",
            "development_plan": "开发计划",
            "intent": "意图纠错",
            "builtin": "内置工具",
            "skill": "Skill 执行",
        }.get(route, route or "默认")

    def summarize_route(route: str, domain: str, resource: str, reason: str, agent_id: str = "") -> str:
        parts = [f"模型选择 {route_label(route)} 路由"]
        if domain:
            parts.append(f"领域 {domain}")
        if resource:
            parts.append(f"资源 {resource}")
        if agent_id:
            parts.append(f"agent {agent_id}")
        if reason:
            parts.append(f"原因：{trim(reason, 160)}")
        return "；".join(parts) + "。"

    def summarize_reply(runtime: SpiritKinRuntime, interaction: InteractionInput, reply: Any) -> None:
        root_span = root_span_id(runtime, interaction)
        metadata = getattr(reply, "metadata", {}) if reply is not None else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        scheduler = metadata.get("scheduler") if isinstance(metadata.get("scheduler"), dict) else None
        if scheduler:
            route_detail = summarize_route(
                str(scheduler.get("route") or ""),
                str(scheduler.get("domain") or ""),
                str(scheduler.get("resource_profile") or ""),
                str(scheduler.get("reason") or ""),
                str(scheduler.get("agent_id") or ""),
            )
            emit_runtime_work(
                runtime,
                interaction,
                route_detail,
                detail={"scheduler": scheduler},
                phase="scheduler",
                span_id=named_span_id(runtime, interaction, "scheduler"),
                parent_id=root_span,
                agent_id=str(scheduler.get("agent_id") or "scheduler"),
                status="completed",
            )

        execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else None
        if execution:
            target = str(execution.get("target") or "--")
            operation = str(execution.get("operation") or "--")
            error = str(execution.get("error") or "")
            success = execution.get("success") is not False
            command_preview = execution_command_preview(None, execution)
            subject = command_preview or f"{target}.{operation}"
            output_text = trim(execution_output_preview(execution, getattr(reply, "text", "") or getattr(reply, "spoken_text", "")), 2000)
            message = f"{subject} · {'完成' if success else '失败'}。"
            if error:
                message = f"{subject} · 执行失败：{error[:240]}"
            emit_runtime_work(
                runtime,
                interaction,
                message,
                kind="command",
                detail={
                    "execution": {**execution, "command": subject, "output": output_text},
                    "result": {"success": success},
                    **({"error": {"message": error[:240]}} if error else {}),
                },
                phase="execution",
                span_id=named_span_id(runtime, interaction, "execution", target, operation),
                parent_id=root_span,
                agent_id="executor",
                status="completed" if success else "failed",
            )

        development_plan = metadata.get("development_plan") if isinstance(metadata.get("development_plan"), dict) else None
        if development_plan:
            summary = str(development_plan.get("summary") or "")
            emit_runtime_work(
                runtime,
                interaction,
                f"模型生成开发计划：{trim(summary, 220)}" if summary else "模型生成开发计划，等待确认后进入实现。",
                detail={"development_plan": development_plan},
                phase="route",
                span_id=named_span_id(runtime, interaction, "route", "development_plan"),
                parent_id=root_span,
                agent_id="planner",
                status="completed",
            )
            for action in list(development_plan.get("suggested_actions") or [])[:3]:
                emit_runtime_work(
                    runtime,
                    interaction,
                    f"建议下一步：{trim(action, 220)}",
                    kind="command",
                    detail={"result": {"suggested_action": action}},
                    phase="agent",
                    span_id=named_span_id(runtime, interaction, "agent", "suggested_action"),
                    parent_id=root_span,
                    agent_id="planner",
                    status="running",
                )

        task = metadata.get("task") if isinstance(metadata.get("task"), dict) else None
        finalizer = task.get("finalizer") if isinstance(task, dict) and isinstance(task.get("finalizer"), dict) else None
        if finalizer:
            decision = str(finalizer.get("decision") or "")
            next_status = str(finalizer.get("next_status") or "")
            verified = bool(finalizer.get("verified"))
            score = finalizer.get("score")
            task_id = str(task.get("task_id") or "")
            reason_text = ", ".join(str(item) for item in list(finalizer.get("reasons") or [])[:3] if str(item))
            summary_parts = [f"任务收口：{decision or 'unknown'}"]
            if next_status:
                summary_parts.append(f"next={next_status}")
            summary_parts.append(f"verified={verified}")
            if score not in {"", None}:
                summary_parts.append(f"score={score}")
            if reason_text:
                summary_parts.append(f"reason={trim(reason_text, 120)}")
            emit_runtime_work(
                runtime,
                interaction,
                "；".join(summary_parts) + "。",
                kind="status",
                detail={"scheduler": {"task_id": task_id, "finalizer": finalizer}, "result": {"decision": decision, "next_status": next_status, "verified": verified, "score": score}},
                phase="scheduler",
                span_id=named_span_id(runtime, interaction, "scheduler", "finalizer", task_id or "task"),
                parent_id=root_span,
                agent_id=str(finalizer.get("source") or "scheduler_task_finalizer"),
                status="completed" if verified else "running",
            )

        response_kind = str(metadata.get("response_kind") or "")
        if response_kind in {"policy_denied", "task_failed", "scheduler_busy", "skill_assist_required", "skill_failed_with_assist"}:
            text = trim(getattr(reply, "text", "") or getattr(reply, "spoken_text", ""), 220)
            if text:
                failed = "failed" in response_kind or "denied" in response_kind
                emit_runtime_work(
                    runtime,
                    interaction,
                    f"运行状态：{text}",
                    kind="command" if failed else "thought",
                    detail={"reason": {"response_kind": response_kind}},
                    phase="agent",
                    span_id=named_span_id(runtime, interaction, "agent", response_kind),
                    parent_id=root_span,
                    agent_id=str(metadata.get("agent_id") or "agent"),
                    status="failed" if failed else "running",
                )

    def root_status_for_reply(reply: Any) -> str:
        metadata = getattr(reply, "metadata", {}) if reply is not None else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        response_kind = str(metadata.get("response_kind") or "")
        if response_kind in {"policy_denied", "task_failed"}:
            return "failed"
        if reply is None:
            return "skipped"
        return "completed"

    def emit_root_terminal(runtime: SpiritKinRuntime, interaction: InteractionInput, reply: Any) -> None:
        status = root_status_for_reply(reply)
        metadata = getattr(reply, "metadata", {}) if reply is not None else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        emit_runtime_work(
            runtime,
            interaction,
            "本轮请求处理完成。" if status == "completed" else "本轮请求已结束。",
            kind="status",
            detail={"result": {"response_kind": metadata.get("response_kind"), "requires_confirmation": bool(getattr(reply, "requires_confirmation", False))}},
            phase="scheduler",
            span_id=root_span_id(runtime, interaction),
            parent_id="",
            agent_id=str(getattr(reply, "agent_name", "") or metadata.get("agent_id") or "runtime"),
            status=status,
            is_terminal=True,
        )

    def emit_root_failure(runtime: SpiritKinRuntime, interaction: InteractionInput, exc: Exception) -> None:
        emit_runtime_work(
            runtime,
            interaction,
            f"本轮请求处理失败：{trim(exc, 220)}",
            kind="command",
            detail={"error": {"type": type(exc).__name__, "message": trim(exc, 240)}},
            phase="scheduler",
            span_id=root_span_id(runtime, interaction),
            parent_id="",
            agent_id="runtime",
            status="failed",
            is_terminal=True,
        )

    def patched_process_user_input(self: SpiritKinRuntime, interaction: InteractionInput):
        previous = current_interaction(self)
        self._codex_current_interaction = interaction
        normalized = (interaction.text or "").strip()
        managed_by_handle_input = bool(trace_state(self, interaction).get("handle_input_active")) if normalized else False
        if normalized:
            root_span = root_span_id(self, interaction)
            if not managed_by_handle_input:
                emit_runtime_work(
                    self,
                    interaction,
                    "收到本轮输入，开始进入桌面模型工作流。",
                    detail={"scheduler": {"stage": "input_received"}},
                    phase="scheduler",
                    span_id=root_span,
                    parent_id="",
                    agent_id="runtime",
                    status="started",
                )
            attachments = len(getattr(interaction, "attachments", ()) or ())
            metadata = dict(interaction.metadata or {})
            detail = []
            if attachments:
                detail.append(f"{attachments} 个附件")
            if metadata.get("plan_mode") is True:
                detail.append("计划模式")
            if metadata.get("pursue_goal") is True:
                detail.append("目标推进")
            suffix = f"（{', '.join(detail)}）" if detail else ""
            emit_runtime_work(
                self,
                interaction,
                f"读取当前会话上下文和桌面状态{suffix}。",
                detail={"scheduler": {"attachments": attachments, "plan_mode": metadata.get("plan_mode") is True, "pursue_goal": metadata.get("pursue_goal") is True}},
                phase="scheduler",
                span_id=named_span_id(self, interaction, "scheduler", "context"),
                parent_id=root_span,
                agent_id="runtime",
                status="running",
            )
            emit_runtime_work(
                self,
                interaction,
                "提交到 agent 编排器，准备进行路由选择和模型调用。",
                kind="command",
                detail={"scheduler": {"stage": "dispatch"}},
                phase="scheduler",
                span_id=named_span_id(self, interaction, "scheduler", "dispatch"),
                parent_id=root_span,
                agent_id="runtime",
                status="started",
            )
        try:
            reply = original_process_user_input(self, interaction)
        except Exception as exc:
            if normalized and not managed_by_handle_input:
                emit_root_failure(self, interaction, exc)
                finish_trace_state(self, interaction)
            raise
        else:
            if normalized and not managed_by_handle_input:
                if reply is not None:
                    summarize_reply(self, interaction, reply)
                emit_root_terminal(self, interaction, reply)
                finish_trace_state(self, interaction)
            return reply
        finally:
            if previous is None:
                try:
                    delattr(self, "_codex_current_interaction")
                except Exception:
                    pass
            else:
                self._codex_current_interaction = previous

    def patched_handle_input(self: SpiritKinRuntime, interaction: InteractionInput):
        normalized = (interaction.text or "").strip()
        if normalized:
            state = trace_state(self, interaction)
            state["handle_input_active"] = True
            self._codex_active_work_trace_state = state
            root_span = root_span_id(self, interaction)
            emit_runtime_work(
                self,
                interaction,
                "收到本轮输入，开始进入桌面模型工作流。",
                detail={"scheduler": {"stage": "input_received"}},
                phase="scheduler",
                span_id=root_span,
                parent_id="",
                agent_id="runtime",
                status="started",
            )
        try:
            reply = original_handle_input(self, interaction)
        except Exception as exc:
            if normalized:
                emit_root_failure(self, interaction, exc)
            finish_trace_state(self, interaction)
            raise
        if normalized and reply is not None:
            summarize_reply(self, interaction, reply)
        if normalized:
            emit_root_terminal(self, interaction, reply)
        finish_trace_state(self, interaction)
        return reply

    def patch_agent_cluster() -> None:
        if AgentCluster is None or getattr(AgentCluster, "_codex_work_events_installed", False):
            return
        AgentCluster._codex_work_events_installed = True
        original_process = AgentCluster.process
        original_call_llm_for_agent = AgentCluster._call_llm_for_agent
        original_dispatch_plan = AgentCluster._dispatch_plan
        original_handle_execution = AgentCluster._handle_execution
        original_handle_tool = AgentCluster._handle_tool
        original_handle_skill = AgentCluster._handle_skill

        def runtime_for_agent(agent: Any) -> SpiritKinRuntime | None:
            return getattr(agent, "_codex_runtime", None)

        def interaction_for_agent(agent: Any) -> InteractionInput | None:
            runtime = runtime_for_agent(agent)
            return current_interaction(runtime) if runtime is not None else None

        def agent_trace_id(agent: Any) -> str:
            return str(getattr(agent, "name", "") or getattr(agent, "agent_id", "") or "agent_cluster")

        def emit_agent_work(
            agent: Any,
            text: str,
            *,
            kind: str = "thought",
            detail: dict[str, Any] | None = None,
            phase: str = "agent",
            span_id: str = "",
            parent_id: str = "",
            status: str = "running",
            is_terminal: bool = False,
        ) -> None:
            runtime = runtime_for_agent(agent)
            interaction = interaction_for_agent(agent)
            if runtime is not None and interaction is not None:
                emit_runtime_work(
                    runtime,
                    interaction,
                    text,
                    kind=kind,
                    detail=detail,
                    phase=phase,
                    span_id=span_id,
                    parent_id=parent_id,
                    agent_id=agent_trace_id(agent),
                    status=status,
                    is_terminal=is_terminal,
                )

        def patched_call_llm_for_agent(self: Any, prompt: str, *args: Any, **kwargs: Any):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            if runtime is None or interaction is None:
                return original_call_llm_for_agent(self, prompt, *args, **kwargs)
            try:
                from backend.services.conversation_engine import llm_stream_listener
            except Exception:
                return original_call_llm_for_agent(self, prompt, *args, **kwargs)

            metadata = dict(interaction.metadata or {})
            state = trace_state(runtime, interaction)
            agent_id = str(kwargs.get("agent_name") or agent_trace_id(self) or "main_text")
            profile = dict(getattr(self, "_agent_profiles_by_id", {}).get(agent_id, {}) or {})
            primary_call = agent_id.strip().lower() in {"", "main_text", "agent_cluster"}
            target_label = "Spirit" if primary_call else str(profile.get("label") or agent_id)
            provider = str(kwargs.get("provider") or "")
            model = str(kwargs.get("model_name") or "")
            try:
                brain_decision = self._route_brain_for_agent(agent_id, prompt, route="llm_call")
                provider = provider or str(getattr(brain_decision, "provider", "") or "")
                model = model or str(getattr(brain_decision, "model", "") or "")
            except Exception:
                pass
            external_call = not primary_call
            state["llm_call_index"] = int(state.get("llm_call_index") or 0) + 1
            model_span = named_span_id(runtime, interaction, "model", agent_id, state["llm_call_index"])
            response_id = f"{state['run_id']}:response:{_trace_fragment(agent_id, 'main_text')}"
            model_call_detail = {
                "agent_id": agent_id,
                "caller_agent_id": "main_text",
                "target_agent_id": agent_id,
                "target_label": target_label,
                "provider": provider,
                "model": model,
                "external": external_call,
                "call_index": state["llm_call_index"],
            }
            raw_text = ""
            emitted_text = ""
            stream_index = 0
            reasoning_text = ""
            emitted_reasoning = ""
            pending_reasoning_chars = 0
            reasoning_emit_count = 0
            last_reasoning_emit_at = time.monotonic()
            reasoning_span = f"{model_span}:reasoning"

            def publish_completed_reply() -> None:
                """Release visible reply text only after the model reasoning span closes."""

                nonlocal emitted_text, stream_index
                visible = visible_stream_text(raw_text)
                if not visible or visible == emitted_text:
                    return
                delta = visible[len(emitted_text):] if visible.startswith(emitted_text) else visible
                stream_index += 1
                runtime._emit_runtime_event(
                    {
                        "type": "assistant.delta",
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "payload": {
                            "response_id": response_id,
                            "run_id": state["run_id"],
                            "request_id": str(metadata.get("request_id") or ""),
                            "session_id": str(metadata.get("session_id") or ""),
                            "agent_id": agent_id,
                            "status": "completed",
                            "stream_index": stream_index,
                            "delta": delta,
                            "text": visible,
                            "is_final": True,
                        },
                    }
                )
                emitted_text = visible

            def flush_reasoning(force: bool = False) -> None:
                nonlocal emitted_reasoning, pending_reasoning_chars, reasoning_emit_count, last_reasoning_emit_at
                visible = reasoning_text.strip()
                if len(visible) > 24000:
                    visible = visible[:10000] + "\n……（中间推理过程已省略）……\n" + visible[-10000:]
                if not visible or (visible == emitted_reasoning and not force):
                    return
                if not force:
                    if reasoning_emit_count >= 10:
                        return
                    if pending_reasoning_chars < 800 and time.monotonic() - last_reasoning_emit_at < 0.4:
                        return
                emit_agent_work(
                    self,
                    visible,
                    detail={
                        "model_reasoning": {
                            "provider": provider,
                            "model": model,
                            "visibility": "process",
                        }
                    },
                    phase="model_reasoning",
                    span_id=reasoning_span,
                    parent_id=model_span,
                    status="completed" if force else "stream",
                )
                emitted_reasoning = visible
                pending_reasoning_chars = 0
                reasoning_emit_count += 1
                last_reasoning_emit_at = time.monotonic()

            def on_token(token: str, accumulated: str = "") -> None:
                nonlocal raw_text
                token_text = str(token or "")
                accumulated_text = str(accumulated or "")
                if accumulated_text and len(accumulated_text) >= len(raw_text):
                    raw_text = accumulated_text
                else:
                    raw_text += token_text

            def on_reasoning(token: str, accumulated: str = "") -> None:
                nonlocal reasoning_text, pending_reasoning_chars
                token_text = str(token or "")
                accumulated_text = str(accumulated or "")
                if accumulated_text and len(accumulated_text) >= len(reasoning_text):
                    reasoning_text = accumulated_text
                else:
                    reasoning_text += token_text
                pending_reasoning_chars += len(token_text)
                flush_reasoning()

            emit_agent_work(
                self,
                f"正在调用 {target_label}。",
                kind="call",
                detail={"model_call": model_call_detail},
                phase="model",
                span_id=model_span,
                parent_id=root_span_id(runtime, interaction),
                status="started",
            )
            try:
                with llm_stream_listener(on_token, on_reasoning):
                    result = original_call_llm_for_agent(self, prompt, *args, **kwargs)
            except Exception as exc:
                flush_reasoning(force=True)
                emit_agent_work(
                    self,
                    f"{target_label} 调用失败：{trim(exc, 180)}",
                    kind="call",
                    detail={"model_call": model_call_detail, "error": {"message": trim(exc, 220)}},
                    phase="model",
                    span_id=model_span,
                    parent_id=root_span_id(runtime, interaction),
                    status="failed",
                )
                raise
            else:
                flush_reasoning(force=True)
                if not reasoning_text.strip():
                    # Insert the explicit no-reasoning record inside the model span
                    # without auto-closing that span; the lifecycle completion below
                    # remains the single authoritative model completion event.
                    state.pop("active_span", None)
                    emit_agent_work(
                        self,
                        "模型本轮没有返回独立推理流；已保留上下文读取、路由和调用过程。",
                        detail={
                            "model_reasoning": {
                                "provider": provider,
                                "model": model,
                                "visibility": "summary",
                                "available": False,
                            }
                        },
                        phase="model_reasoning",
                        span_id=reasoning_span,
                        parent_id=model_span,
                        status="completed",
                    )
                emit_agent_work(
                    self,
                    f"{target_label} 调用完成。",
                    kind="call",
                    detail={"model_call": model_call_detail},
                    phase="model",
                    span_id=model_span,
                    parent_id=root_span_id(runtime, interaction),
                    status="completed",
                )
                publish_completed_reply()
            return result

        def patched_process(self: Any, user_input: str, visual_context: str = "", channel: str = "text", input_metadata: dict | None = None):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            context_span = named_span_id(runtime, interaction, "agent", agent_trace_id(self)) if runtime is not None and interaction is not None else ""
            emit_agent_work(
                self,
                "agent 开始读取会话记忆、任务状态和能力清单。",
                detail={"agent": {"stage": "read_context"}},
                phase="agent",
                span_id=context_span,
                parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                status="started",
            )
            try:
                reply = original_process(self, user_input, visual_context=visual_context, channel=channel, input_metadata=input_metadata)
            except Exception as exc:
                emit_agent_work(
                    self,
                    f"读取运行上下文失败：{trim(exc, 180)}",
                    detail={"agent": {"stage": "read_context"}, "error": {"message": trim(exc, 220)}},
                    phase="agent",
                    span_id=context_span,
                    parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                    status="failed",
                )
                raise
            emit_agent_work(
                self,
                "运行上下文已读取。",
                detail={"agent": {"stage": "read_context"}},
                phase="agent",
                span_id=context_span,
                parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                status="completed",
            )
            return reply

        def patched_dispatch_plan(self: Any, context: Any, plan: Any):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            root_span = root_span_id(runtime, interaction) if runtime is not None and interaction is not None else ""
            route = str(getattr(plan, "route", "") or "")
            domain = str(getattr(plan, "domain", "") or "")
            resource = str(getattr(plan, "resource_profile", "") or "")
            reason = str(getattr(plan, "reason", "") or "")
            agent = getattr(plan, "agent", None)
            agent_id = str(getattr(agent, "name", "") or "")
            route_span = named_span_id(runtime, interaction, "route", route or "default") if runtime is not None and interaction is not None else ""
            emit_agent_work(
                self,
                summarize_route(route, domain, resource, reason, agent_id),
                detail={"route": {"name": route, "domain": domain, "resource_profile": resource, "reason": reason, "agent_id": agent_id}},
                phase="route",
                span_id=route_span,
                parent_id=root_span,
                status="completed",
            )
            if route == "tool":
                tool_call = getattr(plan, "tool_call", None)
                tool_name = str(getattr(tool_call, "name", "") or "")
                emit_agent_work(
                    self,
                    f"准备调用工具：{tool_name or 'unknown'}。",
                    kind="command",
                    detail={"tool": {"name": tool_name or "unknown"}},
                    phase="tool",
                    span_id=named_span_id(runtime, interaction, "tool", tool_name or "unknown") if runtime is not None and interaction is not None else "",
                    parent_id=route_span,
                    status="started",
                )
            elif route == "executor":
                req = getattr(plan, "execution_request", None)
                target = str(getattr(req, "target", "") or "--")
                operation = str(getattr(req, "operation", "") or "--")
                emit_agent_work(
                    self,
                    f"准备执行桌面指令：{target}.{operation}。",
                    kind="command",
                    detail={"execution": {"target": target, "operation": operation}},
                    phase="execution",
                    span_id=named_span_id(runtime, interaction, "execution", target, operation) if runtime is not None and interaction is not None else "",
                    parent_id=route_span,
                    status="started",
                )
            elif route == "development_plan":
                emit_agent_work(
                    self,
                    "开始整理开发计划和建议步骤。",
                    detail={"route": {"name": route}},
                    phase="route",
                    span_id=route_span,
                    parent_id=root_span,
                    status="running",
                )
            elif route == "skill":
                spec = getattr(plan, "skill_spec", None)
                skill_name = str(getattr(spec, "name", "") or "unknown")
                emit_agent_work(
                    self,
                    f"准备运行 Skill：{skill_name}。",
                    kind="command",
                    detail={"skill": {"name": skill_name}},
                    phase="skill",
                    span_id=named_span_id(runtime, interaction, "skill", skill_name) if runtime is not None and interaction is not None else "",
                    parent_id=route_span,
                    status="started",
                )
            return original_dispatch_plan(self, context, plan)

        def patched_handle_execution(self: Any, request: Any, user_input: str = "", skip_confirmation: bool = False):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            target = str(getattr(request, "target", "") or "--")
            operation = str(getattr(request, "operation", "") or "--")
            command_preview = execution_command_preview(request)
            execution_span = named_span_id(runtime, interaction, "execution", target, operation) if runtime is not None and interaction is not None else ""
            emit_agent_work(
                self,
                f"运行 {command_preview}。",
                kind="command",
                detail={"execution": {"target": target, "operation": operation, "command": command_preview, "skip_confirmation": bool(skip_confirmation)}},
                phase="execution",
                span_id=execution_span,
                parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                status="started",
            )
            try:
                reply = original_handle_execution(self, request, user_input=user_input, skip_confirmation=skip_confirmation)
            except Exception as exc:
                emit_agent_work(
                    self,
                    f"{command_preview} · 执行异常：{trim(exc, 180)}",
                    kind="command",
                    detail={
                        "execution": {"target": target, "operation": operation, "command": command_preview, "output": ""},
                        "error": {"type": type(exc).__name__, "message": trim(exc, 220)},
                    },
                    phase="execution",
                    span_id=execution_span,
                    parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                    status="failed",
                )
                raise
            metadata = getattr(reply, "metadata", {}) if reply is not None else {}
            execution = metadata.get("execution") if isinstance(metadata, dict) and isinstance(metadata.get("execution"), dict) else {}
            if execution:
                ok = execution.get("success") is not False
                error = trim(execution.get("error") or execution.get("error_code") or "", 180)
                command_preview = execution_command_preview(request, execution)
                output_text = trim(execution_output_preview(execution, getattr(reply, "text", "") or getattr(reply, "spoken_text", "")), 2000)
                if error:
                    emit_agent_work(
                        self,
                        f"{command_preview} · 执行失败：{error}",
                        kind="command",
                        detail={"execution": {**execution, "command": command_preview, "output": output_text}, "error": {"message": error}},
                        phase="execution",
                        span_id=execution_span,
                        parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                        status="failed",
                    )
                else:
                    emit_agent_work(
                        self,
                        f"{command_preview} · {'完成' if ok else '失败'}。",
                        kind="command",
                        detail={"execution": {**execution, "command": command_preview, "output": output_text}, "result": {"success": ok}},
                        phase="execution",
                        span_id=execution_span,
                        parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                        status="completed" if ok else "failed",
                    )
            elif getattr(reply, "requires_confirmation", False):
                emit_agent_work(
                    self,
                    f"需要确认后才能继续执行：{target}.{operation}。",
                    kind="command",
                    detail={"execution": {"target": target, "operation": operation}, "reason": {"requires_confirmation": True}},
                    phase="execution",
                    span_id=execution_span,
                    parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                    status="blocked",
                )
            else:
                response_kind = str(metadata.get("response_kind") or "") if isinstance(metadata, dict) else ""
                failed = reply is None or response_kind in {"policy_denied", "task_failed", "execution_failed"}
                output_text = trim(getattr(reply, "text", "") or getattr(reply, "spoken_text", ""), 2000) if reply is not None else ""
                emit_agent_work(
                    self,
                    f"{command_preview} · {'失败' if failed else '完成'}。",
                    kind="command",
                    detail={
                        "execution": {
                            "target": target,
                            "operation": operation,
                            "command": command_preview,
                            "output": output_text,
                            "success": not failed,
                        },
                        "result": {"success": not failed, "response_kind": response_kind},
                    },
                    phase="execution",
                    span_id=execution_span,
                    parent_id=root_span_id(runtime, interaction) if runtime is not None and interaction is not None else "",
                    status="failed" if failed else "completed",
                )
            return reply

        def patched_handle_tool(self: Any, context: Any, tool_call: Any):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            tool_name = str(getattr(tool_call, "name", "") or "unknown")
            tool_span = named_span_id(runtime, interaction, "tool", tool_name) if runtime is not None and interaction is not None else ""
            parent_span = root_span_id(runtime, interaction) if runtime is not None and interaction is not None else ""
            emit_agent_work(
                self,
                f"调用工具：{tool_name}。",
                kind="command",
                detail={"tool": {"name": tool_name}},
                phase="tool",
                span_id=tool_span,
                parent_id=parent_span,
                status="started",
            )
            try:
                reply = original_handle_tool(self, context, tool_call)
            except Exception as exc:
                emit_agent_work(
                    self,
                    f"工具调用失败：{tool_name} · {trim(exc, 180)}",
                    kind="command",
                    detail={"tool": {"name": tool_name}, "error": {"type": type(exc).__name__, "message": trim(exc, 220)}},
                    phase="tool",
                    span_id=tool_span,
                    parent_id=parent_span,
                    status="failed",
                )
                raise

            metadata = getattr(reply, "metadata", {}) if reply is not None else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            execution = metadata.get("execution") if isinstance(metadata.get("execution"), dict) else {}
            response_kind = str(metadata.get("response_kind") or "")
            requires_confirmation = bool(getattr(reply, "requires_confirmation", False))
            failed = (
                str(getattr(reply, "agent_name", "") or "") == "tool_error"
                or execution.get("success") is False
                or response_kind in {"policy_denied", "task_failed", "tool_failed"}
            )
            status = "blocked" if requires_confirmation else "failed" if failed else "completed"
            result_text = trim(getattr(reply, "text", "") or getattr(reply, "spoken_text", ""), 180)
            emit_agent_work(
                self,
                f"工具 {tool_name} {'等待确认' if requires_confirmation else '执行失败' if failed else '执行完成'}"
                + (f"：{result_text}" if result_text else "。"),
                kind="command",
                detail={
                    "tool": {"name": tool_name},
                    "result": {
                        "success": not failed and not requires_confirmation,
                        "requires_confirmation": requires_confirmation,
                        "response_kind": response_kind,
                        "message": result_text,
                    },
                },
                phase="tool",
                span_id=tool_span,
                parent_id=parent_span,
                status=status,
            )
            return reply

        def patched_handle_skill(self: Any, skill_spec: Any, context: Any):
            runtime = runtime_for_agent(self)
            interaction = interaction_for_agent(self)
            skill_name = str(getattr(skill_spec, "name", "") or "unknown")
            skill_span = named_span_id(runtime, interaction, "skill", skill_name) if runtime is not None and interaction is not None else ""
            parent_span = root_span_id(runtime, interaction) if runtime is not None and interaction is not None else ""
            emit_agent_work(
                self,
                f"运行 Skill：{skill_name}。",
                kind="command",
                detail={"skill": {"name": skill_name}},
                phase="skill",
                span_id=skill_span,
                parent_id=parent_span,
                status="started",
            )
            try:
                reply = original_handle_skill(self, skill_spec, context)
            except Exception as exc:
                emit_agent_work(
                    self,
                    f"Skill 运行失败：{skill_name} · {trim(exc, 180)}",
                    kind="command",
                    detail={"skill": {"name": skill_name}, "error": {"type": type(exc).__name__, "message": trim(exc, 220)}},
                    phase="skill",
                    span_id=skill_span,
                    parent_id=parent_span,
                    status="failed",
                )
                raise

            metadata = getattr(reply, "metadata", {}) if reply is not None else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            skill_run = metadata.get("skill_run") if isinstance(metadata.get("skill_run"), dict) else {}
            requires_confirmation = bool(getattr(reply, "requires_confirmation", False))
            failed = skill_run.get("success") is False or str(metadata.get("response_kind") or "") == "skill_failed_with_assist"
            status = "blocked" if requires_confirmation else "failed" if failed else "completed"
            result_text = trim(getattr(reply, "text", "") or getattr(reply, "spoken_text", ""), 180)
            emit_agent_work(
                self,
                f"Skill {skill_name} {'等待确认' if requires_confirmation else '运行失败' if failed else '运行完成'}"
                + (f"：{result_text}" if result_text else "。"),
                kind="command",
                detail={
                    "skill": {"name": skill_name},
                    "result": {
                        "success": not failed and not requires_confirmation,
                        "requires_confirmation": requires_confirmation,
                        "message": result_text,
                    },
                },
                phase="skill",
                span_id=skill_span,
                parent_id=parent_span,
                status=status,
            )
            return reply

        AgentCluster.process = patched_process
        AgentCluster._call_llm_for_agent = patched_call_llm_for_agent
        AgentCluster._dispatch_plan = patched_dispatch_plan
        AgentCluster._handle_execution = patched_handle_execution
        AgentCluster._handle_tool = patched_handle_tool
        AgentCluster._handle_skill = patched_handle_skill

    patch_agent_cluster()
    SpiritKinRuntime.handle_input = patched_handle_input
    SpiritKinRuntime._process_user_input = patched_process_user_input

    original_init = SpiritKinRuntime.__init__

    def patched_init(self: SpiritKinRuntime, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            self.agent._codex_runtime = self
        except Exception:
            pass

    SpiritKinRuntime.__init__ = patched_init
