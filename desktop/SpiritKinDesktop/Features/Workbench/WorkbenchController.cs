using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Net.Http;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkbenchController
{
    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly IntegratedTerminalPanelView TerminalPanel;
    private readonly RowDefinition TerminalRow;
    private readonly string _rootDir;
    private readonly Func<DesktopState> _getState;
    private readonly Func<DesktopSession> _activeSession;
    private readonly Func<string> _activeWorkspaceRoot;
    private readonly Func<ProjectRuntimeProfile> _activeProjectRuntimeProfile;
    private readonly Func<ProjectRuntimeProfile, Dictionary<string, string>> _buildProjectRuntimeEnvironment;
    private readonly Func<string> _apiBase;
    private readonly Func<string> _commandUrl;
    private readonly Func<string> _frontendBaseUrl;
    private readonly Action _ensureFrontendService;
    private readonly Func<Task> _saveStateAsync;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<string, string, string, bool> _confirmAction;
    private readonly Func<string, string, string, string?> _promptText;
    private readonly Func<ContextMenu, string, RoutedEventHandler, MenuItem> _addContextMenuItem;
    private readonly Func<Separator> _createStyledSeparator;
    private readonly Action<ContextMenu> _applyMenuStyle;
    private readonly Action<ContextMenu, string> _addDisabledMenuHeader;
    private readonly Func<Dispatcher> _dispatcher;
    private Func<double> _assistantWorkStartedAt = () => 0;
    private Func<double> _assistantWorkDuration = () => 0;
    private Func<int, string> _pendingAttachmentNames = _ => "";
    private Func<bool> _hasPendingAttachments = () => false;
    private Func<string> _runtimeDisplay = () => "Work locally";

    private readonly ObservableCollection<EventViewModel> _workbenchProgress = new();
    private readonly ObservableCollection<EventViewModel> _sources = new();
    private readonly ObservableCollection<GitChangeViewModel> _gitChanges = new();

    private ConPtyTerminalSession? _terminalSession;
    private CancellationTokenSource? _terminalReadCts;
    private int _terminalInputStart;
    private bool _terminalUpdating;
    private bool _syncingGitSelection;
    private int _cachedGitDirtyCount;
    private string _cachedGithubCliStatus = "GitHub CLI 未检测";
    private bool _gitChangesLoaded;
    private bool _gitChangesLoading;
    private bool _workbenchPanelCollapsed;
    private int _workbenchPanelPersistVersion;
    private string _lastKnownBranch = "";
    private string _gitWorkspacePath = "";

    public WorkbenchController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        IntegratedTerminalPanelView terminalPanel,
        RowDefinition terminalRow,
        string rootDir,
        Func<DesktopState> getState,
        Func<DesktopSession> activeSession,
        Func<string> activeWorkspaceRoot,
        Func<ProjectRuntimeProfile> activeProjectRuntimeProfile,
        Func<ProjectRuntimeProfile, Dictionary<string, string>> buildProjectRuntimeEnvironment,
        Func<string> apiBase,
        Func<string> commandUrl,
        Func<string> frontendBaseUrl,
        Action ensureFrontendService,
        Func<Task> saveStateAsync,
        Func<string, string, bool> confirmDestructiveAction,
        Func<string, string, string, bool> confirmAction,
        Func<string, string, string, string?> promptText,
        Func<ContextMenu, string, RoutedEventHandler, MenuItem> addContextMenuItem,
        Func<Separator> createStyledSeparator,
        Action<ContextMenu> applyMenuStyle,
        Action<ContextMenu, string> addDisabledMenuHeader,
        Func<Dispatcher> dispatcher)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        TerminalPanel = terminalPanel;
        TerminalRow = terminalRow;
        _rootDir = rootDir;
        _getState = getState;
        _activeSession = activeSession;
        _activeWorkspaceRoot = activeWorkspaceRoot;
        _activeProjectRuntimeProfile = activeProjectRuntimeProfile;
        _buildProjectRuntimeEnvironment = buildProjectRuntimeEnvironment;
        _apiBase = apiBase;
        _commandUrl = commandUrl;
        _frontendBaseUrl = frontendBaseUrl;
        _ensureFrontendService = ensureFrontendService;
        _saveStateAsync = saveStateAsync;
        _confirmDestructiveAction = confirmDestructiveAction;
        _confirmAction = confirmAction;
        _promptText = promptText;
        _addContextMenuItem = addContextMenuItem;
        _createStyledSeparator = createStyledSeparator;
        _applyMenuStyle = applyMenuStyle;
        _addDisabledMenuHeader = addDisabledMenuHeader;
        _dispatcher = dispatcher;
    }

    internal ObservableCollection<EventViewModel> WorkbenchProgress => _workbenchProgress;
    internal ObservableCollection<EventViewModel> Sources => _sources;
    internal ObservableCollection<GitChangeViewModel> GitChanges => _gitChanges;
    internal int CachedGitDirtyCount => _cachedGitDirtyCount;
    internal bool PanelCollapsed => _workbenchPanelCollapsed;
    internal void SetLastKnownBranch(string value) => _lastKnownBranch = value;
    internal void SetComposerStatusCallbacks(
        Func<double> assistantWorkStartedAt,
        Func<double> assistantWorkDuration,
        Func<int, string> pendingAttachmentNames,
        Func<bool> hasPendingAttachments,
        Func<string> runtimeDisplay)
    {
        _assistantWorkStartedAt = assistantWorkStartedAt;
        _assistantWorkDuration = assistantWorkDuration;
        _pendingAttachmentNames = pendingAttachmentNames;
        _hasPendingAttachments = hasPendingAttachments;
        _runtimeDisplay = runtimeDisplay;
    }

    private DesktopState State => _getState();
    private DesktopSession ActiveSession() => _activeSession();
    private string ActiveWorkspaceRoot() => _activeWorkspaceRoot();
    private ProjectRuntimeProfile ActiveProjectRuntimeProfile() => _activeProjectRuntimeProfile();
    private Dictionary<string, string> BuildProjectRuntimeEnvironment(ProjectRuntimeProfile runtime) => _buildProjectRuntimeEnvironment(runtime);
    private string ApiBase() => _apiBase();
    private string CommandUrl() => _commandUrl();
    private string FrontendBaseUrl() => _frontendBaseUrl();
    private void EnsureFrontendService() => _ensureFrontendService();
    private Task SaveStateAsync() => _saveStateAsync();
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private bool ConfirmAction(string title, string message, string confirmText = "确定") => _confirmAction(title, message, confirmText);
    private string? PromptText(string title, string label, string initial = "") => _promptText(title, label, initial);
    private MenuItem AddContextMenuItem(ContextMenu menu, string header, RoutedEventHandler click) => _addContextMenuItem(menu, header, click);
    private Separator CreateStyledSeparator() => _createStyledSeparator();
    private void ApplyMenuStyle(ContextMenu menu) => _applyMenuStyle(menu);
    private void AddDisabledMenuHeader(ContextMenu menu, string header) => _addDisabledMenuHeader(menu, header);
    private double AssistantWorkStartedAt() => _assistantWorkStartedAt();
    private double AssistantWorkDuration() => _assistantWorkDuration();
    private string PendingAttachmentNames(int limit) => _pendingAttachmentNames(limit);
    private bool HasPendingAttachments() => _hasPendingAttachments();
    private Dispatcher Dispatcher => _dispatcher();
    private string RuntimeDisplay() => _runtimeDisplay();

    internal static string FormatDuration(double seconds)
    {
        if (seconds < 60)
        {
            return $"{Math.Max(0, (int)seconds)}s";
        }
        var minutes = (int)(seconds / 60);
        var remainder = (int)(seconds % 60);
        return remainder > 0 ? $"{minutes}m {remainder}s" : $"{minutes}m";
    }

    private static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private static string FormatTime(double seconds) => seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");

    internal void DisposeTerminal()
    {
        _terminalReadCts?.Cancel();
        _terminalSession?.Dispose();
        _terminalReadCts?.Dispose();
        _terminalReadCts = null;
        _terminalSession = null;
    }

    internal void PrimeGitStatus()
    {
        var count = GitDirtyCount();
        var gh = GitHubCliStatus();
        Dispatcher.Invoke(() =>
        {
            _cachedGitDirtyCount = count;
            _cachedGithubCliStatus = gh;
            RenderWorkbenchStatus(ActiveSession());
        });
    }

    internal void InvalidateWorkspaceContext()
    {
        _gitWorkspacePath = "";
        _lastKnownBranch = "";
        _gitChangesLoaded = false;
        _cachedGitDirtyCount = 0;
        _gitChanges.Clear();
        ResetTerminalSession("项目已切换，终端将在下一条命令前重载运行 Profile。");
        RenderWorkbenchStatus(ActiveSession());
        _ = RefreshGitChangesAsync();
    }

    private static PendingConfirmationInfo? PendingInfo(Dictionary<string, object?>? pending)
    {
        if (pending is null || !pending.TryGetValue("target", out var target) || target is null)
        {
            return null;
        }
        var operation = pending.TryGetValue("operation", out var op) ? op?.ToString() ?? "" : "";
        var risk = pending.TryGetValue("risk_level", out var riskValue) ? riskValue?.ToString() ?? "review" : "review";
        var createdAtText = pending.TryGetValue("created_at", out var createdAtValue) ? createdAtValue?.ToString() ?? "" : "";
        if (double.TryParse(createdAtText, out var createdAt) && createdAt > 0 && NowSeconds() - createdAt > 300)
        {
            return null;
        }
        return new PendingConfirmationInfo(target.ToString() ?? "", operation, risk);
    }

    private sealed record PendingConfirmationInfo(string Target, string Operation, string RiskLevel);
}
