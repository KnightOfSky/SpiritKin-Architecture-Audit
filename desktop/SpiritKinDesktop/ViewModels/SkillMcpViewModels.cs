using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace SpiritKinDesktop;

public sealed class SkillViewModel
{
    public SkillViewModel(string name, string description, string status, string version, string riskLevel, string[] triggerIntents, string[] toolAllowlist, string stepsJson, string rollbackStrategy, string[] successCriteria, string ownerAgentId, string ownerDomain, string workspacePath, string sourceType, string promotionStatus, string reviewGate, string uiBindingStatus, string uiBindingsJson, string debugSummary = "")
    {
        Name = name;
        Description = description;
        Status = string.IsNullOrWhiteSpace(status) ? "candidate" : status;
        Version = string.IsNullOrWhiteSpace(version) ? "0.1.0" : version;
        RiskLevel = string.IsNullOrWhiteSpace(riskLevel) ? "low" : riskLevel;
        TriggerIntents = triggerIntents;
        ToolAllowlist = toolAllowlist;
        StepsJson = string.IsNullOrWhiteSpace(stepsJson) ? "[]" : stepsJson;
        RollbackStrategy = string.IsNullOrWhiteSpace(rollbackStrategy) ? "manual_review" : rollbackStrategy;
        SuccessCriteria = successCriteria;
        OwnerAgentId = string.IsNullOrWhiteSpace(ownerAgentId) ? "skill_runner" : ownerAgentId;
        OwnerDomain = string.IsNullOrWhiteSpace(ownerDomain) ? "skill" : ownerDomain;
        WorkspacePath = string.IsNullOrWhiteSpace(workspacePath) ? $"state/agents/{OwnerAgentId}/workspace" : workspacePath;
        SourceType = string.IsNullOrWhiteSpace(sourceType) ? "human" : sourceType;
        PromotionStatus = string.IsNullOrWhiteSpace(promotionStatus) ? Status : promotionStatus;
        ReviewGate = string.IsNullOrWhiteSpace(reviewGate) ? "core_review" : reviewGate;
        UiBindingStatus = uiBindingStatus;
        UiBindingsJson = string.IsNullOrWhiteSpace(uiBindingsJson) ? "[]" : uiBindingsJson;
        DebugSummary = string.IsNullOrWhiteSpace(debugSummary) ? "调试：暂无运行记录" : debugSummary;
        StatusLabel = UiDisplayText.Status(Status);
        RiskLabel = UiDisplayText.Risk(RiskLevel);
        var bindingMeta = string.IsNullOrWhiteSpace(UiBindingStatus) ? "" : $" · UI {UiDisplayText.Status(UiBindingStatus)}";
        Meta = $"版本 {Version} · 归属 {UiDisplayText.Domain(OwnerAgentId)} · {RiskLabel} · 触发 {TriggerIntents.Length} · 工具 {ToolAllowlist.Length}{bindingMeta}";
        StatusBrush = Status.ToLowerInvariant() switch
        {
            "active" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "candidate" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
            "draft" => new SolidColorBrush(Color.FromRgb(203, 213, 225)),
            "rejected" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            "archived" => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
            _ => new SolidColorBrush(Color.FromRgb(203, 213, 225)),
        };
    }

