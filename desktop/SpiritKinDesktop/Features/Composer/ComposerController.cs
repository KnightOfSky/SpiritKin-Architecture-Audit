using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal const string PermissionModeSetting = "composer_permission_mode";
    internal const string FullAccessGrantedSetting = "composer_full_access_granted";
    internal const string ModelIdSetting = "composer_model_id";
    internal const string ModelDisplaySetting = "composer_model_display";
    internal const string ModelProviderSetting = "composer_model_provider";
    internal const string ModelSourceSetting = "composer_model_source";
    internal const string ModelNameSetting = "composer_model_name";
    internal const string ReasoningEffortSetting = "composer_reasoning_effort";
    internal const string ProjectIdSetting = "composer_project_id";
    internal const string RuntimeModeSetting = "composer_runtime_mode";
    internal const string RuntimeDisplaySetting = "composer_runtime_display";
    internal const string BranchSetting = "composer_branch";
    internal const string PlanModeSetting = "composer_plan_mode";
    internal const string WebSearchModeSetting = "composer_web_search_mode";
    internal const string CollaborationModeSetting = "composer_collaboration_mode";
    internal const string PursueGoalSetting = "composer_pursue_goal";
    internal const string PursueGoalTextSetting = "composer_pursue_goal_text";
    internal const string PlanSummarySetting = "composer_plan_summary";
    internal const string PursueGoalStatusSetting = "composer_pursue_goal_status";
    internal const string PursueGoalProgressSetting = "composer_pursue_goal_progress";
    internal const string PursueGoalNextActionSetting = "composer_pursue_goal_next_action";
    internal const string PursueGoalTurnCountSetting = "composer_pursue_goal_turn_count";
    internal const string AssistantWorkStartedAtSetting = "assistant_work_started_at";
    internal const string AssistantWorkDurationSetting = "assistant_work_duration_seconds";
    internal const string AssistantWorkLabelSetting = "assistant_work_label";
    internal const string AssistantLastSteerSetting = "assistant_last_steer";
    internal const string AssistantCommandCountSetting = "assistant_command_count";
    internal const string AssistantDirtyCountStartSetting = "assistant_dirty_count_start";
    internal const string AssistantWorkMessageIdSetting = "assistant_work_message_id";
    internal const string DesktopTtsEnabledSetting = "desktop_tts_enabled";

    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly string _rootDir;
    private readonly Func<DesktopState> _getState;
    private readonly Action _renderState;
    private readonly Func<Task> _saveStateAsync;
    private readonly Func<DesktopSession> _activeSession;
    private readonly Func<DesktopMessage> _addWorkMessage;
    private readonly Func<ProjectRuntimeProfile> _activeProjectRuntimeProfile;
    private readonly Func<string> _currentSessionCollaborationThreadId;
    private readonly Func<bool> _projectCollaborationMessagesIntoActiveSessionFromCache;
    private readonly Action<DesktopSession> _renderActiveMessages;
    private readonly Action<DesktopSession> _syncQuickChatLayout;
    private readonly Action<string, string?> _openManagementPage;
    private readonly Func<string, string, string, bool> _confirmAction;
    private readonly Func<string, string, string, string?> _promptText;
    private readonly Action<HttpRequestMessage> _applyAuth;
    private readonly Func<string> _apiBase;
    private readonly Action _launchLocalEdgeBrowser;
    private readonly Func<bool> _getCollaborationChatActive;
    private readonly Action<bool> _setCollaborationChatActive;
    private readonly Action<bool> _setQuickChatMode;
    private readonly Action<string> _setActiveCollaborationThreadId;
    private readonly Action _clearCollaborationChatSignature;
    private readonly Action _clearCollaborationContextSignature;
    private readonly Action _clearCollaborationThreadSignature;
    private readonly Func<int> _getCachedGitDirtyCount;
    private readonly Func<IEnumerable<GitChangeViewModel>> _gitChanges;
    private readonly Func<string, GitCommandResult> _runGit;
    private readonly Func<bool, string> _currentGitBranch;
    private readonly Func<List<string>> _gitBranches;
    private readonly Func<int> _gitDirtyCount;
    private readonly Action<string> _setLastKnownBranch;
    private readonly Func<ContextMenu, string, RoutedEventHandler, MenuItem> _addContextMenuItem;
    private readonly Func<Separator> _createStyledSeparator;
    private readonly Action<ContextMenu> _applyMenuStyle;
    private readonly Action<ContextMenu, string> _addDisabledMenuHeader;
    private readonly Action<string> _setConnectionStatus;
    private readonly Action<string> _selectProjectInSidebar;
    private readonly Func<DesktopItem?, bool> _isArchived;
    private readonly IReadOnlyCollection<AssistModelViewModel> _assistModels;
    private readonly Func<IEnumerable<AgentViewModel>> _agents;
    private readonly Func<IEnumerable<CollaborationParticipantOption>> _collaborationParticipantOptions;
    private readonly ObservableCollection<ComposerAttachmentViewModel> _composerAttachments;
    private readonly HashSet<string> _expandedProjectIds;
    private readonly DispatcherTimer _assistantWorkTimer;

    private readonly List<ComposerAttachment> _pendingAttachments = new();
    private readonly List<ComposerDocumentPreview> _pendingAttachmentDocuments = new();
    private int _lastCollaborationMentionTriggerIndex = -1;

    public ComposerController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        HttpClient http,
        JsonSerializerOptions jsonOptions,
        string rootDir,
        Func<DesktopState> getState,
        Action renderState,
        Func<Task> saveStateAsync,
        Func<DesktopSession> activeSession,
        Func<DesktopMessage> addWorkMessage,
        Func<ProjectRuntimeProfile> activeProjectRuntimeProfile,
        Func<string> currentSessionCollaborationThreadId,
        Func<bool> projectCollaborationMessagesIntoActiveSessionFromCache,
        Action<DesktopSession> renderActiveMessages,
        Action<DesktopSession> syncQuickChatLayout,
        Action<string, string?> openManagementPage,
        Func<string, string, string, bool> confirmAction,
        Func<string, string, string, string?> promptText,
        Action<HttpRequestMessage> applyAuth,
        Func<string> apiBase,
        Action launchLocalEdgeBrowser,
        Func<bool> getCollaborationChatActive,
        Action<bool> setCollaborationChatActive,
        Action<bool> setQuickChatMode,
        Action<string> setActiveCollaborationThreadId,
        Action clearCollaborationChatSignature,
        Action clearCollaborationContextSignature,
        Action clearCollaborationThreadSignature,
        Func<int> getCachedGitDirtyCount,
        Func<IEnumerable<GitChangeViewModel>> gitChanges,
        Func<string, GitCommandResult> runGit,
        Func<bool, string> currentGitBranch,
        Func<List<string>> gitBranches,
        Func<int> gitDirtyCount,
        Action<string> setLastKnownBranch,
        Func<ContextMenu, string, RoutedEventHandler, MenuItem> addContextMenuItem,
        Func<Separator> createStyledSeparator,
        Action<ContextMenu> applyMenuStyle,
        Action<ContextMenu, string> addDisabledMenuHeader,
        Action<string> setConnectionStatus,
        Action<string> selectProjectInSidebar,
        Func<DesktopItem?, bool> isArchived,
        IReadOnlyCollection<AssistModelViewModel> assistModels,
        Func<IEnumerable<AgentViewModel>> agents,
        Func<IEnumerable<CollaborationParticipantOption>> collaborationParticipantOptions,
        ObservableCollection<ComposerAttachmentViewModel> composerAttachments,
        HashSet<string> expandedProjectIds,
        DispatcherTimer assistantWorkTimer)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        _http = http;
        _jsonOptions = jsonOptions;
        _rootDir = rootDir;
        _getState = getState;
        _renderState = renderState;
        _saveStateAsync = saveStateAsync;
        _activeSession = activeSession;
        _addWorkMessage = addWorkMessage;
        _activeProjectRuntimeProfile = activeProjectRuntimeProfile;
        _currentSessionCollaborationThreadId = currentSessionCollaborationThreadId;
        _projectCollaborationMessagesIntoActiveSessionFromCache = projectCollaborationMessagesIntoActiveSessionFromCache;
        _renderActiveMessages = renderActiveMessages;
        _syncQuickChatLayout = syncQuickChatLayout;
        _openManagementPage = openManagementPage;
        _confirmAction = confirmAction;
        _promptText = promptText;
        _applyAuth = applyAuth;
        _apiBase = apiBase;
        _launchLocalEdgeBrowser = launchLocalEdgeBrowser;
        _getCollaborationChatActive = getCollaborationChatActive;
        _setCollaborationChatActive = setCollaborationChatActive;
        _setQuickChatMode = setQuickChatMode;
        _setActiveCollaborationThreadId = setActiveCollaborationThreadId;
        _clearCollaborationChatSignature = clearCollaborationChatSignature;
        _clearCollaborationContextSignature = clearCollaborationContextSignature;
        _clearCollaborationThreadSignature = clearCollaborationThreadSignature;
        _getCachedGitDirtyCount = getCachedGitDirtyCount;
        _gitChanges = gitChanges;
        _runGit = runGit;
        _currentGitBranch = currentGitBranch;
        _gitBranches = gitBranches;
        _gitDirtyCount = gitDirtyCount;
        _setLastKnownBranch = setLastKnownBranch;
        _addContextMenuItem = addContextMenuItem;
        _createStyledSeparator = createStyledSeparator;
        _applyMenuStyle = applyMenuStyle;
        _addDisabledMenuHeader = addDisabledMenuHeader;
        _setConnectionStatus = setConnectionStatus;
        _selectProjectInSidebar = selectProjectInSidebar;
        _isArchived = isArchived;
        _assistModels = assistModels;
        _agents = agents;
        _collaborationParticipantOptions = collaborationParticipantOptions;
        _composerAttachments = composerAttachments;
        _expandedProjectIds = expandedProjectIds;
        _assistantWorkTimer = assistantWorkTimer;
    }

    internal bool CollaborationChatActive => _getCollaborationChatActive();
    private DesktopState State => _getState();
    private void RenderState() => _renderState();
    private Task SaveStateAsync() => _saveStateAsync();
    private DesktopSession ActiveSession() => _activeSession();
    private DesktopMessage AddWorkMessage() => _addWorkMessage();
    private ProjectRuntimeProfile ActiveProjectRuntimeProfile() => _activeProjectRuntimeProfile();
    private string CurrentSessionCollaborationThreadId() => _currentSessionCollaborationThreadId();
    private bool ProjectCollaborationMessagesIntoActiveSessionFromCache() => _projectCollaborationMessagesIntoActiveSessionFromCache();
    private void RenderActiveMessages(DesktopSession active) => _renderActiveMessages(active);
    private void SyncQuickChatLayout(DesktopSession active) => _syncQuickChatLayout(active);
    private void OpenManagementPage(string module, string? subPage = null) => _openManagementPage(module, subPage);
    private bool ConfirmAction(string title, string message, string confirmText = "确定") => _confirmAction(title, message, confirmText);
    private string? PromptText(string title, string label, string initial = "") => _promptText(title, label, initial);
    private void ApplyAuth(HttpRequestMessage request) => _applyAuth(request);
    private string ApiBase() => _apiBase();
    private void LaunchLocalEdgeBrowser() => _launchLocalEdgeBrowser();
    private void SetCollaborationChatActive(bool value) => _setCollaborationChatActive(value);
    private void SetQuickChatMode(bool value) => _setQuickChatMode(value);
    private void SetActiveCollaborationThreadId(string threadId) => _setActiveCollaborationThreadId(threadId);
    private void ClearCollaborationSignatures()
    {
        _clearCollaborationChatSignature();
        _clearCollaborationContextSignature();
        _clearCollaborationThreadSignature();
    }
    private int CachedGitDirtyCount => _getCachedGitDirtyCount();
    private IEnumerable<GitChangeViewModel> GitChanges => _gitChanges();
    private GitCommandResult RunGit(string arguments) => _runGit(arguments);
    private string CurrentGitBranch(bool refresh) => _currentGitBranch(refresh);
    private List<string> GitBranches() => _gitBranches();
    private int GitDirtyCount() => _gitDirtyCount();
    private void SetLastKnownBranch(string branch) => _setLastKnownBranch(branch);
    private MenuItem AddContextMenuItem(ContextMenu menu, string header, RoutedEventHandler click) => _addContextMenuItem(menu, header, click);
    private Separator CreateStyledSeparator() => _createStyledSeparator();
    private void ApplyMenuStyle(ContextMenu menu) => _applyMenuStyle(menu);
    private void AddDisabledMenuHeader(ContextMenu menu, string header) => _addDisabledMenuHeader(menu, header);
    private void SetConnectionStatus(string text) => _setConnectionStatus(text);
    private void SelectProjectInSidebar(string projectId) => _selectProjectInSidebar(projectId);
    private bool IsArchived(string status) => _isArchived(new DesktopItem { Status = status });

    private static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private static string NewId(string prefix) => $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];
    private static string QuoteArg(string value) => $"\"{value.Replace("\"", "\\\"")}\"";

    internal bool HasPendingAttachments => _pendingAttachments.Count > 0;
    internal string PendingAttachmentNames(int limit = 5) =>
        string.Join(", ", _pendingAttachments.Select(item => item.Name).Take(limit));
    internal string[] PendingAttachmentDisplayPaths(int limit = 8) =>
        _pendingAttachments
            .Select(item => !string.IsNullOrWhiteSpace(item.LocalPath)
                ? item.LocalPath
                : (!string.IsNullOrWhiteSpace(item.RelativePath) ? item.RelativePath : item.Name))
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Take(limit)
            .ToArray();
    internal double AssistantWorkStartedAt => GetSettingDouble(AssistantWorkStartedAtSetting);
    internal double AssistantWorkDuration => GetSettingDouble(AssistantWorkDurationSetting);
    internal string RuntimeDisplay => GetSettingString(RuntimeDisplaySetting, "Work locally");
    internal bool DesktopTtsEnabled()
    {
        var configured = GetSettingString(DesktopTtsEnabledSetting);
        return string.IsNullOrWhiteSpace(configured) || GetSettingBool(DesktopTtsEnabledSetting);
    }
    internal bool DesktopTtsSettingIsEmpty => string.IsNullOrWhiteSpace(GetSettingString(DesktopTtsEnabledSetting));
    internal void SetDesktopTtsEnabled(bool enabled) => SetSetting(DesktopTtsEnabledSetting, enabled);
    internal void IncrementAssistantCommandCount() =>
        SetSetting(AssistantCommandCountSetting, GetSettingDouble(AssistantCommandCountSetting) + 1);
    internal void SetPlanSummary(string summary) => SetSetting(PlanSummarySetting, summary);
    internal void SetPursueGoalText(string goalText) => SetSetting(PursueGoalTextSetting, goalText);
    internal void SetPursueGoalStatus(string value) => SetSetting(PursueGoalStatusSetting, value);
    internal void SetPursueGoalProgress(string value) => SetSetting(PursueGoalProgressSetting, value);
    internal void SetPursueGoalNextAction(string value) => SetSetting(PursueGoalNextActionSetting, value);
    internal void SetPursueGoalTurnCount(string value) => SetSetting(PursueGoalTurnCountSetting, value);
    internal void SetPursueGoalEnabled(bool enabled) => SetSetting(PursueGoalSetting, enabled);
    internal bool PursueGoalEnabled => GetSettingBool(PursueGoalSetting);
    internal string PursueGoalText => GetSettingString(PursueGoalTextSetting);
    internal string SelectedModelId => GetSettingString(ModelIdSetting);

    internal static string ReadJsonElementString(JsonElement element, string fallback = "")
    {
        return element.ValueKind switch
        {
            JsonValueKind.String => string.IsNullOrWhiteSpace(element.GetString()) ? fallback : element.GetString()!,
            JsonValueKind.Number => element.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => fallback,
            _ => element.GetRawText(),
        };
    }

    internal static string ReadJsonString(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return ReadJsonElementString(value);
    }

    internal static string ReadJsonString(JsonElement element, string key, string fallback)
    {
        var value = ReadJsonString(element, key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value;
    }

    internal static long ReadJsonLong(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number))
        {
            return number;
        }
        return long.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    internal static bool ReadJsonBool(JsonElement element, string key, bool fallback = false)
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

    internal static string[] ReadJsonStringArray(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return value.EnumerateArray()
            .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    internal sealed record ComposerModel(string Id, string Display, string Provider, string Source, string ModelName);
    private sealed record ComposerAttachment(string FileId, string Name, string MimeType, string Uri, long SizeBytes, string Purpose, string RelativePath, string LocalPath);
    private sealed record ComposerDocumentPreview(string Path, string TextPreview);
}
