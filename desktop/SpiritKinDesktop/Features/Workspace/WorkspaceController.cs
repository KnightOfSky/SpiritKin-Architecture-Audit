using Microsoft.Web.WebView2.Wpf;
using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkspaceController
{
    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly ColumnDefinition ChatSplitterColumn;
    private readonly ColumnDefinition ChatColumn;
    private readonly ColumnDefinition RightSplitterColumn;
    private readonly ColumnDefinition RightPanelColumn;
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly string _rootDir;
    private readonly string _frontendDir;
    private readonly Func<DesktopState> _getState;
    private Action _renderState;
    private Func<Task> _saveStateAsync;
    private Func<DesktopSession> _managedEditorSession;
    private readonly Func<bool> _isRendering;
    private Func<bool> _workbenchPanelCollapsed;
    private Func<string> _latestCommandRequestId;
    private Action<RuntimeEvent> _applyEvent;
    private Action _prepareQuickChat = () => { };
    private Action _workspaceChanged = () => { };
    private readonly Func<Window> _owner;
    private readonly Dispatcher _dispatcher;
    private readonly ObservableCollection<ProjectViewModel> _managedProjects;
    private readonly ObservableCollection<QuickCommandViewModel> _quickCommands;

    private ClientWebSocket? _ws;
    private CancellationTokenSource? _wsCts;
    private volatile bool _wsConnected;
    private string _workspaceProjectContextId = "";
    private bool _rightNavCollapsed;
    private bool _quickChatMode = true;
    private bool _syncingQuickCommandSelection;
    private string _sessionFilter = "active";
    private bool _updatingSessionFilter;
    private Window? _avatarFloatWindow;
    private WebView2? _avatarFloatView;
    private Window? _webPreviewWindow;
    private WebView2? _webPreviewView;
    private string _lastAvatarSessionId = "";
    private string _lastAvatarRequestId = "";
    private int _frontendPort = RealtimeContract.DefaultPorts.Frontend;
    private int _eventBridgePort = RealtimeContract.DefaultPorts.EventBridge;
    private int _commandGatewayPort = RealtimeContract.DefaultPorts.CommandGateway;
    private int _remoteWorkerPort = RealtimeContract.DefaultPorts.RemoteWorker;
    private int _androidEndpointPort = RealtimeContract.DefaultPorts.AndroidEndpoint;
    private int _iosEndpointPort = RealtimeContract.DefaultPorts.IosEndpoint;

    public WorkspaceController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        ColumnDefinition chatSplitterColumn,
        ColumnDefinition chatColumn,
        ColumnDefinition rightSplitterColumn,
        ColumnDefinition rightPanelColumn,
        HttpClient http,
        JsonSerializerOptions jsonOptions,
        string rootDir,
        Func<DesktopState> getState,
        Action renderState,
        Func<Task> saveStateAsync,
        Func<DesktopSession> managedEditorSession,
        Func<bool> isRendering,
        Func<bool> workbenchPanelCollapsed,
        Func<string> latestCommandRequestId,
        Action<RuntimeEvent> applyEvent,
        Func<Window> owner,
        Dispatcher dispatcher,
        ObservableCollection<ProjectViewModel> managedProjects,
        ObservableCollection<QuickCommandViewModel> quickCommands)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        ChatSplitterColumn = chatSplitterColumn;
        ChatColumn = chatColumn;
        RightSplitterColumn = rightSplitterColumn;
        RightPanelColumn = rightPanelColumn;
        _http = http;
        _jsonOptions = jsonOptions;
        _rootDir = rootDir;
        _frontendDir = Path.Combine(rootDir, "frontend");
        _getState = getState;
        _renderState = renderState;
        _saveStateAsync = saveStateAsync;
        _managedEditorSession = managedEditorSession;
        _isRendering = isRendering;
        _workbenchPanelCollapsed = workbenchPanelCollapsed;
        _latestCommandRequestId = latestCommandRequestId;
        _applyEvent = applyEvent;
        _owner = owner;
        _dispatcher = dispatcher;
        _managedProjects = managedProjects;
        _quickCommands = quickCommands;
        // 主题热切时把 data-theme 推给已加载的内嵌 3D 页（换肤不依赖重新导航）。
        ThemeManager.ThemeChanged += _ => { _dispatcher.InvokeAsync(async () => await SyncAvatarThemeAsync()); };
    }

    internal int FrontendPort => _frontendPort;
    internal int EventBridgePort => _eventBridgePort;
    internal int CommandGatewayPort => _commandGatewayPort;
    internal int RemoteWorkerPort => _remoteWorkerPort;
    internal int AndroidEndpointPort => _androidEndpointPort;
    internal int IosEndpointPort => _iosEndpointPort;
    internal bool WsConnected => _wsConnected;
    internal bool QuickChatMode => _quickChatMode;
    internal string WorkspaceProjectContextId => _workspaceProjectContextId;

    internal void SetWorkbenchPanelCollapsedCallback(Func<bool> callback) => _workbenchPanelCollapsed = callback;
    internal void SetManagedEditorSessionCallback(Func<DesktopSession> callback) => _managedEditorSession = callback;
    internal void SetPrepareQuickChatCallback(Action callback) => _prepareQuickChat = callback ?? (() => { });
    internal void SetWorkspaceChangedCallback(Action callback) => _workspaceChanged = callback ?? (() => { });
    internal void SetRuntimeCallbacks(Action renderState, Func<Task> saveStateAsync, Func<string> latestCommandRequestId, Action<RuntimeEvent> applyEvent)
    {
        _renderState = renderState;
        _saveStateAsync = saveStateAsync;
        _latestCommandRequestId = latestCommandRequestId;
        _applyEvent = applyEvent;
    }
    internal void SetQuickChatMode(bool value) => _quickChatMode = value;
    internal void SetWorkspaceProjectContextId(string value)
    {
        var normalized = string.IsNullOrWhiteSpace(value) ? "" : value;
        if (string.Equals(_workspaceProjectContextId, normalized, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        _workspaceProjectContextId = normalized;
        _workspaceChanged();
    }
    internal void ClearWorkspaceProjectContextId() => SetWorkspaceProjectContextId("");

    private DesktopState State => _getState();
    private DesktopState _state => _getState();
    private bool _rendering => _isRendering();
    private Dispatcher Dispatcher => _dispatcher;
    private bool IsRendering() => _isRendering();
    private bool WorkbenchPanelCollapsed() => _workbenchPanelCollapsed();
    private string LatestCommandRequestId() => _latestCommandRequestId();
    private void RenderState() => _renderState();
    private Task SaveStateAsync() => _saveStateAsync();
    private DesktopSession ManagedEditorSession() => _managedEditorSession();
    private void ApplyEvent(RuntimeEvent ev) => _applyEvent(ev);

    internal string ApiBase() => (WorkspaceSidebar.ApiUrlBox.Text.Trim().Length == 0 ? $"http://127.0.0.1:{_commandGatewayPort}" : WorkspaceSidebar.ApiUrlBox.Text.Trim()).TrimEnd('/').Replace("/command", "");
    internal string CommandUrl() => $"{ApiBase()}/command";
    internal string DesktopStateUrl() => $"{ApiBase()}/desktop/state";
    internal string FrontendBaseUrl() => $"http://127.0.0.1:{_frontendPort}";
    internal string AvatarUrl()
    {
        var ws = Uri.EscapeDataString(WorkspaceSidebar.WsUrlBox.Text.Trim());
        var cmd = Uri.EscapeDataString(CommandUrl());
        var token = WorkspaceSidebar.TokenBox.Password.Trim();
        var tokenPart = string.IsNullOrWhiteSpace(token) ? "" : $"&token={Uri.EscapeDataString(token)}";
        return $"{FrontendBaseUrl()}/avatar_3d.html?config=models/spirit3d/manifest.json&v=atelier-stage-4&embed=1&cameraDistance=7.2&cameraYOffset=0.10&cameraTargetYOffset=0.62&ws={ws}&cmd={cmd}{tokenPart}";
    }

    internal void SetConnected(bool connected, string text)
    {
        WorkspaceSidebar.SyncDot.Fill = new SolidColorBrush(connected ? Color.FromRgb(22, 163, 74) : Color.FromRgb(100, 116, 139));
        WorkspaceSidebar.ConnectionStatusText.Text = text;
    }

    internal void SetStatus(string text) => Dispatcher.Invoke(() => WorkspaceSidebar.ConnectionStatusText.Text = text);

    internal string SessionToken() => WorkspaceSidebar.TokenBox.Password.Trim();

    internal void ApplyAuth(HttpRequestMessage request)
    {
        var token = WorkspaceSidebar.TokenBox.Password.Trim();
        if (string.IsNullOrWhiteSpace(token))
        {
            return;
        }
        request.Headers.Add("X-SpiritKin-Token", token);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }

    internal void DisposeRuntime()
    {
        _wsCts?.Cancel();
        _ws?.Dispose();
        _wsCts?.Dispose();
        _wsCts = null;
        _ws = null;
        _avatarFloatWindow?.Close();
        _webPreviewWindow?.Close();
    }

    internal static string FindWorkspaceRoot()
    {
        var seeds = new List<string>();
        var configured = Environment.GetEnvironmentVariable("SPIRITKIN_WORKSPACE_ROOT");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            seeds.Add(Environment.ExpandEnvironmentVariables(configured.Trim()));
        }

        seeds.Add(Directory.GetCurrentDirectory());
        seeds.Add(AppContext.BaseDirectory);

        foreach (var seed in seeds.Where(seed => !string.IsNullOrWhiteSpace(seed)))
        {
            var dir = Path.GetFullPath(seed);
            for (var i = 0; i < 12; i++)
            {
                if (IsWorkspaceRoot(dir))
                {
                    return dir;
                }

                var parent = Directory.GetParent(dir);
                if (parent is null)
                {
                    break;
                }
                dir = parent.FullName;
            }
        }

        return seeds
            .Select(seed => Path.GetFullPath(Environment.ExpandEnvironmentVariables(seed)))
            .FirstOrDefault(Directory.Exists)
            ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
    }

    private static bool IsWorkspaceRoot(string path)
    {
        return Directory.Exists(path)
            && File.Exists(Path.Combine(path, "requirements.txt"))
            && File.Exists(Path.Combine(path, "frontend", "avatar_3d.html"))
            && File.Exists(Path.Combine(path, "backend", "app", "command_gateway.py"));
    }

    private static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private static string NewId(string prefix) => $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];
    private static string FormatTime(double seconds) => seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");

    private static void SetComboText(ComboBox combo, string value)
    {
        if (!string.IsNullOrWhiteSpace(combo.SelectedValuePath))
        {
            combo.SelectedValue = value;
            if (combo.SelectedItem is not null)
            {
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        foreach (var item in combo.Items.OfType<ComboBoxItem>())
        {
            if (string.Equals(ComboBoxItemValue(item), value, StringComparison.OrdinalIgnoreCase)
                || string.Equals(Convert.ToString(item.Content), value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = item;
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        combo.Text = value;
    }

    private static string ComboBoxItemValue(ComboBoxItem item)
    {
        var tag = Convert.ToString(item.Tag);
        return string.IsNullOrWhiteSpace(tag) ? Convert.ToString(item.Content) ?? "" : tag;
    }

    private static string ComboDisplayText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return Convert.ToString(item.Content) ?? "";
        }
        if (combo.SelectedItem is null && combo.SelectedValue is string selectedValue)
        {
            return selectedValue;
        }
        return Convert.ToString(combo.SelectedItem) ?? combo.Text;
    }

    private static void SyncEditableComboSelectionText(ComboBox combo)
    {
        if (!combo.IsEditable)
        {
            return;
        }
        var display = ComboDisplayText(combo).Trim();
        if (!string.IsNullOrWhiteSpace(display))
        {
            combo.Text = display;
        }
    }

    private static Dictionary<string, object?> JsonElementToDictionary(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return new Dictionary<string, object?>();
        }
        var dict = new Dictionary<string, object?>();
        foreach (var property in element.EnumerateObject())
        {
            dict[property.Name] = property.Value.ValueKind switch
            {
                JsonValueKind.String => property.Value.GetString(),
                JsonValueKind.Number => property.Value.TryGetInt64(out var number) ? number : property.Value.GetDouble(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Null => null,
                _ => property.Value.GetRawText(),
            };
        }
        return dict;
    }

    private static T? FindVisualChild<T>(DependencyObject? parent, Func<T, bool>? predicate = null)
        where T : DependencyObject
    {
        if (parent is null)
        {
            return null;
        }

        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T match && (predicate is null || predicate(match)))
            {
                return match;
            }

            var nested = FindVisualChild(child, predicate);
            if (nested is not null)
            {
                return nested;
            }
        }

        return null;
    }
}