    public string Name { get; }
    public string Description { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Version { get; }
    public string RiskLevel { get; }
    public string RiskLabel { get; }
    public string[] TriggerIntents { get; }
    public string[] ToolAllowlist { get; }
    public string StepsJson { get; }
    public string RollbackStrategy { get; }
    public string[] SuccessCriteria { get; }
    public string OwnerAgentId { get; }
    public string OwnerDomain { get; }
    public string WorkspacePath { get; }
    public string SourceType { get; }
    public string PromotionStatus { get; }
    public string ReviewGate { get; }
    public string UiBindingStatus { get; }
    public string UiBindingsJson { get; }
    public string DebugSummary { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }
}

public sealed class SkillSourceViewModel
{
    public SkillSourceViewModel(string sourceId, string label, string url, string branch, string sourceType, string status, string trustLevel, string targetScope, string quarantinePath, int candidateCount, int scannedFileCount, string[] warnings)
    {
        SourceId = string.IsNullOrWhiteSpace(sourceId) ? "source" : sourceId;
        Label = string.IsNullOrWhiteSpace(label) ? SourceId : label;
        Url = url;
        Branch = branch;
        SourceType = string.IsNullOrWhiteSpace(sourceType) ? "git" : sourceType;
        Status = string.IsNullOrWhiteSpace(status) ? "registered" : status;
        TrustLevel = string.IsNullOrWhiteSpace(trustLevel) ? "untrusted" : trustLevel;
        TargetScope = string.IsNullOrWhiteSpace(targetScope) ? "project" : targetScope;
        QuarantinePath = quarantinePath;
        CandidateCount = candidateCount;
        ScannedFileCount = scannedFileCount;
        Warnings = warnings;
        StatusLabel = Status.ToLowerInvariant() switch
        {
            "synced" => "已同步",
            "scanned" => "已扫描",
            "registered" => "已注册",
            "sync_failed" => "同步失败",
            "missing_quarantine" => "缺隔离区",
            _ => UiDisplayText.Status(Status),
        };
        var branchText = string.IsNullOrWhiteSpace(Branch) ? "默认分支" : Branch;
        var warningText = Warnings.Length == 0 ? "" : $" · 警告 {Warnings.Length}";
        Meta = $"{SourceType} · {branchText} · {TrustLevel} · 候选 {CandidateCount} · 文件 {ScannedFileCount}{warningText}";
        StatusBrush = Status.ToLowerInvariant() switch
        {
            "synced" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "scanned" => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
            "registered" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
            "sync_failed" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            "missing_quarantine" => new SolidColorBrush(Color.FromRgb(220, 38, 38)),
            _ => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
        };
    }

    public string SourceId { get; }
    public string Label { get; }
    public string Url { get; }
    public string Branch { get; }
    public string SourceType { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string TrustLevel { get; }
    public string TargetScope { get; }
    public string QuarantinePath { get; }
    public int CandidateCount { get; }
    public int ScannedFileCount { get; }
    public string[] Warnings { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }
}

public sealed class McpServerViewModel
{
    public McpServerViewModel(
        string serverId,
        string label,
        string transport,
        string command,
        string url,
        string[] args,
        bool enabled,
        string reviewState,
        string trustLevel,
        string workspaceScope,
        string[] ownerAgentIds,
        string[] envRefs,
        string[] filesystemScopes,
        string[] networkScopes,
        string[] resources,
        string[] prompts,
        string toolsJson,
        string notes,
        string healthStatus,
        string[] healthIssues)
    {
        ServerId = string.IsNullOrWhiteSpace(serverId) ? "mcp-server" : serverId;
        Label = string.IsNullOrWhiteSpace(label) ? ServerId : label;
        Transport = string.IsNullOrWhiteSpace(transport) ? "stdio" : transport;
        Command = command;
        Url = url;
        Args = args;
        Enabled = enabled;
        ReviewState = string.IsNullOrWhiteSpace(reviewState) ? "candidate" : reviewState;
        TrustLevel = string.IsNullOrWhiteSpace(trustLevel) ? "untrusted" : trustLevel;
        WorkspaceScope = string.IsNullOrWhiteSpace(workspaceScope) ? "project" : workspaceScope;
        OwnerAgentIds = ownerAgentIds;
        EnvRefs = envRefs;
        FilesystemScopes = filesystemScopes;
        NetworkScopes = networkScopes;
        Resources = resources;
        Prompts = prompts;
        ToolsJson = string.IsNullOrWhiteSpace(toolsJson) ? "[]" : toolsJson;
        Notes = notes;
        HealthStatus = string.IsNullOrWhiteSpace(healthStatus) ? (enabled ? "needs_attention" : "disabled") : healthStatus;
        HealthIssues = healthIssues;
        ToolCount = CountJsonArray(ToolsJson);
        StatusLabel = BuildStatusLabel(Enabled, ReviewState, HealthStatus);
        Meta = $"{Transport} · {UiDisplayText.Status(ReviewState)} · {TrustLevel} · tools {ToolCount} · agents {OwnerAgentIds.Length}";
        HealthLine = BuildHealthLine(HealthStatus, HealthIssues);
        EndpointLine = BuildEndpointLine(Transport, Command, Url, Args);
        StatusBrush = HealthStatus.ToLowerInvariant() switch
        {
            "ready" => new SolidColorBrush(Color.FromRgb(22, 163, 74)),
            "needs_attention" => new SolidColorBrush(Color.FromRgb(217, 119, 6)),
            "disabled" => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
            _ when !Enabled => new SolidColorBrush(Color.FromRgb(148, 163, 184)),
            _ => new SolidColorBrush(Color.FromRgb(2, 80, 204)),
        };
    }

