using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed class SafetyController
{
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly ChatWorkspaceView _chatWorkspace;
    private readonly Func<string, string, string, string?> _promptText;

    private bool _lastSafetyActive;
    private string _lastSafetyMode = "normal";

    public ObservableCollection<EventViewModel> Events { get; } = new();

    public SafetyController(
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        ChatWorkspaceView chatWorkspace,
        Func<string, string, string, string?> promptText)
    {
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _chatWorkspace = chatWorkspace;
        _promptText = promptText;
    }

    public async Task LoadAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/safety");
            Render(doc.RootElement.GetProperty("safety"));
        }
        catch (Exception ex)
        {
            _chatWorkspace.SafetyStatusText.Text = "安全未知";
            _chatWorkspace.SafetyStatusText.Background = Brushes.Transparent;
            _chatWorkspace.SafetyStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyWarningBrush");
            _chatWorkspace.SafetyStatusText.ToolTip = $"安全状态加载失败：{ex.Message}";
        }
    }

    public async Task SetStopAsync(bool active)
    {
        try
        {
            var payload = new Dictionary<string, object?>
            {
                ["action"] = active ? "panic_stop" : "resume",
                ["mode"] = active ? "soft_stop" : "normal",
                ["reason"] = active ? "Desktop safety stop button" : "Desktop resume button",
                ["actor"] = "wpf_desktop",
            };
            if (!active && _lastSafetyActive && string.Equals(_lastSafetyMode, "hard_stop", StringComparison.OrdinalIgnoreCase))
            {
                const string confirmationText = "RESUME_HARD_STOP";
                var confirmation = _promptText(
                    "解除硬停止",
                    $"硬停止会阻断非恢复类 POST 请求。请输入 {confirmationText} 以确认恢复。",
                    "");
                if (!string.Equals(confirmation?.Trim(), confirmationText, StringComparison.Ordinal))
                {
                    _chatWorkspace.SafetyStatusText.ToolTip = "硬停止恢复已取消：确认文本不匹配。";
                    return;
                }
                payload["confirmation_text"] = confirmationText;
            }
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/safety", payload);
            EnsureOkResponse(doc.RootElement, active ? "安全停止失败" : "安全恢复失败");
            Render(doc.RootElement.GetProperty("safety"));
        }
        catch (Exception ex)
        {
            _chatWorkspace.SafetyStatusText.Text = active ? "停止失败" : "恢复失败";
            _chatWorkspace.SafetyStatusText.ToolTip = ex.Message;
        }
    }

    private void Render(JsonElement safety)
    {
        var active = JsonHelpers.ReadBool(safety, "active", false);
        var mode = JsonHelpers.ReadString(safety, "mode", active ? "soft_stop" : "normal");
        _lastSafetyActive = active;
        _lastSafetyMode = mode;
        _chatWorkspace.SafetyStatusText.Text = active ? (mode == "hard_stop" ? "硬停止" : "已停止") : "安全正常";
        _chatWorkspace.SafetyStatusText.Background = Brushes.Transparent;
        _chatWorkspace.SafetyStatusText.SetResourceReference(
            TextBlock.ForegroundProperty,
            active ? "FantasyDangerBrush" : "FantasySuccessBrush");
        _chatWorkspace.SafetyStatusText.ToolTip = $"模式：{mode}{Environment.NewLine}原因：{JsonHelpers.ReadString(safety, "reason", "--")}{Environment.NewLine}更新：{JsonHelpers.ReadString(safety, "updated_at", "--")}";
        _chatWorkspace.SafetyStopButton.IsEnabled = !active;
        _chatWorkspace.SafetyResumeButton.IsEnabled = active;
        // Keep both global safety commands discoverable. State is expressed by
        // enabled/disabled treatment instead of making the recovery command vanish.
        _chatWorkspace.SafetyStopButton.Visibility = Visibility.Visible;
        _chatWorkspace.SafetyResumeButton.Visibility = Visibility.Visible;
        RenderEvents(safety);
    }

    private void RenderEvents(JsonElement safety)
    {
        Events.Clear();
        if (safety.TryGetProperty("history", out var history) && history.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in history.EnumerateArray().Reverse().Take(20))
            {
                var action = JsonHelpers.ReadString(item, "action", "--");
                var mode = JsonHelpers.ReadString(item, "mode", "--");
                var actor = JsonHelpers.ReadString(item, "actor", "--");
                var reason = JsonHelpers.ReadString(item, "reason", "--");
                Events.Add(new EventViewModel(
                    $"{JsonHelpers.ReadString(item, "at", "--")} · {action}",
                    $"{mode} · {actor}{Environment.NewLine}{reason}".Trim()));
            }
        }
        if (Events.Count == 0)
        {
            Events.Add(new EventViewModel("安全停止历史", "暂无停止/恢复记录。"));
        }
    }

    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

    internal static class JsonHelpers
    {
        public static string ReadString(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return "";
            }
            return value.ValueKind switch
            {
                JsonValueKind.String => value.GetString() ?? "",
                JsonValueKind.Number => value.GetRawText(),
                JsonValueKind.True => "true",
                JsonValueKind.False => "false",
                JsonValueKind.Null => "",
                _ => value.GetRawText(),
            };
        }

        public static string ReadString(JsonElement element, string key, string fallback)
        {
            var value = ReadString(element, key);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        public static bool ReadBool(JsonElement element, string key, bool fallback = false)
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
    }
}
