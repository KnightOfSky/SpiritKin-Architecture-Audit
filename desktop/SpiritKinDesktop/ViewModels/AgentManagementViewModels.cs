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

public sealed class ExternalAssistantViewModel
{
    public ExternalAssistantViewModel(string assistantId, string label, string kind, string command, string workingDirectory, string category, bool enabled, bool allowWrite, bool reviewOnly)
    {
        AssistantId = assistantId;
        Label = label;
        Kind = string.IsNullOrWhiteSpace(kind) ? "cli" : kind;
        Command = command;
        WorkingDirectory = workingDirectory;
        Category = string.IsNullOrWhiteSpace(category) ? "general" : category;
        Enabled = enabled;
        AllowWrite = allowWrite;
        ReviewOnly = reviewOnly;
        Type = $"{(enabled ? "启用" : "关闭")} · {label}";
        Meta = $"{UiDisplayText.Kind(Kind)} · {UiDisplayText.Domain(Category)} · {(string.IsNullOrWhiteSpace(command) ? "未配置命令" : "命令已配置")}";
    }

    public string AssistantId { get; }
    public string Label { get; }
    public string Kind { get; }
    public string Command { get; }
    public string WorkingDirectory { get; }
    public string Category { get; }
    public bool Enabled { get; }
    public bool AllowWrite { get; }
    public bool ReviewOnly { get; }
    public string Type { get; }
    public string Meta { get; }
}

public sealed class AgentAdapterViewModel
{
    public AgentAdapterViewModel(
        string adapterId,
        string label,
        string kind,
        string framework,
        string command,
        string module,
        string endpoint,
        string workingDirectory,
        bool enabled,
        bool allowWrite,
        bool reviewOnly,
        string[] capabilities,
        string[] ownerAgentIds,
        string healthStatus,
        string healthDetail,
        string notes)
    {
        AdapterId = adapterId;
        Label = string.IsNullOrWhiteSpace(label) ? adapterId : label;
        Kind = string.IsNullOrWhiteSpace(kind) ? "native" : kind;
        Framework = string.IsNullOrWhiteSpace(framework) ? "spiritkin_native" : framework;
        Command = command;
        Module = module;
        Endpoint = endpoint;
        WorkingDirectory = workingDirectory;
        Enabled = enabled;
        AllowWrite = allowWrite;
        ReviewOnly = reviewOnly;
        Capabilities = capabilities;
        OwnerAgentIds = ownerAgentIds;
        HealthStatus = string.IsNullOrWhiteSpace(healthStatus) ? "unknown" : healthStatus;
        HealthDetail = healthDetail;
        Notes = notes;
        var permission = reviewOnly ? "仅审查" : allowWrite ? "允许写" : "只读/受控";
        Type = $"{UiDisplayText.Status(Enabled ? "enabled" : "disabled")} · {Label}";
        Meta = $"{UiDisplayText.Kind(Kind)} · {UiDisplayText.Framework(Framework)} · {permission} · {UiDisplayText.Status(HealthStatus)}";
    }

    public string AdapterId { get; }
    public string Label { get; }
    public string Kind { get; }
    public string Framework { get; }
    public string Command { get; }
    public string Module { get; }
    public string Endpoint { get; }
    public string WorkingDirectory { get; }
    public bool Enabled { get; }
    public bool AllowWrite { get; }
    public bool ReviewOnly { get; }
    public string[] Capabilities { get; }
    public string[] OwnerAgentIds { get; }
    public string HealthStatus { get; }
    public string HealthDetail { get; }
    public string Notes { get; }
    public string Type { get; }
    public string Meta { get; }
}

public sealed class KnowledgeBaseViewModel
{
    public KnowledgeBaseViewModel(string knowledgeBaseId, string label, string ownerAgentId, string domain, string path, string sharedScope, bool enabled, string notes, int serverFileCount = 0, string serverIndexedAt = "")
    {
        KnowledgeBaseId = knowledgeBaseId;
        Label = string.IsNullOrWhiteSpace(label) ? knowledgeBaseId : label;
        OwnerAgentId = ownerAgentId;
        Domain = string.IsNullOrWhiteSpace(domain) ? "general" : domain;
        Path = path;
        SharedScope = string.IsNullOrWhiteSpace(sharedScope) ? "agent" : sharedScope;
        Enabled = enabled;
        Notes = notes;
        ServerFileCount = serverFileCount;
        ServerIndexedAt = serverIndexedAt;
        Type = $"{(enabled ? "启用" : "关闭")} · {Label}";
        Meta = $"{UiDisplayText.SharedScope(SharedScope)} · 归属 {(string.IsNullOrWhiteSpace(OwnerAgentId) ? UiDisplayText.Domain(Domain) : UiDisplayText.Domain(OwnerAgentId))} · {UiDisplayText.ShortTechnical(Path, 34)}";
    }