    public string ServerId { get; }
    public string Label { get; }
    public string Transport { get; }
    public string Command { get; }
    public string Url { get; }
    public string[] Args { get; }
    public bool Enabled { get; }
    public string ReviewState { get; }
    public string TrustLevel { get; }
    public string WorkspaceScope { get; }
    public string[] OwnerAgentIds { get; }
    public string[] EnvRefs { get; }
    public string[] FilesystemScopes { get; }
    public string[] NetworkScopes { get; }
    public string[] Resources { get; }
    public string[] Prompts { get; }
    public string ToolsJson { get; }
    public string Notes { get; }
    public string HealthStatus { get; }
    public string[] HealthIssues { get; }
    public int ToolCount { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public string HealthLine { get; }
    public string EndpointLine { get; }
    public Brush StatusBrush { get; }

    public static McpServerViewModel FromJson(JsonElement server)
    {
        var healthStatus = "";
        var healthIssues = Array.Empty<string>();
        if (TryReadObject(server, "health", out var health))
        {
            healthStatus = ReadString(health, "status", "");
            healthIssues = ReadStringArray(health, "issues");
        }
        return new McpServerViewModel(
            ReadString(server, "server_id", "mcp-server"),
            ReadString(server, "label", ReadString(server, "server_id", "mcp-server")),
            ReadString(server, "transport", "stdio"),
            ReadString(server, "command"),
            ReadString(server, "url"),
            ReadStringArray(server, "args"),
            ReadBool(server, "enabled", false),
            ReadString(server, "review_state", "candidate"),
            ReadString(server, "trust_level", "untrusted"),
            ReadString(server, "workspace_scope", "project"),
            ReadStringArray(server, "owner_agent_ids"),
            ReadStringArray(server, "env_refs"),
            ReadStringArray(server, "filesystem_scopes"),
            ReadStringArray(server, "network_scopes"),
            ReadStringArray(server, "resources"),
            ReadStringArray(server, "prompts"),
            FormatJsonArray(server, "tools"),
            ReadString(server, "notes"),
            healthStatus,
            healthIssues);
    }

    private static string BuildStatusLabel(bool enabled, string reviewState, string healthStatus)
    {
        if (!enabled)
        {
            return "未启用";
        }
        if (string.Equals(healthStatus, "ready", StringComparison.OrdinalIgnoreCase))
        {
            return "可导出";
        }
        if (!string.Equals(reviewState, "approved", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(reviewState, "active", StringComparison.OrdinalIgnoreCase))
        {
            return "待审核";
        }
        return UiDisplayText.Status(healthStatus);
    }

    private static string BuildHealthLine(string healthStatus, string[] issues)
    {
        var status = UiDisplayText.Status(healthStatus);
        return issues.Length == 0 ? $"健康：{status}" : $"健康：{status} · {string.Join(", ", issues)}";
    }

    private static string BuildEndpointLine(string transport, string command, string url, string[] args)
    {
        if (string.Equals(transport, "stdio", StringComparison.OrdinalIgnoreCase))
        {
            var argText = args.Length == 0 ? "" : $" {string.Join(" ", args)}";
            return string.IsNullOrWhiteSpace(command) ? "stdio · 未配置命令" : $"stdio · {command}{argText}";
        }
        return string.IsNullOrWhiteSpace(url) ? $"{transport} · 未配置 URL" : $"{transport} · {url}";
    }

    private static string FormatJsonArray(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return "[]";
        }
        return JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true });
    }

    private static int CountJsonArray(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            return doc.RootElement.ValueKind == JsonValueKind.Array ? doc.RootElement.GetArrayLength() : 0;
        }
        catch
        {
            return 0;
        }
    }

