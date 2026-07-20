using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;

namespace SpiritKinDesktop;

internal sealed partial class WorkspaceController
{
    internal async Task LoadAvatarAsync()
    {
        try
        {
            await Task.Run(EnsureFrontendService);
            await WorkbenchShell.AvatarView.EnsureCoreWebView2Async();
            WorkbenchShell.AvatarView.CoreWebView2.Settings.AreDevToolsEnabled = true;
            WorkbenchShell.AvatarView.CoreWebView2.WebMessageReceived -= AvatarView_WebMessageReceived;
            WorkbenchShell.AvatarView.CoreWebView2.WebMessageReceived += AvatarView_WebMessageReceived;
            WorkbenchShell.AvatarView.NavigationCompleted -= AvatarView_NavigationCompleted;
            WorkbenchShell.AvatarView.NavigationCompleted += AvatarView_NavigationCompleted;
            var url = AvatarUrl();
            if (WorkbenchShell.AvatarView.Source is null || !string.Equals(WorkbenchShell.AvatarView.Source.ToString(), url, StringComparison.OrdinalIgnoreCase))
            {
                WorkbenchShell.AvatarView.Source = new Uri(url);
            }
            else
            {
                await SyncAvatarSessionAsync();
            }
            WorkspaceSidebar.ConnectionStatusText.Text = $"3D 面板已加载：{FrontendBaseUrl()}";
        }
        catch (Exception ex)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"3D 面板加载失败：{ex.Message}";
        }
    }

    private async void AvatarView_NavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        await SyncAvatarThemeAsync();
        await SyncAvatarSessionAsync();
    }

    internal async Task OpenAvatarFloatWindowAsync()
    {
        try
        {
            await Task.Run(EnsureFrontendService);
            if (_avatarFloatWindow is not null)
            {
                _avatarFloatWindow.Activate();
                if (_avatarFloatView is not null)
                {
                    await SyncAvatarSessionAsync();
                }
                return;
            }

            _avatarFloatView = new WebView2();
            _avatarFloatWindow = new Window
            {
                Title = "SpiritKin 3D Float",
                Width = 420,
                Height = 560,
                MinWidth = 320,
                MinHeight = 420,
                Topmost = true,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Owner = _owner(),
                Background = new SolidColorBrush(Color.FromRgb(7, 13, 21)),
                Content = _avatarFloatView,
            };
            _avatarFloatWindow.Closed += (_, _) =>
            {
                _avatarFloatView?.Dispose();
                _avatarFloatView = null;
                _avatarFloatWindow = null;
            };
            _avatarFloatWindow.Show();
            await _avatarFloatView.EnsureCoreWebView2Async();
            _avatarFloatView.CoreWebView2.Settings.AreDevToolsEnabled = true;
            _avatarFloatView.CoreWebView2.WebMessageReceived += AvatarView_WebMessageReceived;
            _avatarFloatView.Source = new Uri(AvatarUrl());
            _avatarFloatView.NavigationCompleted += async (_, _) =>
            {
                await SyncAvatarThemeAsync();
                await SyncAvatarSessionAsync();
            };
            WorkspaceSidebar.ConnectionStatusText.Text = "已打开桌面 3D 浮窗。";
        }
        catch (Exception ex)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"打开 3D 浮窗失败：{ex.Message}";
        }
    }

    internal async Task SyncAvatarSessionAsync(string? requestId = null)
    {
        var sessionId = State.ActiveSessionId;
        var currentRequestId = string.IsNullOrWhiteSpace(requestId) ? LatestCommandRequestId() : requestId.Trim();
        if (string.IsNullOrWhiteSpace(sessionId))
        {
            return;
        }
        _lastAvatarSessionId = sessionId;
        _lastAvatarRequestId = currentRequestId;
        var payload = JsonSerializer.Serialize(new
        {
            type = "spiritkin.session",
            session_id = sessionId,
            request_id = currentRequestId,
        }, _jsonOptions);
        await PostAvatarWebMessageAsync(WorkbenchShell.AvatarView, WorkbenchShell.AvatarView.CoreWebView2, payload);
        if (_avatarFloatView is not null)
        {
            await PostAvatarWebMessageAsync(_avatarFloatView, _avatarFloatView.CoreWebView2, payload);
        }
    }

    private void AvatarView_WebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (!TryReadAvatarSuggestionMessage(e.WebMessageAsJson, out var prompt))
        {
            return;
        }
        ShowWorkspacePage("chat");
        var target = QuickChatMode ? ChatWorkspace.EmptyPromptBox : ChatWorkspace.PromptBox;
        if (!string.IsNullOrWhiteSpace(prompt))
        {
            target.Text = prompt;
            target.CaretIndex = target.Text.Length;
        }
        target.Focus();
        WorkspaceSidebar.ConnectionStatusText.Text = "已打开建议对应的对话，确认内容后再发送。";
        WorkspaceSidebar.ConnectionStatusText.Visibility = Visibility.Visible;
    }

    internal static bool TryReadAvatarSuggestionMessage(string json, out string prompt)
    {
        prompt = "";
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind != JsonValueKind.Object
                || !string.Equals(ReadJsonString(doc.RootElement, "type"), "spiritkin.open_suggestion", StringComparison.Ordinal))
            {
                return false;
            }
            prompt = ReadJsonString(doc.RootElement, "prompt").Trim();
            if (prompt.Length > 500)
            {
                prompt = prompt[..500];
            }
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static async Task PostAvatarWebMessageAsync(FrameworkElement view, CoreWebView2? coreWebView, string payload)
    {
        try
        {
            if (coreWebView is null)
            {
                return;
            }
            await view.Dispatcher.InvokeAsync(() => coreWebView.PostWebMessageAsJson(payload));
        }
        catch
        {
            // The 3D panel may still be navigating; the URL query carries the same state on load.
        }
    }

    /// <summary>把当前日/夜主题同步给内嵌 3D 页：设置 documentElement 的 data-theme 并派发 spiritkin.theme 事件。</summary>
    internal async Task SyncAvatarThemeAsync()
    {
        var theme = ThemeManager.CurrentIsDark ? "dark" : "light";
        await PostAvatarThemeAsync(WorkbenchShell.AvatarView, WorkbenchShell.AvatarView.CoreWebView2, theme);
        if (_avatarFloatView is not null)
        {
            await PostAvatarThemeAsync(_avatarFloatView, _avatarFloatView.CoreWebView2, theme);
        }
    }

    private static async Task PostAvatarThemeAsync(FrameworkElement view, CoreWebView2? coreWebView, string theme)
    {
        try
        {
            if (coreWebView is null)
            {
                return;
            }
            coreWebView.Profile.PreferredColorScheme = theme == "dark"
                ? CoreWebView2PreferredColorScheme.Dark
                : CoreWebView2PreferredColorScheme.Light;
            var script =
                "(function(t){document.documentElement.setAttribute('data-theme',t);" +
                "window.dispatchEvent(new CustomEvent('spiritkin.theme',{detail:{theme:t}}));})(" +
                JsonSerializer.Serialize(theme) + ");";
            await view.Dispatcher.InvokeAsync(() => coreWebView.ExecuteScriptAsync(script));
        }
        catch
        {
            // 页面仍在导航时忽略；NavigationCompleted 会再补一次。
        }
    }

    internal async Task PostAvatarRuntimeEventsAsync(IReadOnlyList<RuntimeEvent> events, string requestId)
    {
        if (events.Count == 0)
        {
            return;
        }
        var scopedEvents = new List<Dictionary<string, object?>>();
        foreach (var ev in events)
        {
            scopedEvents.Add(new Dictionary<string, object?>
            {
                ["type"] = ev.Type,
                ["payload"] = ScopeAvatarPayload(ev.Payload, requestId),
            });
        }
        var payload = JsonSerializer.Serialize(new
        {
            type = "spiritkin.events",
            session_id = State.ActiveSessionId,
            request_id = requestId,
            events = scopedEvents,
        }, _jsonOptions);
        await PostAvatarWebMessageAsync(WorkbenchShell.AvatarView, WorkbenchShell.AvatarView.CoreWebView2, payload);
        if (_avatarFloatView is not null)
        {
            await PostAvatarWebMessageAsync(_avatarFloatView, _avatarFloatView.CoreWebView2, payload);
        }
    }

    private Dictionary<string, object?> ScopeAvatarPayload(JsonElement payload, string requestId)
    {
        var dict = JsonElementToDictionary(payload);
        dict["session_id"] = State.ActiveSessionId;
        dict["request_id"] = requestId;
        if (dict.TryGetValue("data", out var data) && data is string dataJson)
        {
            try
            {
                using var doc = JsonDocument.Parse(dataJson);
                if (doc.RootElement.ValueKind == JsonValueKind.Object)
                {
                    var dataDict = JsonElementToDictionary(doc.RootElement);
                    dataDict["session_id"] = State.ActiveSessionId;
                    dataDict["request_id"] = requestId;
                    dict["data"] = dataDict;
                }
            }
            catch
            {
                // Keep the original event data if it is not valid JSON.
            }
        }
        return dict;
    }

}


