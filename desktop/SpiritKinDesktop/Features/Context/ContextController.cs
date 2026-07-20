using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class ContextController
{
    internal const string CollaborationChatsScopeId = "__chats__";
    internal const string CollaborationChatSessionId = "virtual_collaboration_chat";

    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly WorkspaceController _workspaceController;
    private readonly string _rootDir;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly Func<DesktopState> _getState;
    private readonly Action _renderState;
    private readonly Func<Task> _saveStateAsync;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Action<DesktopSession> _renderActiveMessages;
    private readonly Action<DesktopSession> _syncQuickChatLayout;
    private readonly Action _scrollMessagesToEnd;
    private readonly Func<string, string, string, string?> _promptText;
    private readonly Func<string, string, string, bool> _confirmAction;
    private readonly ObservableCollection<ChatTimelineItemViewModel> _messages;
    private readonly Dispatcher _dispatcher;

    private Func<ComposerController> _composerController = () => throw new InvalidOperationException("Composer controller has not been attached.");

    internal readonly ObservableCollection<ActionItemViewModel> ContextSuggestions = new();
    internal readonly ObservableCollection<ChangeViewModel> OverviewChanges = new();
    internal readonly ObservableCollection<ActionItemViewModel> CollaborationTasks = new();
    internal readonly ObservableCollection<ActionItemViewModel> CollaborationMessages = new();
    internal readonly ObservableCollection<QuickCommandViewModel> CollaborationContextOptions = new();
    internal readonly ObservableCollection<QuickCommandViewModel> CollaborationProjectScopes = new();
    internal readonly ObservableCollection<QuickCommandViewModel> CollaborationSessionScopes = new();
    internal readonly ObservableCollection<SessionViewModel> CollaborationThreads = new();
    internal readonly ObservableCollection<ActionItemViewModel> CollaborationClaims = new();
    internal readonly ObservableCollection<ActionItemViewModel> CollaborationDecisions = new();
    internal readonly ObservableCollection<ActionItemViewModel> CollaborationReviews = new();

    private readonly DispatcherTimer _collaborationSyncTimer = new() { Interval = TimeSpan.FromSeconds(3) };
    private bool _collaborationSyncInFlight;
    private bool _collaborationChatActive;
    private bool _collaborationCollapsed;
    private bool _syncingCollaborationContextSelection;
    private bool _syncingCollaborationThreadSelection;
    private readonly Dictionary<string, string> _collaborationThreadStatuses = new(StringComparer.OrdinalIgnoreCase);
    // 协作工作链缓存：thread → (agent → 按轮次排列的 work 卡列表)。每个参与模型每一轮一张独立卡。
    private readonly Dictionary<string, Dictionary<string, List<DesktopMessage>>> _collaborationWorkChains = new(StringComparer.OrdinalIgnoreCase);
    // thread|agent → 当前轮次键（被处理来件的 message_id）与已分配的轮次序号。
    private readonly Dictionary<string, string> _collaborationWorkChainRounds = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, int> _collaborationWorkChainRoundSeq = new(StringComparer.OrdinalIgnoreCase);
    // 回复先落、工作卡后到时的待配对登记：预期投影卡 Id → 回复 DesktopMessage Id。
    // 卡片迟到时据此锚到自己回复正上方，而不是时间线末尾（否则卡与回复分离）。
    private readonly Dictionary<string, string> _collaborationPendingReplyAnchors = new(StringComparer.OrdinalIgnoreCase);
    // 流式 token 草稿：thread|agent|parent_message_id → draft DesktopMessage Id。正式 reply 落库后原地替换。
    private readonly Dictionary<string, string> _collaborationStreamingDrafts = new(StringComparer.OrdinalIgnoreCase);
    // 因优先处理新会话而被人工暂停的旧协作 thread（turn guard pause_turns）。新会话完结后提示恢复。
    private readonly HashSet<string> _pausedCollaborationThreads = new(StringComparer.OrdinalIgnoreCase);
    // 用户拒绝暂停后的会话级抑制：同一会话内不再重复弹询问（值 = thread Id）。
    private readonly HashSet<string> _pausePromptDeclinedThreads = new(StringComparer.OrdinalIgnoreCase);
    // 恢复提示防重入：finished 事件可能连发多条，弹一次就够。
    private bool _resumePromptInFlight;
    // 修P：新会话暂停旧会话后，本会话仍待完工的参与模型集合（thread → 未完工 agentId）。
    // 只有全部参与模型都收到完成信号（集合清空）才弹"恢复旧会话"提示——
    // 否则一个模型完工、另一个还在轮空隙就误判全部完成，提前弹窗。
    private readonly Dictionary<string, HashSet<string>> _collaborationPendingResumeAgents = new(StringComparer.OrdinalIgnoreCase);
    // 协作投影合帧：worker 流式事件峰值 10 条/秒，逐条全量投影+渲染会打满 UI 线程。
    // 事件处理只把 thread 标脏，200ms 定时器统一投影渲染一帧。
    private readonly HashSet<string> _collaborationDirtyThreads = new(StringComparer.OrdinalIgnoreCase);
    private readonly DispatcherTimer _collaborationProjectionTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private string _activeCollaborationThreadId = "";
    private string _pendingCollaborationToolCallId = "";
    private string _pendingCollaborationToolTarget = "";
    private string _pendingCollaborationToolOperation = "";
    private string _lastCollaborationStateSignature = "";
    private string _lastCollaborationChatSignature = "";
    private string _lastCollaborationContextSignature = "";
    private string _lastCollaborationScopeSignature = "";
    private string _lastCollaborationThreadSignature = "";
    private readonly List<CollaborationParticipantOption> _collaborationParticipantOptions = new();
    private readonly Dictionary<string, string> _collaborationParticipantAliases = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, Process> _collaborationWorkerProcesses = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, string> _collaborationWorkerScopes = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _collaborationWorkerLock = new();

    internal ContextController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        WorkspaceController workspaceController,
        string rootDir,
        JsonSerializerOptions jsonOptions,
        Func<DesktopState> getState,
        Action renderState,
        Func<Task> saveStateAsync,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Action<DesktopSession> renderActiveMessages,
        Action<DesktopSession> syncQuickChatLayout,
        Action scrollMessagesToEnd,
        Func<string, string, string, string?> promptText,
        Func<string, string, string, bool> confirmAction,
        ObservableCollection<ChatTimelineItemViewModel> messages,
        Dispatcher dispatcher)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        _workspaceController = workspaceController;
        _rootDir = rootDir;
        _jsonOptions = jsonOptions;
        _getState = getState;
        _renderState = renderState;
        _saveStateAsync = saveStateAsync;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _renderActiveMessages = renderActiveMessages;
        _syncQuickChatLayout = syncQuickChatLayout;
        _scrollMessagesToEnd = scrollMessagesToEnd;
        _promptText = promptText;
        _confirmAction = confirmAction;
        _messages = messages;
        _dispatcher = dispatcher;
        _collaborationSyncTimer.Tick += async (_, _) => await TickCollaborationSyncTimerAsync();
        _collaborationProjectionTimer.Tick += (_, _) => FlushCollaborationProjection();
    }

    internal bool CollaborationChatActive => _collaborationChatActive;
    internal bool CollaborationCollapsed => _collaborationCollapsed;
    internal bool SyncingCollaborationContextSelection => _syncingCollaborationContextSelection;
    internal bool SyncingCollaborationThreadSelection => _syncingCollaborationThreadSelection;
    internal IReadOnlyCollection<CollaborationParticipantOption> CollaborationParticipantOptions => _collaborationParticipantOptions;

    internal void SetComposerController(Func<ComposerController> composerController) => _composerController = composerController;
    internal void SetCollaborationChatActive(bool value) => _collaborationChatActive = value;
    internal void ClearActiveCollaborationThread() => _activeCollaborationThreadId = "";
    internal void ClearCollaborationChatSignature() => _lastCollaborationChatSignature = "";
    internal void ClearCollaborationContextSignature() => _lastCollaborationContextSignature = "";
    internal void ClearCollaborationThreadSignature() => _lastCollaborationThreadSignature = "";
    internal void ClearCollaborationScopeSignature() => _lastCollaborationScopeSignature = "";
    internal void MarkCollaborationThreadsDeletedLocally(IEnumerable<string> threadIds)
    {
        var changed = false;
        foreach (var threadId in threadIds)
        {
            if (string.IsNullOrWhiteSpace(threadId))
            {
                continue;
            }
            _collaborationThreadStatuses[threadId.Trim()] = "deleted";
            changed = true;
        }
        if (!changed)
        {
            return;
        }
        ClearCollaborationThreadSignature();
        ClearCollaborationChatSignature();
        ClearCollaborationContextSignature();
        RenderCollaborationThreads();
        RenderCollaborationChatMessagesIfChanged(force: true);
    }

    internal void StartSyncTimer() => _collaborationSyncTimer.Start();
    internal void StopSyncTimer()
    {
        _collaborationSyncTimer.Stop();
        _collaborationProjectionTimer.Stop();
        StopCollaborationWorkers(killOnly: true);
    }

    internal void ToggleCollaborationCollapsed()
    {
        _collaborationCollapsed = !_collaborationCollapsed;
        WorkspaceSidebar.CollaborationBodyPanel.Visibility = _collaborationCollapsed ? Visibility.Collapsed : Visibility.Visible;
        WorkspaceSidebar.ToggleCollaborationButton.Content = _collaborationCollapsed ? "›" : "⌄";
    }

    internal void RenderCollaborationChatSidebarEntry()
    {
        var openCount = CollaborationMessages.Count(item => item.Type.StartsWith("open", StringComparison.OrdinalIgnoreCase) && !IsHumanCollaborationMessage(item));
        var threadCount = Math.Max(CollaborationThreads.Count, 1);
        WorkspaceSidebar.CollaborationChatSummaryText.Text = $"{threadCount} 个协作线程 · {CollaborationMessages.Count} 条";
        WorkspaceSidebar.CollaborationChatUnreadText.Text = openCount > 0 ? $"{openCount} 未读" : "";
    }

    internal void RenderCollaborationComposerState()
    {
        ChatWorkspace.CollaborationComposerPanel.Visibility = Visibility.Collapsed;
        ChatWorkspace.AttachButton.Visibility = Visibility.Visible;
        ChatWorkspace.AgentMentionButton.Visibility = Visibility.Visible;
        ChatWorkspace.PermissionButton.Visibility = Visibility.Visible;
        ChatWorkspace.QuickCommandBox.Visibility = Visibility.Visible;
        ChatWorkspace.ManageQuickCommandsButton.Visibility = Visibility.Collapsed;
        if (_collaborationChatActive)
        {
            EnsureComboText(ChatWorkspace.CollaborationComposerFromBox, "human_desktop");
            EnsureComboText(ChatWorkspace.CollaborationComposerToBox, "all");
            EnsureComboText(ChatWorkspace.CollaborationComposerRoleBox, "question");
            ChatWorkspace.PromptBox.ToolTip = "模型协作已开启，可直接输入 @ClaudeCode、@Codex 或 @all";
        }
        else
        {
            ChatWorkspace.PromptBox.ToolTip = null;
        }
    }

    private DesktopState _state => _getState();
    private ComposerController _composer => _composerController();
    private Dispatcher Dispatcher => _dispatcher;
    private void RenderState() => _renderState();
    private Task SaveStateAsync() => _saveStateAsync();
    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private void RenderActiveMessages(DesktopSession active) => _renderActiveMessages(active);
    private void SyncQuickChatLayout(DesktopSession active) => _syncQuickChatLayout(active);
    private void ScrollMessagesToEnd() => _scrollMessagesToEnd();
    private string? PromptText(string title, string label, string initial = "") => _promptText(title, label, initial);
    private bool ConfirmAction(string title, string message, string confirmText = "确定") => _confirmAction(title, message, confirmText);

    private static string ReadJsonString(JsonElement element, string key) => JsonResponseHelpers.ReadJsonString(element, key);
    private static string ReadJsonString(JsonElement element, string key, string fallback) => JsonResponseHelpers.ReadJsonString(element, key, fallback);
    private static int ReadJsonInt(JsonElement element, string key) => JsonResponseHelpers.ReadJsonInt(element, key);
    private static bool ReadJsonBool(JsonElement element, string key, bool fallback = false) => JsonResponseHelpers.ReadJsonBool(element, key, fallback);
    private static string FormatTimeFromDouble(string raw) => JsonResponseHelpers.FormatTimeFromDouble(raw);
    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);
    private static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();

    private static string[] ReadJsonStringArray(JsonElement element, string key)
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

    private static string ComboText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return ComboBoxItemValue(item);
        }
        if (combo.SelectedValue is string selectedValue && !string.IsNullOrWhiteSpace(selectedValue))
        {
            return selectedValue;
        }
        return combo.Text;
    }

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

    private static void EnsureComboText(ComboBox combo, string fallback)
    {
        if (string.IsNullOrWhiteSpace(ComboText(combo)))
        {
            SetComboText(combo, fallback);
        }
    }

    private static void SyncEditableComboSelectionText(ComboBox combo)
    {
        if (combo.IsEditable && combo.SelectedItem is ComboBoxItem item)
        {
            combo.Text = Convert.ToString(item.Content) ?? "";
        }
    }

    private static string ComboBoxItemValue(ComboBoxItem item)
    {
        var tag = Convert.ToString(item.Tag);
        return string.IsNullOrWhiteSpace(tag) ? Convert.ToString(item.Content) ?? "" : tag;
    }

    private static string[] LinesFromText(string text) =>
        text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private static string SafeFileName(string value)
    {
        var safe = new string((value ?? "").Select(ch => char.IsLetterOrDigit(ch) || ch is '-' or '_' ? ch : '_').ToArray());
        return string.IsNullOrWhiteSpace(safe) ? "item" : safe;
    }

    private static string NewId(string prefix) => $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];

    private static string FormatTime(double seconds)
    {
        try
        {
            return DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g", CultureInfo.CurrentCulture);
        }
        catch
        {
            return "--";
        }
    }
}