    public string KnowledgeBaseId { get; }
    public string Label { get; }
    public string OwnerAgentId { get; }
    public string Domain { get; }
    public string Path { get; }
    public string SharedScope { get; }
    public bool Enabled { get; }
    public string Notes { get; }
    public int ServerFileCount { get; }
    public string ServerIndexedAt { get; }
    public string Type { get; }
    public string Meta { get; }

    public KnowledgeBaseViewModel WithServerIndex(int fileCount, string indexedAt)
    {
        return new KnowledgeBaseViewModel(KnowledgeBaseId, Label, OwnerAgentId, Domain, Path, SharedScope, Enabled, Notes, fileCount, indexedAt);
    }
}

public sealed class KnowledgeSourceViewModel
{
    public KnowledgeSourceViewModel(
        string sourceId,
        string label,
        string kind,
        string path,
        string knowledgeBaseId,
        bool enabled,
        bool recursive,
        string[] ignorePatterns,
        string[] tagFilter,
        string notes,
        string resolvedPath,
        int fileCount,
        double lastSyncAt,
        string status)
    {
        SourceId = sourceId;
        Label = string.IsNullOrWhiteSpace(label) ? sourceId : label;
        Kind = string.IsNullOrWhiteSpace(kind) ? "folder" : kind;
        Path = path;
        KnowledgeBaseId = knowledgeBaseId;
        Enabled = enabled;
        Recursive = recursive;
        IgnorePatterns = ignorePatterns;
        TagFilter = tagFilter;
        Notes = notes;
        ResolvedPath = resolvedPath;
        FileCount = fileCount;
        LastSyncAt = lastSyncAt;
        Status = string.IsNullOrWhiteSpace(status) ? "unsynced" : status;
        Type = $"{UiDisplayText.Status(Status)} · {Label}";
        Meta = $"{UiDisplayText.Kind(Kind)} · KB {KnowledgeBaseId} · 文件 {FileCount} · {UiDisplayText.ShortTechnical(Path, 42)}";
    }

    public string SourceId { get; }
    public string Label { get; }
    public string Kind { get; }
    public string Path { get; }
    public string KnowledgeBaseId { get; }
    public bool Enabled { get; }
    public bool Recursive { get; }
    public string[] IgnorePatterns { get; }
    public string[] TagFilter { get; }
    public string Notes { get; }
    public string ResolvedPath { get; }
    public int FileCount { get; }
    public double LastSyncAt { get; }
    public string Status { get; }
    public string Type { get; }
    public string Meta { get; }
    public string StatusLine => $"外部源状态：{UiDisplayText.Status(Status)} · 文件 {FileCount} · 上次同步 {FormatSyncTime(LastSyncAt)}";

    public static KnowledgeSourceViewModel FromJson(JsonElement source)
    {
        var lastSyncAt = 0.0;
        if (source.TryGetProperty("last_sync", out var lastSync) && lastSync.ValueKind == JsonValueKind.Object)
        {
            lastSyncAt = ReadDouble(lastSync, "updated_at");
        }
        return new KnowledgeSourceViewModel(
            ReadString(source, "source_id"),
            ReadString(source, "label"),
            ReadString(source, "kind", "folder"),
            ReadString(source, "path"),
            ReadString(source, "knowledge_base_id"),
            ReadBool(source, "enabled", true),
            ReadBool(source, "recursive", true),
            ReadStringArray(source, "ignore_patterns"),
            ReadStringArray(source, "tag_filter"),
            ReadString(source, "notes"),
            ReadString(source, "resolved_path"),
            ReadInt(source, "file_count"),
            lastSyncAt,
            ReadString(source, "status", "unsynced"));
    }

    private static string FormatSyncTime(double timestamp)
    {
        if (timestamp <= 0)
        {
            return "未同步";
        }
        try
        {
            return DateTimeOffset.FromUnixTimeSeconds((long)timestamp).LocalDateTime.ToString("yyyy-MM-dd HH:mm", CultureInfo.CurrentCulture);
        }
        catch
        {
            return "--";
        }
    }

    private static string ReadString(JsonElement element, string key, string fallback = "")
    {
        if (!element.TryGetProperty(key, out var value))
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

    private static int ReadInt(JsonElement element, string key)
    {
        return int.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
    }

    private static double ReadDouble(JsonElement element, string key)
    {
        return double.TryParse(ReadString(element, key), NumberStyles.Any, CultureInfo.InvariantCulture, out var parsed) ? parsed : 0;
    }

    private static bool ReadBool(JsonElement element, string key, bool fallback)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
            _ => fallback,
        };
    }

    private static string[] ReadStringArray(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return value.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }
}