    private static bool TryReadObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(key, out value)
            && value.ValueKind == JsonValueKind.Object;
    }

    private static string ReadString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? fallback,
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => fallback,
        };
    }

    private static bool ReadBool(JsonElement element, string key, bool fallback)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String when bool.TryParse(value.GetString(), out var parsed) => parsed,
            _ => fallback,
        };
    }

    private static string[] ReadStringArray(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return Array.Empty<string>();
        }
        if (value.ValueKind == JsonValueKind.Array)
        {
            return value.EnumerateArray()
                .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.GetRawText())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Select(item => item!.Trim())
                .ToArray();
        }
        if (value.ValueKind == JsonValueKind.String)
        {
            return value.GetString()?
                .Split(new[] { ',', ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .ToArray() ?? Array.Empty<string>();
        }
        return Array.Empty<string>();
    }
}

public sealed class McpToolMappingViewModel
{
    public McpToolMappingViewModel(
        string mcpServer,
        string serverLabel,
        string serverTransport,
        string mcpToolName,
        string internalToolName,
        string target,
        string operation,
        string riskLevel,
        bool readOnly,
        bool confirmationRequired,
        string[] ownerAgentIds,
        string workspaceScope)
    {
        McpServer = mcpServer;
        ServerLabel = string.IsNullOrWhiteSpace(serverLabel) ? mcpServer : serverLabel;
        ServerTransport = serverTransport;
        McpToolName = mcpToolName;
        InternalToolName = string.IsNullOrWhiteSpace(internalToolName) ? $"{mcpServer}.{mcpToolName}" : internalToolName;
        Target = string.IsNullOrWhiteSpace(target) ? "mcp" : target;
        Operation = string.IsNullOrWhiteSpace(operation) ? mcpToolName : operation;
        RiskLevel = string.IsNullOrWhiteSpace(riskLevel) ? "medium" : riskLevel;
        ReadOnly = readOnly;
        ConfirmationRequired = confirmationRequired;
        OwnerAgentIds = ownerAgentIds;
        WorkspaceScope = string.IsNullOrWhiteSpace(workspaceScope) ? "project" : workspaceScope;
        var permission = ReadOnly ? "只读" : ConfirmationRequired ? "需确认" : "可执行";
        Meta = $"{ServerLabel} · {ServerTransport} · MCP:{McpToolName} · {permission} · {UiDisplayText.Risk(RiskLevel)}";
        Detail = $"target={Target} · operation={Operation} · scope={WorkspaceScope} · agents={(OwnerAgentIds.Length == 0 ? "--" : string.Join(", ", OwnerAgentIds))}";
    }

    public string McpServer { get; }
    public string ServerLabel { get; }
    public string ServerTransport { get; }
    public string McpToolName { get; }
    public string InternalToolName { get; }
    public string Target { get; }
    public string Operation { get; }
    public string RiskLevel { get; }
    public bool ReadOnly { get; }
    public bool ConfirmationRequired { get; }
    public string[] OwnerAgentIds { get; }
    public string WorkspaceScope { get; }
    public string Meta { get; }
    public string Detail { get; }

    public static McpToolMappingViewModel FromJson(JsonElement mapping)
    {
        return new McpToolMappingViewModel(
            ReadString(mapping, "mcp_server", "--"),
            ReadString(mapping, "server_label"),
            ReadString(mapping, "server_transport"),
            ReadString(mapping, "mcp_tool_name"),
            ReadString(mapping, "internal_tool_name"),
            ReadString(mapping, "target", "mcp"),
            ReadString(mapping, "operation"),
            ReadString(mapping, "risk_level", "medium"),
            ReadBool(mapping, "read_only", false),
            ReadBool(mapping, "confirmation_required", true),
            ReadStringArray(mapping, "owner_agent_ids"),
            ReadString(mapping, "workspace_scope", "project"));
    }

    private static string ReadString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? fallback,
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => fallback,
        };
    }

    private static bool ReadBool(JsonElement element, string key, bool fallback)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String when bool.TryParse(value.GetString(), out var parsed) => parsed,
            _ => fallback,
        };
    }

    private static string[] ReadStringArray(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return Array.Empty<string>();
        }
        if (value.ValueKind == JsonValueKind.Array)
        {
            return value.EnumerateArray()
                .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.GetRawText())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Select(item => item!.Trim())
                .ToArray();
        }
        if (value.ValueKind == JsonValueKind.String)
        {
            return value.GetString()?
                .Split(new[] { ',', ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .ToArray() ?? Array.Empty<string>();
        }
        return Array.Empty<string>();
    }
}
