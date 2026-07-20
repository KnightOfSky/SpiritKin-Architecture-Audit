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
    internal async Task OpenWebPreviewWindowAsync()
    {
        try
        {
            await Task.Run(EnsureFrontendService);
            var url = $"{FrontendBaseUrl()}/desktop_console.html?cmd={Uri.EscapeDataString(CommandUrl())}&ws={Uri.EscapeDataString(WorkspaceSidebar.WsUrlBox.Text.Trim())}";
            if (_webPreviewWindow is not null)
            {
                _webPreviewWindow.Activate();
                if (_webPreviewView is not null)
                {
                    _webPreviewView.Source = new Uri(url);
                }
                return;
            }

            _webPreviewView = new WebView2();
            _webPreviewWindow = new Window
            {
                Title = "SpiritKin Web Preview",
                Width = 960,
                Height = 680,
                MinWidth = 640,
                MinHeight = 420,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Owner = _owner(),
                Background = new SolidColorBrush(Color.FromRgb(7, 13, 21)),
                Content = _webPreviewView,
            };
            _webPreviewWindow.Closed += (_, _) =>
            {
                _webPreviewView?.Dispose();
                _webPreviewView = null;
                _webPreviewWindow = null;
            };
            _webPreviewWindow.Show();
            await _webPreviewView.EnsureCoreWebView2Async();
            _webPreviewView.CoreWebView2.Settings.AreDevToolsEnabled = true;
            _webPreviewView.Source = new Uri(url);
            WorkspaceSidebar.ConnectionStatusText.Text = "已打开桌面 Web preview 浮窗。";
        }
        catch (Exception ex)
        {
            WorkspaceSidebar.ConnectionStatusText.Text = $"打开 Web preview 失败：{ex.Message}";
        }
    }

    internal void StartWebSocket()
    {
        _wsCts?.Cancel();
        _ws?.Dispose();
        _wsCts = new CancellationTokenSource();
        _ = Task.Run(() => WebSocketLoopAsync(_wsCts.Token));
    }

    private async Task WebSocketLoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            // 标记当前阶段，catch 时据此区分 connect / receive / apply 哪一步失败，不再吞异常。
            var phase = "connect";
            try
            {
                // WPF 控件有线程亲和性：本方法跑在 Task.Run 的后台线程，直接读 WsUrlBox.Text 会抛
                // InvalidOperationException（跨线程访问），导致永远连不上、状态卡在“实时重连中”。
                // 必须切到 UI 线程读取 URL。
                var wsUrl = await Dispatcher.InvokeAsync(() => WorkspaceSidebar.WsUrlBox.Text.Trim());
                var sessionToken = await Dispatcher.InvokeAsync(() => WorkspaceSidebar.TokenBox.Password.Trim());
                using var ws = new ClientWebSocket();
                _ws = ws;
                await ws.ConnectAsync(new Uri(wsUrl), token);
                var authPayload = JsonSerializer.Serialize(new { type = "runtime.auth", token = sessionToken }, _jsonOptions);
                var authBytes = Encoding.UTF8.GetBytes(authPayload);
                await ws.SendAsync(authBytes, WebSocketMessageType.Text, true, token);
                // 连接态止血：WS 存活期间由实时 work_updated 投影思考链，HTTP fallback 跳过以免重复。
                _wsConnected = true;
                await Dispatcher.InvokeAsync(() =>
                {
                    ChatWorkspace.WsStatusText.Text = "实时已连接";
                    ChatWorkspace.WsStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasySuccessBrush");
                    ChatWorkspace.WsStatusText.Background = Brushes.Transparent;
                    ChatWorkspace.WsStatusText.ToolTip = $"已连接 {wsUrl}";
                });
                var buffer = new byte[128 * 1024];
                while (ws.State == WebSocketState.Open && !token.IsCancellationRequested)
                {
                    phase = "receive";
                    using var ms = new MemoryStream();
                    WebSocketReceiveResult result;
                    do
                    {
                        result = await ws.ReceiveAsync(buffer, token);
                        if (result.MessageType == WebSocketMessageType.Close)
                        {
                            break;
                        }
                        ms.Write(buffer, 0, result.Count);
                    }
                    while (!result.EndOfMessage);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        break;
                    }
                    var json = Encoding.UTF8.GetString(ms.ToArray());
                    RuntimeEvent? ev;
                    try
                    {
                        ev = JsonSerializer.Deserialize<RuntimeEvent>(json, _jsonOptions);
                    }
                    catch (JsonException parseEx)
                    {
                        // 单条事件解析失败不应中断整条连接（否则一条坏事件就触发重连风暴）。
                        Debug.WriteLine($"[ws] parse skipped: {parseEx.Message}");
                        continue;
                    }
                    if (ev is not null)
                    {
                        phase = "apply";
                        await Dispatcher.InvokeAsync(() => ApplyEvent(ev));
                        phase = "receive";
                    }
                }
                // 内层循环退出（连接关闭或取消）：清存活标志，重新启用 HTTP 兜底。
                _wsConnected = false;
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested)
            {
                _wsConnected = false;
                return;
            }
            catch (Exception ex)
            {
                _wsConnected = false;
                // 显性化：阶段 + 异常类型 + 消息，落 Debug 输出并通过状态标签 ToolTip 暴露完整诊断。
                var detail = $"{phase}: {ex.GetType().Name}: {ex.Message}";
                Debug.WriteLine($"[ws] {detail}");
                await Dispatcher.InvokeAsync(() =>
                {
                    ChatWorkspace.WsStatusText.Text = $"实时重连中（{phase} 失败）";
                    ChatWorkspace.WsStatusText.SetResourceReference(TextBlock.ForegroundProperty, "FantasyWarningBrush");
                    ChatWorkspace.WsStatusText.Background = Brushes.Transparent;
                    ChatWorkspace.WsStatusText.ToolTip = detail;
                });
                try
                {
                    await Task.Delay(2000, token);
                }
                catch (TaskCanceledException)
                {
                    return;
                }
            }
        }
    }

}

