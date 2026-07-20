using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed class GovernanceController
{
    private readonly ManagementPanelsView _panels;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly Func<string, string, bool> _confirmDestructiveAction;

    internal ObservableCollection<ToolAuthorizationItemViewModel> ToolAuthorizations { get; } = new();
    internal ObservableCollection<ScheduledIntentItemViewModel> ScheduledIntents { get; } = new();

    internal GovernanceController(
        ManagementPanelsView panels,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        Func<string, string, bool> confirmDestructiveAction)
    {
        _panels = panels;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _confirmDestructiveAction = confirmDestructiveAction;
        _panels.ToolAuthorizationList.ItemsSource = ToolAuthorizations;
        _panels.ScheduledIntentsList.ItemsSource = ScheduledIntents;
        NewScheduledIntent();
    }

    internal async Task LoadAsync()
    {
        var failures = new List<string>();
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/tool-authorization");
            RenderToolAuthorization(doc.RootElement.GetProperty("tool_authorization"));
        }
        catch (Exception ex)
        {
            ToolAuthorizations.Clear();
            failures.Add($"工具授权：{ex.Message}");
        }

        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/scheduler/intents?include_finished=1");
            RenderScheduler(doc.RootElement.GetProperty("scheduler"));
        }
        catch (Exception ex)
        {
            ScheduledIntents.Clear();
            failures.Add($"定时任务：{ex.Message}");
        }

        _panels.GovernanceSummaryText.Text = failures.Count == 0
            ? $"工具 {ToolAuthorizations.Count} · 定时任务 {ScheduledIntents.Count}"
            : string.Join(Environment.NewLine, failures);
    }

    internal void RenderSelectedToolAuthorization()
    {
        if (_panels.ToolAuthorizationList.SelectedItem is not ToolAuthorizationItemViewModel item)
        {
            _panels.ToolAuthorizationIdBox.Text = "";
            _panels.ToolAuthorizationEnabledBox.IsChecked = false;
            _panels.SaveToolAuthorizationButton.IsEnabled = false;
            return;
        }
        _panels.ToolAuthorizationIdBox.Text = item.ToolId;
        _panels.ToolAuthorizationEnabledBox.IsChecked = item.Enabled;
        SetComboTag(_panels.ToolAuthorizationRiskBox, item.Risk);
        SetComboTag(_panels.ToolAuthorizationConfirmationBox, item.ConfirmationPolicy);
        _panels.SaveToolAuthorizationButton.IsEnabled = true;
        _panels.ToolAuthorizationActionText.Text = $"{item.Source} · 更新于 {item.UpdatedAtLabel}";
    }

    internal async Task SaveToolAuthorizationAsync()
    {
        if (_panels.ToolAuthorizationList.SelectedItem is not ToolAuthorizationItemViewModel item)
        {
            _panels.ToolAuthorizationActionText.Text = "请先选择工具。";
            return;
        }
        try
        {
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/tool-authorization", new
            {
                action = "update",
                tool_id = item.ToolId,
                enabled = _panels.ToolAuthorizationEnabledBox.IsChecked == true,
                risk = ComboTag(_panels.ToolAuthorizationRiskBox),
                confirmation_policy = ComboTag(_panels.ToolAuthorizationConfirmationBox),
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "保存工具授权");
            RenderToolAuthorization(doc.RootElement.GetProperty("tool_authorization"), item.ToolId);
            _panels.ToolAuthorizationActionText.Text = "工具授权已保存。";
        }
        catch (Exception ex)
        {
            _panels.ToolAuthorizationActionText.Text = $"保存失败：{ex.Message}";
        }
    }

    internal void RenderSelectedScheduledIntent()
    {
        if (_panels.ScheduledIntentsList.SelectedItem is not ScheduledIntentItemViewModel item)
        {
            return;
        }
        _panels.ScheduledIntentTextBox.Text = item.Text;
        SetComboTag(_panels.ScheduledIntentTypeBox, item.IntentType);
        SetComboTag(_panels.ScheduledTriggerTypeBox, item.TriggerType);
        _panels.ScheduledTimezoneBox.Text = item.Timezone;
        _panels.ScheduledRunAtBox.Text = item.RunAt;
        _panels.ScheduledIntervalBox.Text = item.IntervalSeconds <= 0 ? "3600" : item.IntervalSeconds.ToString(CultureInfo.InvariantCulture);
        _panels.ScheduledCronBox.Text = string.IsNullOrWhiteSpace(item.Cron) ? "0 9 * * *" : item.Cron;
        _panels.ScheduledActionPromptBox.Text = item.ActionPrompt;
        _panels.SchedulerActionText.Text = $"{UiDisplayText.Status(item.Status)} · 下次 {item.NextRunLabel}";
        SyncScheduledTriggerFields();
        SyncScheduledActionButtons();
    }

    internal void NewScheduledIntent()
    {
        _panels.ScheduledIntentsList.SelectedItem = null;
        _panels.ScheduledIntentTextBox.Text = "";
        SetComboTag(_panels.ScheduledIntentTypeBox, "reminder");
        SetComboTag(_panels.ScheduledTriggerTypeBox, "date");
        _panels.ScheduledTimezoneBox.Text = "Asia/Shanghai";
        _panels.ScheduledRunAtBox.Text = DateTimeOffset.Now.AddMinutes(5).ToString("yyyy-MM-ddTHH:mm:sszzz", CultureInfo.InvariantCulture);
        _panels.ScheduledIntervalBox.Text = "3600";
        _panels.ScheduledCronBox.Text = "0 9 * * *";
        _panels.ScheduledActionPromptBox.Text = "";
        _panels.SchedulerActionText.Text = "新建定时任务";
        SyncScheduledTriggerFields();
        SyncScheduledActionButtons();
        _panels.ScheduledIntentTextBox.Focus();
    }

    internal void SyncScheduledTriggerFields()
    {
        var trigger = ComboTag(_panels.ScheduledTriggerTypeBox);
        _panels.ScheduledRunAtBox.IsEnabled = trigger == "date";
        _panels.ScheduledIntervalBox.IsEnabled = trigger == "interval";
        _panels.ScheduledCronBox.IsEnabled = trigger == "cron";
    }

    internal async Task SaveScheduledIntentAsync()
    {
        try
        {
            var values = BuildScheduledIntentValues(
                _panels.ScheduledIntentTextBox.Text,
                ComboTag(_panels.ScheduledIntentTypeBox),
                ComboTag(_panels.ScheduledTriggerTypeBox),
                _panels.ScheduledTimezoneBox.Text,
                _panels.ScheduledRunAtBox.Text,
                _panels.ScheduledIntervalBox.Text,
                _panels.ScheduledCronBox.Text,
                _panels.ScheduledActionPromptBox.Text);
            var selected = _panels.ScheduledIntentsList.SelectedItem as ScheduledIntentItemViewModel;
            object payload = selected is null
                ? new { action = "create", intent = values }
                : new { action = "update", intent_id = selected.IntentId, updates = values };
            using var doc = await _postJsonAsync($"{_apiBase()}/scheduler/intents", payload);
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "保存定时任务");
            var result = doc.RootElement.GetProperty("result");
            var intentId = ReadString(result, "intent_id");
            await LoadAsync();
            _panels.ScheduledIntentsList.SelectedValue = intentId;
            RenderSelectedScheduledIntent();
            _panels.SchedulerActionText.Text = "定时任务已保存。";
        }
        catch (Exception ex)
        {
            _panels.SchedulerActionText.Text = $"保存失败：{ex.Message}";
        }
    }

    internal async Task RunScheduledIntentActionAsync(string action)
    {
        if (_panels.ScheduledIntentsList.SelectedItem is not ScheduledIntentItemViewModel item)
        {
            _panels.SchedulerActionText.Text = "请先选择定时任务。";
            return;
        }
        if (action == "cancel" && !_confirmDestructiveAction("取消定时任务", $"确认取消“{item.Text}”？"))
        {
            return;
        }
        try
        {
            using var doc = await _postJsonAsync($"{_apiBase()}/scheduler/intents", new
            {
                action,
                intent_id = item.IntentId,
            });
            JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "更新定时任务");
            await LoadAsync();
            _panels.ScheduledIntentsList.SelectedValue = item.IntentId;
            RenderSelectedScheduledIntent();
            _panels.SchedulerActionText.Text = action == "run_now" ? "试跑事件已发送。" : "定时任务状态已更新。";
        }
        catch (Exception ex)
        {
            _panels.SchedulerActionText.Text = $"操作失败：{ex.Message}";
        }
    }

    internal static Dictionary<string, object> BuildScheduledIntentValues(
        string text,
        string intentType,
        string triggerType,
        string timezone,
        string runAt,
        string intervalSeconds,
        string cron,
        string actionPrompt)
    {
        var normalizedText = string.Join(" ", text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        if (string.IsNullOrWhiteSpace(normalizedText))
        {
            throw new ArgumentException("提醒内容不能为空", nameof(text));
        }
        var trigger = string.IsNullOrWhiteSpace(triggerType) ? "date" : triggerType;
        if (!double.TryParse(intervalSeconds, NumberStyles.Float, CultureInfo.InvariantCulture, out var interval))
        {
            interval = 0;
        }
        if (trigger == "date" && string.IsNullOrWhiteSpace(runAt))
        {
            throw new ArgumentException("指定时间不能为空", nameof(runAt));
        }
        if (trigger == "interval" && interval <= 0)
        {
            throw new ArgumentException("间隔秒数必须大于 0", nameof(intervalSeconds));
        }
        if (trigger == "cron" && cron.Split(' ', StringSplitOptions.RemoveEmptyEntries).Length != 5)
        {
            throw new ArgumentException("Cron 必须包含 5 个字段", nameof(cron));
        }
        return new Dictionary<string, object>
        {
            ["text"] = normalizedText,
            ["intent_type"] = string.IsNullOrWhiteSpace(intentType) ? "reminder" : intentType,
            ["trigger_type"] = trigger,
            ["timezone"] = string.IsNullOrWhiteSpace(timezone) ? "Asia/Shanghai" : timezone.Trim(),
            ["run_at"] = runAt.Trim(),
            ["interval_seconds"] = interval,
            ["cron"] = cron.Trim(),
            ["action_prompt"] = actionPrompt.Trim(),
        };
    }

    private void RenderToolAuthorization(JsonElement state, string selectedToolId = "")
    {
        var previous = string.IsNullOrWhiteSpace(selectedToolId)
            ? (_panels.ToolAuthorizationList.SelectedItem as ToolAuthorizationItemViewModel)?.ToolId ?? ""
            : selectedToolId;
        ToolAuthorizations.Clear();
        if (state.TryGetProperty("entries", out var entries) && entries.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in entries.EnumerateArray())
            {
                ToolAuthorizations.Add(ToolAuthorizationItemViewModel.FromJson(entry));
            }
        }
        _panels.ToolAuthorizationSummaryText.Text = $"启用 {ReadInt(state, "enabled_count")} · 停用 {ReadInt(state, "disabled_count")} · 配置 {ReadString(state, "path", "--")}";
        _panels.ToolAuthorizationList.SelectedValue = previous;
        if (_panels.ToolAuthorizationList.SelectedItem is null && ToolAuthorizations.Count > 0)
        {
            _panels.ToolAuthorizationList.SelectedIndex = 0;
        }
        RenderSelectedToolAuthorization();
    }

    private void RenderScheduler(JsonElement state)
    {
        var previous = (_panels.ScheduledIntentsList.SelectedItem as ScheduledIntentItemViewModel)?.IntentId ?? "";
        ScheduledIntents.Clear();
        if (state.TryGetProperty("intents", out var intents) && intents.ValueKind == JsonValueKind.Array)
        {
            foreach (var intent in intents.EnumerateArray())
            {
                ScheduledIntents.Add(ScheduledIntentItemViewModel.FromJson(intent));
            }
        }
        var jobDefaults = state.TryGetProperty("job_defaults", out var defaults) ? defaults : default;
        _panels.SchedulerSummaryText.Text = jobDefaults.ValueKind == JsonValueKind.Object
            ? $"任务 {ScheduledIntents.Count} · misfire {ReadInt(jobDefaults, "misfire_grace_time")}s · 并发 {ReadInt(jobDefaults, "max_instances")}"
            : $"任务 {ScheduledIntents.Count}";
        _panels.ScheduledIntentsList.SelectedValue = previous;
        if (_panels.ScheduledIntentsList.SelectedItem is null && ScheduledIntents.Count > 0)
        {
            _panels.ScheduledIntentsList.SelectedIndex = 0;
        }
        if (ScheduledIntents.Count > 0)
        {
            RenderSelectedScheduledIntent();
        }
        else
        {
            NewScheduledIntent();
        }
    }

    private void SyncScheduledActionButtons()
    {
        var selected = _panels.ScheduledIntentsList.SelectedItem is ScheduledIntentItemViewModel;
        _panels.PauseScheduledIntentButton.IsEnabled = selected;
        _panels.ResumeScheduledIntentButton.IsEnabled = selected;
        _panels.RunScheduledIntentButton.IsEnabled = selected;
        _panels.CancelScheduledIntentButton.IsEnabled = selected;
    }

    private static string ComboTag(ComboBox combo)
    {
        return combo.SelectedItem is ComboBoxItem item
            ? Convert.ToString(item.Tag, CultureInfo.InvariantCulture) ?? ""
            : combo.Text.Trim();
    }

    private static void SetComboTag(ComboBox combo, string value)
    {
        foreach (var raw in combo.Items)
        {
            if (raw is ComboBoxItem item && string.Equals(Convert.ToString(item.Tag), value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = item;
                return;
            }
        }
        combo.SelectedItem = null;
        combo.Text = value;
    }

    private static string ReadString(JsonElement element, string key, string fallback = "")
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        var text = value.ValueKind == JsonValueKind.String ? value.GetString() : value.GetRawText();
        return string.IsNullOrWhiteSpace(text) ? fallback : text;
    }

    private static int ReadInt(JsonElement element, string key)
    {
        return element.TryGetProperty(key, out var value) && value.TryGetInt32(out var parsed) ? parsed : 0;
    }

}

