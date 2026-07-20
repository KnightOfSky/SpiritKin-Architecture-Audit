using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal string UniqueWorkflowNodeId(string baseNodeId)
    {
        var normalized = Regex.Replace(string.IsNullOrWhiteSpace(baseNodeId) ? "node" : baseNodeId.Trim(), @"[^A-Za-z0-9_]+", "_").Trim('_');
        if (string.IsNullOrWhiteSpace(normalized))
        {
            normalized = "node";
        }
        if (!_workflowEditNodes.Any(item => string.Equals(item.NodeId, normalized, StringComparison.OrdinalIgnoreCase)))
        {
            return normalized;
        }
        var index = 2;
        var candidate = $"{normalized}_{index}";
        while (_workflowEditNodes.Any(item => string.Equals(item.NodeId, candidate, StringComparison.OrdinalIgnoreCase)))
        {
            index++;
            candidate = $"{normalized}_{index}";
        }
        return candidate;
    }

    internal static string DefaultWorkflowNodeIdBase(string nodeType) => nodeType.Trim().ToLowerInvariant() switch
    {
        "tool_call" => "tool",
        "skill_call" => "skill",
        "review_gate" => "review",
        "branch" => "branch",
        "subgraph" => "subgraph",
        "waiter" => "wait",
        "external_callback" => "callback",
        "workflow.android_step" => "android_step",
        "automation.android_step" => "android_step",
        _ when IsOpenWorkflowNodeType(nodeType) => "custom_node",
        _ => "agent",
    };

    internal static string DefaultWorkflowNodeLabel(string nodeType) => nodeType.Trim().ToLowerInvariant() switch
    {
        "tool_call" => "工具自动化",
        "skill_call" => "Skill 自动化",
        "review_gate" => "审核门禁",
        "branch" => "条件分支",
        "subgraph" => "子工作流",
        "waiter" => "等待信号",
        "external_callback" => "外部回调",
        "workflow.android_step" => "Android 步骤",
        "automation.android_step" => "Android 步骤",
        _ when IsOpenWorkflowNodeType(nodeType) => "开放节点",
        _ => "Agent 任务",
    };

    internal static string DefaultWorkflowNodeArguments(string nodeType) => nodeType.Trim().ToLowerInvariant() switch
    {
        "branch" => "{\n  \"condition\": true,\n  \"routes\": {\n    \"true\": [],\n    \"false\": []\n  }\n}",
        "subgraph" => "{\n  \"workflow_name\": \"child.workflow.v1\",\n  \"inputs\": {}\n}",
        "waiter" => "{\n  \"wait_for\": \"external_signal\"\n}",
        "external_callback" => "{\n  \"callback_id\": \"callback_id\"\n}",
        "workflow.android_step" => "{\n  \"device_id\": \"{{android_device_id}}\",\n  \"operation\": \"app.launch\",\n  \"params\": {\n    \"app_name\": \"{{app_name}}\"\n  }\n}",
        "automation.android_step" => "{\n  \"device_id\": \"{{android_device_id}}\",\n  \"operation\": \"app.launch\",\n  \"params\": {\n    \"app_name\": \"{{app_name}}\"\n  }\n}",
        _ when IsOpenWorkflowNodeType(nodeType) => "{\n  \"executor\": \"external_callback\",\n  \"callback_id\": \"{{callback_id}}\"\n}",
        _ => "{}",
    };

    internal static string WorkflowNodeTypeColorHex(string nodeType) => nodeType.Trim().ToLowerInvariant() switch
    {
        "agent_task" => "#1557E0",
        "tool_call" => "#16A34A",
        "skill_call" => "#2563EB",
        "review_gate" => "#D97706",
        "branch" => "#7C3AED",
        "subgraph" => "#0891B2",
        "waiter" => "#0F766E",
        "external_callback" => "#BE123C",
        "workflow.android_step" => "#0D9488",
        "automation.android_step" => "#0D9488",
        _ when IsOpenWorkflowNodeType(nodeType) => "#64748B",
        _ => "#64748B",
    };

    internal static bool IsOpenWorkflowNodeType(string nodeType)
    {
        var normalized = (nodeType ?? "").Trim().ToLowerInvariant();
        return normalized.StartsWith("custom.", StringComparison.Ordinal)
            || normalized.StartsWith("external.", StringComparison.Ordinal)
            || normalized.StartsWith("integration.", StringComparison.Ordinal)
            || normalized.StartsWith("automation.", StringComparison.Ordinal);
    }

    internal static string WorkflowPortKindColorHex(string portKind, string fallback) => WorkflowConnectionRules.NormalizePortKind(portKind) switch
    {
        "artifact" => "#DC2626",
        "knowledge" => "#7C3AED",
        "signal" => "#0F766E",
        "review" => "#D97706",
        "automation" => "#16A34A",
        "control" => "#1557E0",
        "execution" => fallback,
        _ => fallback,
    };

    internal static double ReadWorkflowNodeCoordinate(string text, double fallback)
    {
        if (!double.TryParse(text, out var value))
        {
            return fallback;
        }
        return Math.Max(0, Math.Round(value));
    }

}
