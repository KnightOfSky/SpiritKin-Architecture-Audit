using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal async Task LoadSyncAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/sync");
            RenderSync(doc.RootElement.GetProperty("sync"));
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SyncText.Text = $"同步加载失败：{ex.Message}";
        }
    }

    private void RenderSync(JsonElement sync)
    {
        var gitStatus = _workbenchControllerValue.RunGit("status --short --branch");
        var gitSummary = gitStatus.Success
            ? ComposerController.TrimStatusText(gitStatus.Output, 900)
            : $"Git 状态读取失败：{gitStatus.Output}";
        WorkbenchShell.ManagementPanels.SyncText.Text =
            $"修订：{ReadJsonString(sync, "revision")}{Environment.NewLine}" +
            $"更新者：{ReadJsonString(sync, "updated_by")}{Environment.NewLine}" +
            $"更新时间：{FormatTimeFromDouble(ReadJsonString(sync, "updated_at"))}{Environment.NewLine}" +
            $"会话/项目/任务/事件：{ReadJsonInt(sync, "session_count")}/{ReadJsonInt(sync, "project_count")}/{ReadJsonInt(sync, "task_count")}/{ReadJsonInt(sync, "event_count")}{Environment.NewLine}" +
            $"待确认：{(sync.TryGetProperty("pending", out var pending) && pending.ValueKind == JsonValueKind.Object ? "是" : "否")}{Environment.NewLine}" +
            $"Git：{Environment.NewLine}{gitSummary}";
        _syncClients.Clear();
        if (sync.TryGetProperty("clients", out var clients) && clients.ValueKind == JsonValueKind.Array)
        {
            foreach (var client in clients.EnumerateArray())
            {
                _syncClients.Add(new EventViewModel(ReadJsonString(client, "client_id"), $"事件：{ReadJsonInt(client, "event_count")}"));
            }
        }
        WorkbenchShell.ManagementPanels.SyncLastEventBox.Text = sync.TryGetProperty("last_event", out var lastEvent) && lastEvent.ValueKind != JsonValueKind.Null ? FormatJson(lastEvent) : "--";
    }

    internal async Task SyncActionAsync(string action)
    {
        try
        {
            using var doc = await PostJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/sync", new { action });
            await LoadStateAsync();
            RenderSync(doc.RootElement.GetProperty("sync"));
            WorkbenchShell.ManagementPanels.SyncText.Text = $"动作完成：{action}{Environment.NewLine}{WorkbenchShell.ManagementPanels.SyncText.Text}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.SyncText.Text = $"同步动作失败：{ex.Message}";
        }
    }

    internal async Task LoadLogsAsync(string logId = "")
    {
        try
        {
            await LoadActionLogAsync();
            var suffix = string.IsNullOrWhiteSpace(logId) ? "" : $"?log_id={Uri.EscapeDataString(logId)}";
            using var doc = await GetJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/logs{suffix}");
            var logs = doc.RootElement.GetProperty("logs");
            _rendering = true;
            _logs.Clear();
            _logPaths.Clear();
            foreach (var log in logs.GetProperty("logs").EnumerateArray())
            {
                var itemLogId = ReadJsonString(log, "log_id");
                var path = ReadJsonString(log, "path");
                if (!string.IsNullOrWhiteSpace(itemLogId) && !string.IsNullOrWhiteSpace(path))
                {
                    _logPaths[itemLogId] = path;
                }
                _logs.Add(new LogViewModel(
                    itemLogId,
                    Path.GetFileName(path),
                    $"{ReadJsonString(log, "log_id")} · {ReadJsonInt(log, "size_bytes")} bytes · errors {ReadJsonInt(log, "error_count")} · warnings {ReadJsonInt(log, "warning_count")}"));
            }
            if (logs.TryGetProperty("selected", out var selected) && selected.ValueKind == JsonValueKind.Object)
            {
                WorkbenchShell.ManagementPanels.LogsList.SelectedValue = ReadJsonString(selected, "log_id");
                WorkbenchShell.ManagementPanels.LogTailBox.Text = selected.TryGetProperty("tail", out var tail) && tail.ValueKind == JsonValueKind.Array
                    ? string.Join(Environment.NewLine, tail.EnumerateArray().Select(line => line.GetString() ?? ""))
                    : "";
            }
            else
            {
                WorkbenchShell.ManagementPanels.LogTailBox.Text = "暂无日志。";
            }
            _rendering = false;
        }
        catch (Exception ex)
        {
            _rendering = false;
            WorkbenchShell.ManagementPanels.LogTailBox.Text = $"日志加载失败：{ex.Message}";
        }
    }

    internal async Task LoadActionLogAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/action-log?limit=80");
            RenderActionLog(doc.RootElement.GetProperty("action_log"));
        }
        catch (Exception ex)
        {
            _actionLogEvents.Clear();
            _actionLogEvents.Add(new EventViewModel("统一动作日志", $"加载失败：{ex.Message}"));
            WorkbenchShell.ManagementPanels.UnifiedActionLogSummaryText.Text = "统一动作日志加载失败。";
        }
    }

    private void RenderActionLog(JsonElement actionLog)
    {
        _actionLogEvents.Clear();
        var eventCount = ReadJsonInt(actionLog, "event_count");
        var availableCount = ReadJsonInt(actionLog, "available_event_count");
        var sourceCounts = ReadJsonString(actionLog, "source_counts");
        WorkbenchShell.ManagementPanels.UnifiedActionLogSummaryText.Text =
            $"显示 {eventCount} / {availableCount} 条 · 来源 {sourceCounts}";
        if (!actionLog.TryGetProperty("events", out var events) || events.ValueKind != JsonValueKind.Array)
        {
            _actionLogEvents.Add(new EventViewModel("统一动作日志", "暂无动作记录。"));
            return;
        }
        foreach (var item in events.EnumerateArray())
        {
            var source = ReadJsonString(item, "source_label", ReadJsonString(item, "source", "Action"));
            var action = ReadJsonString(item, "action");
            var status = ReadJsonString(item, "status");
            var target = ReadJsonString(item, "target");
            var timestamp = FormatTime(ReadJsonDouble(item, "timestamp"));
            var message = ReadJsonString(item, "message");
            var actor = ReadJsonString(item, "actor");
            _actionLogEvents.Add(new EventViewModel(
                $"{source} · {status}",
                $"{timestamp} · {action} · {target}{Environment.NewLine}{actor} · {message}".Trim()));
        }
        if (_actionLogEvents.Count == 0)
        {
            _actionLogEvents.Add(new EventViewModel("统一动作日志", "暂无动作记录。"));
        }
    }

    internal async Task LoadDailyAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{_workspaceControllerValue.ApiBase()}/desktop/daily");
            var daily = doc.RootElement.GetProperty("daily");
            WorkbenchShell.ManagementPanels.DailySummaryText.Text =
                $"{ReadJsonString(daily, "date")}{Environment.NewLine}" +
                $"任务: 今日 {ReadJsonInt(daily, "today_task_count")} / 总计 {ReadJsonInt(daily, "task_total")} · {ReadJsonString(daily, "task_status_counts")}{Environment.NewLine}" +
                $"学习: 今日 {ReadJsonInt(daily, "today_learning_count")} · 训练集 {ReadJsonInt(daily, "learning_dataset_count")} 条{Environment.NewLine}" +
                $"服务: 运行 {ReadJsonInt(daily, "running_service_count")} · 停止 {ReadJsonInt(daily, "stopped_service_count")} · 错误日志 {ReadJsonInt(daily, "open_error_log_count")}";
            _dailyItems.Clear();
            if (daily.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in items.EnumerateArray())
                {
                    var type = ReadJsonString(item, "type");
                    var title = ReadJsonString(item, "title");
                    var target = ReadJsonString(item, "target");
                    if (string.IsNullOrWhiteSpace(target))
                    {
                        target = title;
                    }
                    var id = ReadJsonString(item, "id");
                    if (string.IsNullOrWhiteSpace(id))
                    {
                        id = $"{type}:{target}";
                    }
                    _dailyItems.Add(new ActionItemViewModel(
                        id,
                        $"{type} · {title}",
                        ReadJsonString(item, "status"),
                        type,
                        "",
                        target));
                }
            }
            WorkbenchShell.ManagementPanels.DailyActionText.Text = _dailyItems.Count == 0 ? "今日暂无待处理事项。" : "选择事项后可定位到对应管理页。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.DailySummaryText.Text = $"日报加载失败：{ex.Message}";
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "日报加载失败。";
        }
    }
}
