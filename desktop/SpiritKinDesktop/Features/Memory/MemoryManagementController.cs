using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed class MemoryManagementController
{
    private readonly ManagementPanelsView _panel;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly List<MemoryConflictViewModel> _allConflicts = new();

    public ObservableCollection<MemoryConflictViewModel> Conflicts { get; } = new();
    public ObservableCollection<EventViewModel> AuditFindings { get; } = new();
    public bool HasLoaded { get; private set; }

    public MemoryManagementController(
        ManagementPanelsView panel,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase)
    {
        _panel = panel;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _panel.MemoryConflictsList.ItemsSource = Conflicts;
        _panel.MemoryAuditList.ItemsSource = AuditFindings;
    }

    public async Task LoadAsync()
    {
        SetBusy(true, "正在读取长期记忆审计…");
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/memory");
            Render(doc.RootElement);
            HasLoaded = true;
        }
        catch (Exception ex)
        {
            _panel.MemorySummaryText.Text = $"记忆复核加载失败：{ex.Message}";
            _panel.MemoryResolutionStatusText.Text = "无法读取记忆管理端点。";
            _panel.MemoryResolutionStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyDangerBrush");
        }
        finally
        {
            SetBusy(false);
        }
    }

    public void ApplyFilter()
    {
        var selectedId = (_panel.MemoryConflictsList.SelectedItem as MemoryConflictViewModel)?.ConflictId;
        var filter = SelectedTag(_panel.MemoryConflictFilterBox, "pending");
        var visible = filter switch
        {
            "pending" => _allConflicts.Where(item => item.IsOpen),
            "resolved" => _allConflicts.Where(item => !item.IsOpen),
            _ => _allConflicts,
        };
        Conflicts.Clear();
        foreach (var item in visible)
        {
            Conflicts.Add(item);
        }
        _panel.MemoryEmptyText.Visibility = Conflicts.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        _panel.MemoryConflictsList.SelectedItem = Conflicts.FirstOrDefault(item => item.ConflictId == selectedId) ?? Conflicts.FirstOrDefault();
        RenderSelected();
    }

    public void RenderSelected()
    {
        if (_panel.MemoryConflictsList.SelectedItem is not MemoryConflictViewModel selected)
        {
            _panel.MemoryNewIdText.Text = "新记忆";
            _panel.MemoryOldIdText.Text = "已有记忆";
            _panel.MemoryNewContentBox.Text = "";
            _panel.MemoryOldContentBox.Text = "";
            _panel.MemoryNewEvidenceBox.Text = "";
            _panel.MemoryNewProvenanceBox.Text = "";
            _panel.MemoryOldEvidenceBox.Text = "";
            _panel.MemoryOldProvenanceBox.Text = "";
            _panel.MemoryConflictReasonText.Text = "选择一条冲突后查看检测依据。";
            _panel.ApplyMemoryResolutionButton.IsEnabled = false;
            return;
        }

        _panel.MemoryNewIdText.Text = $"新记忆 · {selected.SourceEntryId}";
        _panel.MemoryOldIdText.Text = $"已有记忆 · {selected.TargetEntryId}";
        _panel.MemoryNewContentBox.Text = selected.SourceContent;
        _panel.MemoryOldContentBox.Text = selected.TargetContent;
        _panel.MemoryNewEvidenceBox.Text = selected.SourceEvidence;
        _panel.MemoryNewProvenanceBox.Text = selected.SourceProvenance;
        _panel.MemoryOldEvidenceBox.Text = selected.TargetEvidence;
        _panel.MemoryOldProvenanceBox.Text = selected.TargetProvenance;
        _panel.MemoryConflictReasonText.Text = $"{selected.Reason} · 检测 {selected.CreatedAt}";
        _panel.MemoryResolutionReasonBox.Text = selected.ResolutionReason;
        _panel.ApplyMemoryResolutionButton.IsEnabled = selected.IsOpen;
        _panel.MemoryResolutionBox.IsEnabled = selected.IsOpen;
        _panel.MemoryResolutionReasonBox.IsEnabled = selected.IsOpen;
        _panel.MemoryResolutionStatusText.Text = selected.IsOpen
            ? "等待人工处置"
            : $"{selected.StatusLabel} · {ResolutionLabel(selected.Resolution)}";
        _panel.MemoryResolutionStatusText.SetResourceReference(
            TextBlock.ForegroundProperty,
            selected.IsOpen ? "FantasyWarningBrush" : "FantasySuccessBrush");
    }

    public async Task ResolveSelectedAsync()
    {
        if (_panel.MemoryConflictsList.SelectedItem is not MemoryConflictViewModel selected || !selected.IsOpen)
        {
            _panel.MemoryResolutionStatusText.Text = "请先选择一条待复核冲突。";
            return;
        }
        var resolution = SelectedTag(_panel.MemoryResolutionBox, "clarification_needed");
        var reason = _panel.MemoryResolutionReasonBox.Text.Trim();
        if (ResolutionRequiresReason(resolution) && string.IsNullOrWhiteSpace(reason))
        {
            _panel.MemoryResolutionReasonBox.SetResourceReference(Control.BorderBrushProperty, "FantasyDangerBrush");
            _panel.MemoryResolutionStatusText.Text = "采用新记忆或已有记忆时必须填写处置理由。";
            _panel.MemoryResolutionStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyDangerBrush");
            return;
        }

        _panel.MemoryResolutionReasonBox.SetResourceReference(Control.BorderBrushProperty, "FantasyBorderBrush");
        SetBusy(true, "正在保存处置…");
        try
        {
            using var doc = await _postJsonAsync(
                $"{_apiBase()}/desktop/memory",
                new Dictionary<string, object?>
                {
                    ["action"] = "resolve_conflict",
                    ["conflict_id"] = selected.ConflictId,
                    ["resolution"] = resolution,
                    ["reason"] = reason,
                });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "记忆冲突处置失败");
            Render(doc.RootElement);
            _panel.MemoryResolutionStatusText.Text = "处置已保存，召回状态已更新。";
            _panel.MemoryResolutionStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasySuccessBrush");
        }
        catch (Exception ex)
        {
            _panel.MemoryResolutionStatusText.Text = $"处置失败：{ex.Message}";
            _panel.MemoryResolutionStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyDangerBrush");
        }
        finally
        {
            SetBusy(false);
        }
    }

    internal void Render(JsonElement root)
    {
        if (!root.TryGetProperty("memory_management", out var memory) || memory.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidOperationException("memory_management payload missing");
        }

        var selectedId = (_panel.MemoryConflictsList.SelectedItem as MemoryConflictViewModel)?.ConflictId;
        _allConflicts.Clear();
        if (memory.TryGetProperty("conflicts", out var conflicts) && conflicts.ValueKind == JsonValueKind.Array)
        {
            _allConflicts.AddRange(conflicts.EnumerateArray().Select(MemoryConflictViewModel.FromJson));
        }

        AuditFindings.Clear();
        if (memory.TryGetProperty("audit", out var audit) && audit.ValueKind == JsonValueKind.Object &&
            audit.TryGetProperty("findings", out var findings) && findings.ValueKind == JsonValueKind.Array)
        {
            foreach (var finding in findings.EnumerateArray().Take(40))
            {
                var severity = JsonResponseHelpers.ReadJsonString(finding, "severity", "info");
                var code = JsonResponseHelpers.ReadJsonString(finding, "code", "memory_audit");
                var message = JsonResponseHelpers.ReadJsonString(finding, "message");
                var suggestion = JsonResponseHelpers.ReadJsonString(finding, "suggestion");
                AuditFindings.Add(new EventViewModel($"{SeverityLabel(severity)} · {code}", $"{message}{Environment.NewLine}{suggestion}".Trim()));
            }
        }
        if (AuditFindings.Count == 0)
        {
            AuditFindings.Add(new EventViewModel("记忆审计", "未发现需要处理的问题。"));
        }

        _panel.MemorySummaryText.Text = BuildSummary(memory);
        ApplyFilter();
        if (!string.IsNullOrWhiteSpace(selectedId))
        {
            _panel.MemoryConflictsList.SelectedItem = Conflicts.FirstOrDefault(item => item.ConflictId == selectedId) ?? Conflicts.FirstOrDefault();
        }
        RenderSelected();
    }

    internal static bool ResolutionRequiresReason(string resolution) => resolution is "prefer_new" or "prefer_existing";

    internal static string BuildSummary(JsonElement memory)
    {
        var stats = memory.TryGetProperty("stats", out var statsValue) && statsValue.ValueKind == JsonValueKind.Object ? statsValue : default;
        var audit = memory.TryGetProperty("audit", out var auditValue) && auditValue.ValueKind == JsonValueKind.Object ? auditValue : default;
        var severity = audit.ValueKind == JsonValueKind.Object && audit.TryGetProperty("by_severity", out var severityValue) && severityValue.ValueKind == JsonValueKind.Object ? severityValue : default;
        return $"长期记忆 {ReadInt(stats, "total")} · 待复核 {ReadInt(stats, "pending_conflict_count")} · 冲突历史 {ReadInt(stats, "conflict_count")} · 审计错误/警告 {ReadInt(severity, "error")}/{ReadInt(severity, "warning")}";
    }

    private void SetBusy(bool busy, string status = "")
    {
        _panel.RefreshMemoryButton.IsEnabled = !busy;
        _panel.ApplyMemoryResolutionButton.IsEnabled = !busy && (_panel.MemoryConflictsList.SelectedItem as MemoryConflictViewModel)?.IsOpen == true;
        if (!string.IsNullOrWhiteSpace(status))
        {
            _panel.MemoryResolutionStatusText.Text = status;
            _panel.MemoryResolutionStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyMutedBrush");
        }
    }

    private static int ReadInt(JsonElement element, string key)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        return value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number) ? number : 0;
    }

    private static string SelectedTag(ComboBox box, string fallback)
    {
        return box.SelectedItem is ComboBoxItem item && item.Tag is string tag && !string.IsNullOrWhiteSpace(tag) ? tag : fallback;
    }

    private static string ResolutionLabel(string resolution) => resolution switch
    {
        "prefer_new" => "已采用新记忆",
        "prefer_existing" => "已保留已有记忆",
        "context_difference" => "按上下文并存",
        "dismiss" => "已驳回冲突",
        "clarification_needed" => "等待用户澄清",
        _ => resolution,
    };

    private static string SeverityLabel(string severity) => severity switch
    {
        "error" => "错误",
        "warning" => "警告",
        _ => "提示",
    };
}
