using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal static IEnumerable<(string Title, string Detail, string Key)> DescribeRuntimeWorkSteps(RuntimeEvent ev)
    {
        var payload = ev.Payload;
        if (payload.ValueKind != JsonValueKind.Object)
        {
            yield break;
        }

        switch (ev.Type)
        {
            case RealtimeContract.Events.AssistantMessage:
                foreach (var step in DescribeAssistantMessageWorkSteps(payload))
                {
                    yield return step;
                }
                break;
            case RealtimeContract.Events.AssistantConfirmationRequested:
                yield return DescribeConfirmationWorkStep(payload);
                break;
            case RealtimeContract.Events.AssistantExecutionUpdated:
                yield return DescribeExecutionWorkStep(payload);
                break;
            case RealtimeContract.Events.AssistantTaskUpdated:
                yield return ("工作指令", DescribeTaskWorkStep(payload), DescribeTaskWorkStepKey(payload));
                break;
            case RealtimeContract.Events.AssistantProjectUpdated:
                yield return ("工作指令", DescribeProjectWorkStep(payload), DescribeProjectWorkStepKey(payload));
                break;
        }
    }

    internal static IEnumerable<(string Title, string Detail, string Key)> DescribeAssistantMessageWorkSteps(JsonElement payload)
    {
        if (payload.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Object)
        {
            if (data.TryGetProperty("scheduler", out var scheduler) && scheduler.ValueKind == JsonValueKind.Object)
            {
                var route = ReadJsonString(scheduler, "route");
                var domain = ReadJsonString(scheduler, "domain");
                var reason = ReadJsonString(scheduler, "reason");
                var resource = ReadJsonString(scheduler, "resource_profile");
                var routeSentence = DescribeRouteSentence(route, domain, resource, reason);
                if (!string.IsNullOrWhiteSpace(routeSentence))
                {
                    yield return ("思考", routeSentence, RouteStepKey(route, domain, resource));
                }
            }
            if (data.TryGetProperty("development_plan", out var devPlan) && devPlan.ValueKind == JsonValueKind.Object)
            {
                var summary = ReadJsonString(devPlan, "summary");
                yield return ("思考", string.IsNullOrWhiteSpace(summary)
                    ? "模型把这次请求整理成开发计划，等待你确认后再进入实现。"
                    : $"模型把这次请求整理成开发计划：{TrimStatusText(summary, 220)}", PlanStepKey(summary));
                var actionIndex = 0;
                foreach (var action in ReadJsonStringArray(devPlan, "suggested_actions").Take(3))
                {
                    yield return ("工作指令", $"建议下一步：{TrimStatusText(action, 220)}", $"plan_action:{actionIndex++}");
                }
            }
            var hasExecution = data.TryGetProperty("execution", out var execution) && execution.ValueKind == JsonValueKind.Object;
            if (hasExecution)
            {
                yield return DescribeExecutionWorkStep(execution);
            }
            // 执行器结果同时带 tool_name 时，只保留带实参的 execution 行；旧的 app.launch
            // 摘要没有 cmd，继续投影会在真实命令组后留下灰色伪步骤。
            if (!hasExecution && data.TryGetProperty("tool_name", out var toolName) && toolName.ValueKind == JsonValueKind.String)
            {
                var name = toolName.GetString() ?? "";
                yield return ("工作指令", $"调用工具：{name}", $"tool:{name}");
            }
        }
    }

    internal static (string Title, string Detail, string Key) DescribeAssistantWorkUpdatedStep(JsonElement payload)
    {
        var text = ReadJsonString(payload, "text");
        var key = WorkUpdatedStepKey(payload);
        if (payload.TryGetProperty("detail", out var detail) && detail.ValueKind == JsonValueKind.Object)
        {
            if (detail.TryGetProperty("model_call", out var modelCall) && modelCall.ValueKind == JsonValueKind.Object)
            {
                return ("调用", text, key);
            }
            if (detail.TryGetProperty("execution", out var execution) && execution.ValueKind == JsonValueKind.Object)
            {
                var executionStep = DescribeExecutionWorkStep(execution);
                return ("工作指令", string.IsNullOrWhiteSpace(text) ? executionStep.Detail : text, executionStep.Key);
            }
        }

        var kind = ReadJsonString(payload, "kind", "thought");
        return (string.Equals(kind, "command", StringComparison.OrdinalIgnoreCase) ? "工作指令" : "思考", text, key);
    }

    internal static string DescribeRouteSentence(string route, string domain, string resource, string reason)
    {
        if (string.IsNullOrWhiteSpace(route) && string.IsNullOrWhiteSpace(reason))
        {
            return "";
        }
        var routeLabel = route switch
        {
            "general" => "普通回答路径",
            "agent" => "专业 agent 路径",
            "executor" => "桌面执行器路径",
            "tool" => "工具调用路径",
            "development_plan" => "开发计划路径",
            "intent" => "意图纠错路径",
            "builtin" => "内置工具路径",
            _ => string.IsNullOrWhiteSpace(route) ? "默认路径" : $"{route} 路径",
        };
        var parts = new List<string> { $"模型选择了 {routeLabel}" };
        if (!string.IsNullOrWhiteSpace(domain))
        {
            parts.Add($"领域是 {domain}");
        }
        if (!string.IsNullOrWhiteSpace(resource))
        {
            parts.Add($"资源通道是 {resource}");
        }
        if (!string.IsNullOrWhiteSpace(reason))
        {
            parts.Add($"原因：{TrimStatusText(reason, 180)}");
        }
        return string.Join("；", parts) + "。";
    }

    internal static (string Title, string Detail, string Key) DescribeConfirmationWorkStep(JsonElement payload)
    {
        var target = ReadJsonString(payload, "pending_target", "--");
        var operation = ReadJsonString(payload, "pending_operation", "--");
        var risk = ReadJsonString(payload, "risk_level");
        var detail = $"等待确认 · {target}.{operation}";
        if (!string.IsNullOrWhiteSpace(risk))
        {
            detail += $" · {risk}";
        }
        return ("工作指令", $"需要确认后才能继续执行：{detail}", $"confirm:{target}.{operation}");
    }

    internal static (string Title, string Detail, string Key) DescribeExecutionWorkStep(JsonElement payload)
    {
        var target = ReadJsonString(payload, "target", "--");
        var operation = ReadJsonString(payload, "operation", "--");
        var ok = !payload.TryGetProperty("success", out var success) || success.ValueKind != JsonValueKind.False;
        var error = ReadJsonString(payload, "error");
        var command = ExecutionCommandPreview(payload);
        var subject = string.IsNullOrWhiteSpace(command) ? $"{target}.{operation}" : command;
        var key = $"exec:{target}.{operation}:{(string.IsNullOrWhiteSpace(error) ? (ok ? "ok" : "fail") : "fail")}";
        return string.IsNullOrWhiteSpace(error)
            ? ("工作指令", $"{subject} · {(ok ? "完成" : "失败")}。", key)
            : ("工作指令", $"{subject} · 执行失败：{TrimStatusText(error, 180)}", key);
    }

    private static string ExecutionCommandPreview(JsonElement payload)
    {
        foreach (var candidate in ExecutionCommandContainers(payload))
        {
            foreach (var key in new[] { "command", "cmd", "app_name", "app", "path", "url", "resolved_app" })
            {
                var value = ReadJsonString(candidate, key).Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
        }
        return "";
    }

    private static IEnumerable<JsonElement> ExecutionCommandContainers(JsonElement payload)
    {
        yield return payload;
        foreach (var key in new[] { "arguments", "params", "data", "result", "execution" })
        {
            if (payload.TryGetProperty(key, out var nested) && nested.ValueKind == JsonValueKind.Object)
            {
                yield return nested;
                foreach (var nestedKey in new[] { "arguments", "params", "data", "result" })
                {
                    if (nested.TryGetProperty(nestedKey, out var deeper) && deeper.ValueKind == JsonValueKind.Object)
                    {
                        yield return deeper;
                    }
                }
            }
        }
    }

    internal static string DescribeTaskWorkStep(JsonElement payload)
    {
        var status = ReadJsonString(payload, "status", "updated");
        var request = ReadJsonString(payload, "request", ReadJsonString(payload, "title"));
        return string.IsNullOrWhiteSpace(request)
            ? $"更新任务状态：{status}。"
            : $"更新任务状态为 {status}：{TrimStatusText(request, 180)}";
    }

    internal static string DescribeProjectWorkStep(JsonElement payload)
    {
        var title = ReadJsonString(payload, "project_type", ReadJsonString(payload, "title"));
        return string.IsNullOrWhiteSpace(title)
            ? "更新项目状态。"
            : $"更新项目：{TrimStatusText(title, 180)}";
    }

    internal static string RouteStepKey(string route, string domain, string resource)
    {
        var r = (route ?? "").Trim().ToLowerInvariant();
        var d = (domain ?? "").Trim().ToLowerInvariant();
        var res = (resource ?? "").Trim().ToLowerInvariant();
        return $"route:{r}|{d}|{res}";
    }

    internal static string PlanStepKey(string summary)
    {
        return "plan_summary";
    }

    internal static string DescribeTaskWorkStepKey(JsonElement payload)
    {
        var status = ReadJsonString(payload, "status", "updated").Trim().ToLowerInvariant();
        var request = ReadJsonString(payload, "request", ReadJsonString(payload, "title")).Trim().ToLowerInvariant();
        return $"task:{status}:{request}";
    }

    internal static string DescribeProjectWorkStepKey(JsonElement payload)
    {
        var title = ReadJsonString(payload, "project_type", ReadJsonString(payload, "title")).Trim().ToLowerInvariant();
        return $"project:{title}";
    }

    internal static string WorkUpdatedStepKey(JsonElement payload)
    {
        if (!payload.TryGetProperty("detail", out var detail) || detail.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        if (detail.TryGetProperty("scheduler", out var scheduler) && scheduler.ValueKind == JsonValueKind.Object)
        {
            var route = ReadJsonString(scheduler, "route");
            var domain = ReadJsonString(scheduler, "domain");
            var resource = ReadJsonString(scheduler, "resource_profile");
            return string.IsNullOrWhiteSpace(route) && string.IsNullOrWhiteSpace(domain) && string.IsNullOrWhiteSpace(resource)
                ? ""
                : RouteStepKey(route, domain, resource);
        }
        if (detail.TryGetProperty("route", out var routeElement) && routeElement.ValueKind == JsonValueKind.String)
        {
            return RouteStepKey(ReadJsonString(detail, "route"), ReadJsonString(detail, "domain"), ReadJsonString(detail, "resource_profile"));
        }
        if (detail.TryGetProperty("route", out routeElement) && routeElement.ValueKind == JsonValueKind.Object)
        {
            return RouteStepKey(ReadJsonString(routeElement, "name"), ReadJsonString(routeElement, "domain"), ReadJsonString(routeElement, "resource_profile"));
        }
        if (detail.TryGetProperty("execution", out var execution) && execution.ValueKind == JsonValueKind.Object)
        {
            return DescribeExecutionWorkStep(execution).Key;
        }
        if (detail.TryGetProperty("development_plan", out var devPlan) && devPlan.ValueKind == JsonValueKind.Object)
        {
            return PlanStepKey(ReadJsonString(devPlan, "summary"));
        }
        return "";
    }

    internal static TraceMeta ReadTraceMeta(JsonElement payload, string type = "")
    {
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return default;
        }
        var t = (type ?? "").Trim().ToLowerInvariant();
        var isTerminal = t.EndsWith(".completed", StringComparison.Ordinal)
            || t.EndsWith(".failed", StringComparison.Ordinal)
            || t.EndsWith(".cancelled", StringComparison.Ordinal)
            || ReadJsonBool(payload, "is_terminal");
        var agentId = ReadJsonString(payload, "agent_id");
        var messageId = "";
        var stepKind = "";
        var callAgent = "";
        var callModel = "";
        var callProvider = "";
        var commandText = "";
        var commandOutput = "";
        var shellLabel = "";
        if (payload.TryGetProperty("detail", out var detailEl) && detailEl.ValueKind == JsonValueKind.Object)
        {
            messageId = ReadJsonString(detailEl, "message_id");
            if (string.IsNullOrEmpty(agentId))
            {
                agentId = ReadJsonString(detailEl, "agent_id");
                if (string.IsNullOrEmpty(agentId) && detailEl.TryGetProperty("scheduler", out var sched) && sched.ValueKind == JsonValueKind.Object)
                {
                    agentId = ReadJsonString(sched, "agent_id");
                }
            }
            if (detailEl.TryGetProperty("model_call", out var modelCall) && modelCall.ValueKind == JsonValueKind.Object)
            {
                stepKind = "call";
                callAgent = ReadJsonString(modelCall, "target_label", ReadJsonString(modelCall, "target_agent_id", ReadJsonString(modelCall, "agent_id")));
                callModel = ReadJsonString(modelCall, "model");
                callProvider = ReadJsonString(modelCall, "provider");
            }
            if (detailEl.TryGetProperty("tool", out var tool) && tool.ValueKind == JsonValueKind.Object)
            {
                var toolName = ReadJsonString(tool, "name").Trim();
                if (!string.IsNullOrWhiteSpace(toolName))
                {
                    stepKind = "command";
                    commandText = toolName;
                    commandOutput = ReadExecutionOutput(detailEl);
                    shellLabel = "Tool";
                }
            }
            if (detailEl.TryGetProperty("skill", out var skill) && skill.ValueKind == JsonValueKind.Object)
            {
                var skillName = ReadJsonString(skill, "name").Trim();
                if (!string.IsNullOrWhiteSpace(skillName))
                {
                    stepKind = "command";
                    commandText = skillName;
                    commandOutput = ReadExecutionOutput(detailEl);
                    shellLabel = "Skill";
                }
            }
            if (string.Equals(ReadJsonString(detailEl, "card_kind"), "model_dispatch", StringComparison.OrdinalIgnoreCase))
            {
                stepKind = "call";
                ReadDispatchTargets(detailEl, out callAgent, out callModel, out callProvider);
            }
            if (detailEl.TryGetProperty("execution", out var execution) && execution.ValueKind == JsonValueKind.Object)
            {
                stepKind = "command";
                commandText = ExecutionCommandPreview(execution);
                commandOutput = ReadExecutionOutput(execution);
                shellLabel = ReadJsonString(execution, "shell");
                if (string.IsNullOrWhiteSpace(commandText))
                {
                    var target = ReadJsonString(execution, "target").Trim();
                    var operation = ReadJsonString(execution, "operation").Trim();
                    commandText = string.Join(".", new[] { target, operation }.Where(value => !string.IsNullOrWhiteSpace(value)));
                    if (!string.IsNullOrWhiteSpace(commandText))
                    {
                        shellLabel = "Tool";
                    }
                }
                if (string.IsNullOrWhiteSpace(shellLabel))
                {
                    shellLabel = InferShellLabel(commandText);
                }
            }
        }
        return new TraceMeta
        {
            Seq = ReadJsonLong(payload, "seq"),
            RunId = ReadJsonString(payload, "run_id"),
            EventId = ReadJsonString(payload, "event_id"),
            SpanId = ReadJsonString(payload, "span_id"),
            ParentId = ReadJsonString(payload, "parent_id"),
            Status = ReadJsonString(payload, "status"),
            IsTerminal = isTerminal,
            AgentId = agentId,
            MessageId = messageId,
            StepKind = stepKind,
            CallAgent = callAgent,
            CallModel = callModel,
            CallProvider = callProvider,
            CommandText = commandText,
            CommandOutput = commandOutput,
            ShellLabel = shellLabel,
        };
    }

    private static void ReadDispatchTargets(JsonElement detail, out string agents, out string models, out string providers)
    {
        var agentItems = new List<string>();
        var modelItems = new List<string>();
        var providerItems = new List<string>();
        if (detail.TryGetProperty("call_targets", out var targets) && targets.ValueKind == JsonValueKind.Array)
        {
            foreach (var target in targets.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object))
            {
                var agent = ReadJsonString(target, "label", ReadJsonString(target, "agent_id"));
                var model = ReadJsonString(target, "model");
                var provider = ReadJsonString(target, "provider");
                if (!string.IsNullOrWhiteSpace(agent)) agentItems.Add(agent);
                if (!string.IsNullOrWhiteSpace(model)) modelItems.Add(model);
                if (!string.IsNullOrWhiteSpace(provider)) providerItems.Add(provider);
            }
        }
        if (agentItems.Count == 0)
        {
            agentItems.AddRange(ReadJsonStringArray(detail, "targets"));
        }
        agents = string.Join(", ", agentItems.Distinct(StringComparer.OrdinalIgnoreCase));
        models = string.Join(", ", modelItems.Distinct(StringComparer.OrdinalIgnoreCase));
        providers = string.Join(", ", providerItems.Distinct(StringComparer.OrdinalIgnoreCase));
    }

    private static string ReadExecutionOutput(JsonElement execution)
    {
        foreach (var candidate in ExecutionCommandContainers(execution))
        {
            foreach (var key in new[] { "output", "stdout", "message", "result_text" })
            {
                var value = ReadJsonString(candidate, key).Trim();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
        }
        return "";
    }

    private static string InferShellLabel(string command)
    {
        var normalized = (command ?? "").Trim().ToLowerInvariant();
        if (normalized == "cmd" || normalized.StartsWith("cmd ", StringComparison.Ordinal) || normalized.StartsWith("cmd.exe", StringComparison.Ordinal))
        {
            return "CMD";
        }
        if (normalized.StartsWith("powershell", StringComparison.Ordinal) || normalized.StartsWith("pwsh", StringComparison.Ordinal))
        {
            return "PowerShell";
        }
        if (normalized.StartsWith("bash", StringComparison.Ordinal)
            || normalized.StartsWith("sh ", StringComparison.Ordinal)
            || normalized.StartsWith("zsh", StringComparison.Ordinal))
        {
            return "Shell";
        }
        return string.IsNullOrWhiteSpace(normalized) ? "" : "CMD";
    }
}
