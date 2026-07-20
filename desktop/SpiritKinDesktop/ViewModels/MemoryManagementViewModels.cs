using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

public sealed class MemoryConflictViewModel
{
    private MemoryConflictViewModel(
        string conflictId,
        string status,
        double confidence,
        string reason,
        string createdAt,
        string sourceEntryId,
        string sourceContent,
        string sourceEvidence,
        string sourceProvenance,
        string targetEntryId,
        string targetContent,
        string targetEvidence,
        string targetProvenance,
        string resolution,
        string resolutionReason)
    {
        ConflictId = conflictId;
        Status = status;
        Confidence = confidence;
        Reason = reason;
        CreatedAt = createdAt;
        SourceEntryId = sourceEntryId;
        SourceContent = sourceContent;
        SourceEvidence = sourceEvidence;
        SourceProvenance = sourceProvenance;
        TargetEntryId = targetEntryId;
        TargetContent = targetContent;
        TargetEvidence = targetEvidence;
        TargetProvenance = targetProvenance;
        Resolution = resolution;
        ResolutionReason = resolutionReason;
    }

    public string ConflictId { get; }
    public string Status { get; }
    public double Confidence { get; }
    public string Reason { get; }
    public string CreatedAt { get; }
    public string SourceEntryId { get; }
    public string SourceContent { get; }
    public string SourceEvidence { get; }
    public string SourceProvenance { get; }
    public string TargetEntryId { get; }
    public string TargetContent { get; }
    public string TargetEvidence { get; }
    public string TargetProvenance { get; }
    public string Resolution { get; }
    public string ResolutionReason { get; }
    public bool IsOpen => Status is "pending_review" or "clarification_needed";
    public string StatusLabel => Status switch
    {
        "pending_review" => "待复核",
        "clarification_needed" => "待澄清",
        "resolved" => "已处置",
        "dismissed" => "已驳回",
        _ => Status,
    };
    public string Title => $"{Preview(SourceContent)}  /  {Preview(TargetContent)}";
    public string Meta => $"{StatusLabel} · 置信度 {Confidence:P0} · {ConflictId}";

    internal static MemoryConflictViewModel FromJson(JsonElement item)
    {
        var source = TryObject(item, "source_memory");
        var target = TryObject(item, "target_memory");
        var createdAt = ReadDouble(item, "created_at");
        return new MemoryConflictViewModel(
            JsonResponseHelpers.ReadJsonString(item, "conflict_id"),
            JsonResponseHelpers.ReadJsonString(item, "status", "pending_review"),
            ReadDouble(item, "confidence"),
            JsonResponseHelpers.ReadJsonString(item, "reason"),
            createdAt > 0 ? DateTimeOffset.FromUnixTimeSeconds((long)createdAt).LocalDateTime.ToString("yyyy-MM-dd HH:mm") : "--",
            JsonResponseHelpers.ReadJsonString(item, "source_entry_id"),
            ReadObjectString(source, "content", "记忆原文不可用"),
            EvidenceText(source),
            ProvenanceText(source),
            JsonResponseHelpers.ReadJsonString(item, "target_entry_id"),
            ReadObjectString(target, "content", "记忆原文不可用"),
            EvidenceText(target),
            ProvenanceText(target),
            JsonResponseHelpers.ReadJsonString(item, "resolution"),
            JsonResponseHelpers.ReadJsonString(item, "resolution_reason"));
    }

    private static JsonElement TryObject(JsonElement element, string key)
    {
        return element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Object ? value : default;
    }

    private static string EvidenceText(JsonElement memory)
    {
        if (memory.ValueKind != JsonValueKind.Object ||
            !memory.TryGetProperty("metadata", out var metadata) ||
            metadata.ValueKind != JsonValueKind.Object ||
            !metadata.TryGetProperty("evidence_quotes", out var evidence) ||
            evidence.ValueKind != JsonValueKind.Array)
        {
            return "无可回看的用户原话";
        }
        var lines = evidence.EnumerateArray()
            .Where(value => value.ValueKind == JsonValueKind.String)
            .Select(value => value.GetString()?.Trim() ?? "")
            .Where(value => !string.IsNullOrWhiteSpace(value))
            .Take(8)
            .Select(value => $"“{value}”")
            .ToArray();
        return lines.Length == 0 ? "无可回看的用户原话" : string.Join(Environment.NewLine, lines);
    }

    private static string ProvenanceText(JsonElement memory)
    {
        if (memory.ValueKind != JsonValueKind.Object ||
            !memory.TryGetProperty("metadata", out var metadata) ||
            metadata.ValueKind != JsonValueKind.Object)
        {
            return "来源未记录";
        }
        var source = JsonResponseHelpers.ReadJsonString(metadata, "source", "来源未记录");
        var attribution = JsonResponseHelpers.ReadJsonString(metadata, "attribution", "归属未记录");
        return $"来源：{source} · 归属：{attribution}";
    }

    private static string ReadObjectString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        var value = JsonResponseHelpers.ReadJsonString(element, key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value;
    }

    private static double ReadDouble(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number)
            ? number
            : double.TryParse(JsonResponseHelpers.ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    private static string Preview(string value)
    {
        var compact = string.Join(" ", (value ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return compact.Length <= 24 ? compact : compact[..24] + "…";
    }
}
