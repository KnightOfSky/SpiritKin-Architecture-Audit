using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class ServicesController
{
    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly Func<Task> _loadDiagnosticsAsync;
    private readonly Func<Task> _loadLogsAsync;
    private readonly Func<Task> _loadDailyAsync;
    private readonly Func<DesktopSession> _activeSession;
    private readonly Func<DesktopSession, DesktopItem?> _projectForSession;
    private readonly Func<DesktopItem?> _selectedProject;
    private readonly Func<DesktopItem, string?> _resolveProjectWorkspace;
    private readonly Func<string> _activeWorkspaceRoot;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<string, string, string, bool> _confirmAction;
    private readonly Action _startWebSocket;

    private readonly ObservableCollection<ServiceViewModel> _services = new();
    private readonly ObservableCollection<ServicePortViewModel> _servicePorts = new();
    private readonly ObservableCollection<EventViewModel> _serviceActions = new();
    private readonly List<string> _pendingPortRestartServiceIds = new();
    private string _pendingPortMigrationText = "";
    private string _pendingCommandGatewayUrl = "";
    private string _pendingEventBridgeUrl = "";
    private bool _pendingPortRestartIncludesCommandGateway;

    public ServicesController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        Func<Task> loadDiagnosticsAsync,
        Func<Task> loadLogsAsync,
        Func<Task> loadDailyAsync,
        Func<DesktopSession> activeSession,
        Func<DesktopSession, DesktopItem?> projectForSession,
        Func<DesktopItem?> selectedProject,
        Func<DesktopItem, string?> resolveProjectWorkspace,
        Func<string> activeWorkspaceRoot,
        Func<string, string, bool> confirmDestructiveAction,
        Func<string, string, string, bool> confirmAction,
        Action startWebSocket)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _loadDiagnosticsAsync = loadDiagnosticsAsync;
        _loadLogsAsync = loadLogsAsync;
        _loadDailyAsync = loadDailyAsync;
        _activeSession = activeSession;
        _projectForSession = projectForSession;
        _selectedProject = selectedProject;
        _resolveProjectWorkspace = resolveProjectWorkspace;
        _activeWorkspaceRoot = activeWorkspaceRoot;
        _confirmDestructiveAction = confirmDestructiveAction;
        _confirmAction = confirmAction;
        _startWebSocket = startWebSocket;
    }

    internal ObservableCollection<ServiceViewModel> Services => _services;
    internal ObservableCollection<ServicePortViewModel> ServicePorts => _servicePorts;
    internal ObservableCollection<EventViewModel> ServiceActions => _serviceActions;

    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private string ApiBase() => _apiBase();
    private Task LoadDiagnosticsAsync() => _loadDiagnosticsAsync();
    private Task LoadLogsAsync() => _loadLogsAsync();
    private Task LoadDailyAsync() => _loadDailyAsync();
    private DesktopSession ActiveSession() => _activeSession();
    private DesktopItem? ProjectForSession(DesktopSession session) => _projectForSession(session);
    private DesktopItem? SelectedProject() => _selectedProject();
    private string? ResolveProjectWorkspace(DesktopItem project) => _resolveProjectWorkspace(project);
    private string ActiveWorkspaceRoot() => _activeWorkspaceRoot();
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private bool ConfirmAction(string title, string message, string confirmText = "确定") => _confirmAction(title, message, confirmText);
    private void StartWebSocket() => _startWebSocket();

    internal async Task ServiceActionFromButtonAsync(object sender, string action)
    {
        if (sender is Button button && button.Tag is string serviceId && !string.IsNullOrWhiteSpace(serviceId))
        {
            await ServiceActionAsync(serviceId, action);
        }
    }

    internal void ServicePortsList_SelectionChanged(object sender, SelectionChangedEventArgs e, bool rendering)
    {
        if (rendering)
        {
            return;
        }
        RenderSelectedServicePortEditor();
    }

    internal ServiceViewModel? FindServiceForDailyTitle(string title)
    {
        return _services.FirstOrDefault(item => string.Equals(item.ServiceId, title, StringComparison.OrdinalIgnoreCase))
            ?? _services.FirstOrDefault(item => string.Equals(item.Label, title, StringComparison.OrdinalIgnoreCase) || item.Label.Contains(title, StringComparison.OrdinalIgnoreCase) || title.Contains(item.Label, StringComparison.OrdinalIgnoreCase));
    }

    private static string FormatTimeFromDouble(string raw) => JsonResponseHelpers.FormatTimeFromDouble(raw);
    private static string ReadJsonString(JsonElement element, string key) => JsonResponseHelpers.ReadJsonString(element, key);
    private static string ReadJsonString(JsonElement element, string key, string fallback) => JsonResponseHelpers.ReadJsonString(element, key, fallback);
    private static int ReadJsonInt(JsonElement element, string key) => JsonResponseHelpers.ReadJsonInt(element, key);
    private static bool ReadJsonBool(JsonElement element, string key, bool fallback = false) => JsonResponseHelpers.ReadJsonBool(element, key, fallback);
}
