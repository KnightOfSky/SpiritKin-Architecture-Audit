using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class ContextController
{
    internal async Task LoadContextAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceController.ApiBase()}/desktop/context");
            var context = doc.RootElement.GetProperty("context");
            var policy = context.GetProperty("policy");
            SetComboText(WorkbenchShell.ManagementPanels.ContextModeBox, ReadJsonString(policy, "mode"));
            WorkbenchShell.ManagementPanels.MaxRecentMessagesBox.Text = ReadJsonInt(policy, "max_recent_messages").ToString();
            WorkbenchShell.ManagementPanels.IncludeProjectDocsBox.IsChecked = ReadJsonBool(policy, "include_project_docs", true);
            WorkbenchShell.ManagementPanels.IncludeRecentEventsBox.IsChecked = ReadJsonBool(policy, "include_recent_events", true);
            WorkbenchShell.ManagementPanels.IncludeLearningRecordsBox.IsChecked = ReadJsonBool(policy, "include_learning_records", true);
            WorkbenchShell.ManagementPanels.PinnedContextBox.Text = policy.TryGetProperty("pinned_context", out var pinned) && pinned.ValueKind == JsonValueKind.Array
                ? string.Join(Environment.NewLine, pinned.EnumerateArray().Select(item => item.GetString()))
                : "";
            var active = context.GetProperty("active_session");
            var project = context.GetProperty("project_summary");
            WorkbenchShell.ManagementPanels.ContextSummaryText.Text = $"会话：{ReadJsonString(active, "title")} · 消息 {ReadJsonInt(active, "message_count")}{Environment.NewLine}策略：{ReadJsonString(project, "strategy")}";
            ContextSuggestions.Clear();
            foreach (var suggestion in context.GetProperty("suggestions").EnumerateArray())
            {
                var command = ReadJsonString(suggestion, "command");
                var detail = ReadJsonString(suggestion, "detail");
                ContextSuggestions.Add(new ActionItemViewModel(
                    ReadJsonString(suggestion, "suggestion_id"),
                    $"{ReadJsonString(suggestion, "priority")} · {ReadJsonString(suggestion, "title")}",
                    $"{detail}{Environment.NewLine}{command}".Trim(),
                    "context_suggestion",
                    command,
                    detail));
            }
            WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = ContextSuggestions.Count == 0 ? "暂无项目优化建议。" : "选择建议后可复制命令或详情。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ContextSummaryText.Text = $"上下文模块加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = "上下文模块加载失败。";
        }
    }

    internal async Task SaveContextPolicyAsync()
    {
        var maxRecent = int.TryParse(WorkbenchShell.ManagementPanels.MaxRecentMessagesBox.Text.Trim(), out var parsed) ? parsed : 12;
        var payload = new
        {
            policy = new
            {
                mode = ComboText(WorkbenchShell.ManagementPanels.ContextModeBox),
                max_recent_messages = maxRecent,
                include_project_docs = WorkbenchShell.ManagementPanels.IncludeProjectDocsBox.IsChecked == true,
                include_recent_events = WorkbenchShell.ManagementPanels.IncludeRecentEventsBox.IsChecked == true,
                include_learning_records = WorkbenchShell.ManagementPanels.IncludeLearningRecordsBox.IsChecked == true,
                pinned_context = WorkbenchShell.ManagementPanels.PinnedContextBox.Text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
            }
        };
        using var _ = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/context", payload);
        await LoadContextAsync();
    }

    internal async Task LoadProjectOverviewAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceController.ApiBase()}/desktop/project-overview");
            RenderProjectOverviewState(doc.RootElement.GetProperty("project_overview"));
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ProjectOverviewSummaryText.Text = $"项目总览加载失败：{ex.Message}";
        }
    }

    internal async Task RefreshProjectOverviewAsync()
    {
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/project-overview", new { action = "refresh", propose = true, author = "wpf_desktop" });
        RenderProjectOverviewState(doc.RootElement.GetProperty("project_overview"));
    }

    internal async Task SaveProjectOverviewAsync()
    {
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/project-overview", new { action = "save", markdown = WorkbenchShell.ManagementPanels.ProjectOverviewBox.Text, propose = true, author = "wpf_desktop", note = "Desktop manual edit" });
        RenderProjectOverviewState(doc.RootElement.GetProperty("project_overview"));
    }

    internal async Task ReviewSelectedOverviewChangeAsync(string action)
    {
        if (WorkbenchShell.ManagementPanels.ProjectOverviewChangesList.SelectedItem is not ChangeViewModel selected)
        {
            return;
        }
        using var doc = await PostJsonAsync($"{_workspaceController.ApiBase()}/desktop/project-overview", new { action, change_id = selected.Id, reviewer = "human_desktop" });
        RenderProjectOverviewState(doc.RootElement.GetProperty("project_overview"));
    }

    internal void ProjectOverviewChangesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (WorkbenchShell.ManagementPanels.ProjectOverviewChangesList.SelectedItem is ChangeViewModel selected)
        {
            WorkbenchShell.ManagementPanels.ProjectOverviewDiffBox.Text = selected.Diff;
            WorkbenchShell.ManagementPanels.ProjectOverviewBox.Text = selected.ProposedMarkdown;
        }
    }

    private void RenderProjectOverviewState(JsonElement state)
    {
        var overview = state.GetProperty("overview");
        RenderProjectOverview(overview);
        OverviewChanges.Clear();
        if (state.TryGetProperty("changes", out var changes) && changes.ValueKind == JsonValueKind.Array)
        {
            foreach (var change in changes.EnumerateArray().Reverse())
            {
                OverviewChanges.Add(new ChangeViewModel(
                    ReadJsonString(change, "change_id"),
                    $"{ReadJsonString(change, "status")} · {ReadJsonString(change, "note")}",
                    $"{ReadJsonString(change, "author")} · {FormatTimeFromDouble(ReadJsonString(change, "created_at"))}",
                    ReadJsonString(change, "diff"),
                    ReadJsonString(change, "proposed_markdown")));
            }
        }
        WorkbenchShell.ManagementPanels.ProjectOverviewDiffBox.Text = OverviewChanges.FirstOrDefault()?.Diff ?? "";
    }

    private void RenderProjectOverview(JsonElement overview)
    {
        WorkbenchShell.ManagementPanels.ProjectOverviewBox.Text = ReadJsonString(overview, "markdown");
        var summary = overview.GetProperty("summary");
        WorkbenchShell.ManagementPanels.ProjectOverviewSummaryText.Text = $"路径：{ReadJsonString(overview, "path")}{Environment.NewLine}项目：{ReadJsonInt(summary, "projects")} · 任务：{ReadJsonInt(summary, "tasks")} · 云模型：{ReadJsonString(summary, "cloud_model")}";
    }

    internal void OpenProjectOverviewFile()
    {
        var pathLine = WorkbenchShell.ManagementPanels.ProjectOverviewSummaryText.Text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None)
            .FirstOrDefault(line => line.StartsWith("路径：", StringComparison.Ordinal) || line.StartsWith("path: ", StringComparison.OrdinalIgnoreCase));
        var path = pathLine?
            .Replace("路径：", "", StringComparison.Ordinal)
            .Replace("path: ", "", StringComparison.OrdinalIgnoreCase)
            .Trim();
        if (!string.IsNullOrWhiteSpace(path) && File.Exists(path))
        {
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }
    }
}