internal sealed record ToolAuthorizationItemViewModel(
    string ToolId,
    bool Enabled,
    string Risk,
    string ConfirmationPolicy,
    string Source,
    double UpdatedAt)
{
    public string DisplayLabel => $"{(Enabled ? "已启用" : "已停用")} · {ToolId} · {Risk}";
    internal string UpdatedAtLabel => UpdatedAt > 0
        ? DateTimeOffset.FromUnixTimeMilliseconds((long)(UpdatedAt * 1000)).ToLocalTime().ToString("yyyy-MM-dd HH:mm")
        : "--";

    internal static ToolAuthorizationItemViewModel FromJson(JsonElement element) => new(
        Read(element, "tool_id"),
        element.TryGetProperty("enabled", out var enabled) && enabled.ValueKind == JsonValueKind.True,
        Read(element, "risk", "safe"),
        Read(element, "confirmation_policy", "never"),
        Read(element, "source", "registry"),
        element.TryGetProperty("updated_at", out var updated) && updated.TryGetDouble(out var timestamp) ? timestamp : 0);

    private static string Read(JsonElement element, string key, string fallback = "") =>
        element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback
            : fallback;
}

internal sealed record ScheduledIntentItemViewModel(
    string IntentId,
    string Text,
    string IntentType,
    string TriggerType,
    string Timezone,
    string RunAt,
    double IntervalSeconds,
    string Cron,
    string ActionPrompt,
    string Status,
    string NextRunTime)
{
    public string DisplayLabel => $"{UiDisplayText.Status(Status)} · {Text} · {TriggerLabel}";
    internal string TriggerLabel => TriggerType switch
    {
        "date" => string.IsNullOrWhiteSpace(RunAt) ? "指定时间" : RunAt,
        "interval" => $"每 {IntervalSeconds:g}s",
        "cron" => Cron,
        _ => TriggerType,
    };
    internal string NextRunLabel => string.IsNullOrWhiteSpace(NextRunTime) ? "--" : NextRunTime;

    internal static ScheduledIntentItemViewModel FromJson(JsonElement element) => new(
        Read(element, "intent_id"),
        Read(element, "text"),
        Read(element, "intent_type", "reminder"),
        Read(element, "trigger_type", "date"),
        Read(element, "timezone", "Asia/Shanghai"),
        Read(element, "run_at"),
        element.TryGetProperty("interval_seconds", out var interval) && interval.TryGetDouble(out var seconds) ? seconds : 0,
        Read(element, "cron"),
        Read(element, "action_prompt"),
        Read(element, "status", "active"),
        Read(element, "next_run_time"));

    private static string Read(JsonElement element, string key, string fallback = "") =>
        element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? fallback
            : fallback;
}