public sealed class RouteProfileViewModel
{
    public RouteProfileViewModel(string profileId, string label, string strategy, bool enabled, string membersJson, string notes)
    {
        ProfileId = profileId;
        Label = label;
        Strategy = string.IsNullOrWhiteSpace(strategy) ? "primary_with_specialists" : strategy;
        Enabled = enabled;
        MembersJson = string.IsNullOrWhiteSpace(membersJson) ? "[]" : membersJson;
        Notes = notes;
        Type = $"{(enabled ? "启用" : "关闭")} · {label}";
        Meta = $"{UiDisplayText.RouteStrategy(Strategy)} · 成员 {CountMembers(MembersJson)}";
    }

    public string ProfileId { get; }
    public string Label { get; }
    public string Strategy { get; }
    public bool Enabled { get; }
    public string MembersJson { get; }
    public string Notes { get; }
    public string Type { get; }
    public string Meta { get; }

    private static int CountMembers(string membersJson)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(membersJson) ? "[]" : membersJson);
            return doc.RootElement.ValueKind == JsonValueKind.Array ? doc.RootElement.GetArrayLength() : 0;
        }
        catch
        {
            return 0;
        }
    }
}

public sealed class RemoteTargetViewModel
{
    public RemoteTargetViewModel(string targetId, string label, string baseUrl, bool enabled, bool tokenSet, string[] capabilities)
    {
        TargetId = targetId;
        Label = label;
        BaseUrl = baseUrl;
        Enabled = enabled;
        TokenSet = tokenSet;
        Capabilities = capabilities;
        Type = $"{(enabled ? "启用" : "关闭")} · {label}";
        Meta = $"{(string.IsNullOrWhiteSpace(baseUrl) ? "未配置地址" : UiDisplayText.ShortTechnical(baseUrl, 34))} · Token {(tokenSet ? "已设置" : "未设置")} · 能力 {Capabilities.Length}";
    }

    public string TargetId { get; }
    public string Label { get; }
    public string BaseUrl { get; }
    public bool Enabled { get; }
    public bool TokenSet { get; }
    public string[] Capabilities { get; }
    public string Type { get; }
    public string Meta { get; }
}

public sealed class AgentViewModel
{
    public AgentViewModel(string agentId, string label, string domain, string role, string provider, string model, string modelId, string framework, string adapter, bool enabled, int priority, string[] capabilities, string[] allowedAssistantIds, string knowledgeBaseId, string knowledgeBasePath, string brainProfile, string notes)
    {
        AgentId = agentId;
        Label = label;
        Domain = domain;
        Role = role;
        Provider = provider;
        Model = model;
        ModelId = modelId;
        Framework = string.IsNullOrWhiteSpace(framework) ? "native" : framework;
        Adapter = string.IsNullOrWhiteSpace(adapter) ? "coordinator_router" : adapter;
        Enabled = enabled;
        Priority = priority;
        Capabilities = capabilities;
        AllowedAssistantIds = allowedAssistantIds;
        KnowledgeBaseId = knowledgeBaseId;
        KnowledgeBasePath = knowledgeBasePath;
        BrainProfile = string.IsNullOrWhiteSpace(brainProfile) ? $"{agentId}_brain" : brainProfile;
        Notes = notes;
        Status = enabled ? "启用" : "关闭";
        StatusLabel = Status;
        StatusBrush = new SolidColorBrush(enabled ? Color.FromRgb(22, 163, 74) : Color.FromRgb(148, 163, 184));
        Meta = $"{UiDisplayText.Domain(domain)} · {UiDisplayText.Role(role)} · {UiDisplayText.Framework(Framework)} · {UiDisplayText.ShortTechnical(Adapter, 24)} · 优先级 {priority}";
    }

    public string AgentId { get; }
    public string Label { get; }
    public string Domain { get; }
    public string Role { get; }
    public string Provider { get; }
    public string Model { get; }
    public string ModelId { get; }
    public string Framework { get; }
    public string Adapter { get; }
    public bool Enabled { get; }
    public int Priority { get; }
    public string[] Capabilities { get; }
    public string[] AllowedAssistantIds { get; }
    public string KnowledgeBaseId { get; }
    public string KnowledgeBasePath { get; }
    public string BrainProfile { get; }
    public string Notes { get; }
    public string Status { get; }
    public string StatusLabel { get; }
    public string Meta { get; }
    public Brush StatusBrush { get; }

    public AgentViewModel WithKnowledgeBase(string knowledgeBaseId, string knowledgeBasePath)
    {
        return new AgentViewModel(
            AgentId,
            Label,
            Domain,
            Role,
            Provider,
            Model,
            ModelId,
            Framework,
            Adapter,
            Enabled,
            Priority,
            Capabilities,
            AllowedAssistantIds,
            knowledgeBaseId,
            knowledgeBasePath,
            BrainProfile,
            Notes);
    }
}
