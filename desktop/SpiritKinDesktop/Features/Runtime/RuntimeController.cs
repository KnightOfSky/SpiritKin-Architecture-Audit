using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal event Action<RuntimeEvent>? EventApplied;
    internal event Action<string, bool>? DesktopSpeechActivityChanged;

    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly WindowTitleBar TitleBar;
    private readonly HttpClient _http;
    private readonly DesktopApiClient _desktopApi;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly string _rootDir;
    private readonly Func<DesktopState> _getState;
    private readonly Action<DesktopState> _setState;
    private readonly Func<bool> _isRendering;
    private readonly Action<bool> _setRendering;
    private readonly Func<string> _latestCommandRequestIdAccessor;
    private readonly Action<string> _setLatestCommandRequestId;
    private readonly Func<WorkspaceController> _workspaceController;
    private readonly Func<SafetyController> _safetyController;
    private readonly Func<ModuleManagementController> _moduleManagementController;
    private readonly Func<EvolutionController> _evolutionController;
    private readonly Func<WorkflowController> _workflowController;
    private readonly Func<LearningController> _learningController;
    private readonly Func<MobileManagementController> _mobileManagementController;
    private readonly Func<NavigationController> _navigationController;
    private readonly Func<ContextController> _contextController;
    private readonly Func<WorkbenchController> _workbenchController;
    private readonly Func<ServicesController> _servicesController;
    private readonly Func<AgentsController> _agentsController;
    private readonly Func<ComposerController> _composerController;
    private readonly Func<Task> _loadSkillsAsync;
    private readonly ObservableCollection<SessionViewModel> _sessions;
    private readonly ObservableCollection<ProjectViewModel> _projects;
    private readonly ObservableCollection<ProjectViewModel> _managedSessions;
    private readonly ObservableCollection<ProjectViewModel> _managedProjects;
    private readonly ObservableCollection<ProjectViewModel> _managedProjectSessions;
    private readonly ObservableCollection<TaskViewModel> _tasks;
    private readonly ObservableCollection<ChatTimelineItemViewModel> _messages;
    private readonly ObservableCollection<EventViewModel> _events;
    private readonly ObservableCollection<EventViewModel> _traceEvents;
    private readonly ObservableCollection<EventViewModel> _syncClients;
    private readonly ObservableCollection<EventViewModel> _diagnosticChecks;
    private readonly ObservableCollection<ActionItemViewModel> _diagnosticIssues;
    private readonly ObservableCollection<LogViewModel> _logs;
    private readonly ObservableCollection<EventViewModel> _actionLogEvents;
    private readonly ObservableCollection<ActionItemViewModel> _dailyItems;
    private readonly ObservableCollection<QuickCommandViewModel> _quickCommands;
    private readonly Dictionary<string, string> _logPaths;
    private readonly HashSet<string> _pendingDeletedSessionIds;
    private readonly HashSet<string> _pendingDeletedProjectIds;
    private readonly HashSet<string> _pendingDeletedTaskIds;
    private readonly Dictionary<string, HashSet<string>> _pendingDeletedMessageIds;
    private readonly HashSet<string> _expandedProjectIds;
    private readonly Dispatcher _dispatcher;
    private readonly SemaphoreSlim _saveStateLock = new(1, 1);
    private int _pendingSaveRequests;
    private readonly Dictionary<string, double> _recentAssistantEventKeys = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _desktopTtsLock = new();
    private bool _messageScrollPending;
    private bool _messageScrollRearm;
    private bool _messageScrollViewerHooked;
    private bool _messageAutoScrollSticky = true;
    private string _editingMessageId = "";
    private CancellationTokenSource? _commandSendCts;
    private long _commandSendSequence;
    // request_id → 发起会话 Id：主聊天回复到达时写回原会话，用户中途切换会话也不串窗。
    private readonly Dictionary<string, string> _commandRequestSessionIds = new(StringComparer.OrdinalIgnoreCase);
    // request_id → 主 Agent 流式草稿消息 Id。草稿只存在于本地内存，最终 assistant.message 原位定稿。
    private readonly Dictionary<string, string> _assistantStreamDraftMessageIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _completedAssistantStreamRequestIds = new(StringComparer.OrdinalIgnoreCase);
    private Process? _desktopTtsProcess;
    private string _lastDesktopTtsText = "";
    private DateTime _lastDesktopTtsAt = DateTime.MinValue;
    private PendingConfirmationInfo? _consumedPendingConfirmation;
    private double _consumedPendingConfirmationAt;
    private string _consumedPendingConfirmationRequestId = "";
    private bool _confirmationChoiceInFlight;
    private const double ConfirmationChoiceInFlightSuppressionSeconds = 600;
    private const double ConfirmationChoiceCompletedSuppressionSeconds = 20;

    internal RuntimeController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        WindowTitleBar titleBar,
        HttpClient http,
        DesktopApiClient desktopApi,
        JsonSerializerOptions jsonOptions,
        string rootDir,
        Func<DesktopState> getState,
        Action<DesktopState> setState,
        Func<bool> isRendering,
        Action<bool> setRendering,
        Func<string> latestCommandRequestIdAccessor,
        Action<string> setLatestCommandRequestId,
        Func<WorkspaceController> workspaceController,
        Func<SafetyController> safetyController,
        Func<ModuleManagementController> moduleManagementController,
        Func<EvolutionController> evolutionController,
        Func<WorkflowController> workflowController,
        Func<LearningController> learningController,
        Func<MobileManagementController> mobileManagementController,
        Func<NavigationController> navigationController,
        Func<ContextController> contextController,
        Func<WorkbenchController> workbenchController,
        Func<ServicesController> servicesController,
        Func<AgentsController> agentsController,
        Func<ComposerController> composerController,
        Func<Task> loadSkillsAsync,
        ObservableCollection<SessionViewModel> sessions,
        ObservableCollection<ProjectViewModel> projects,
        ObservableCollection<ProjectViewModel> managedSessions,
        ObservableCollection<ProjectViewModel> managedProjects,
        ObservableCollection<ProjectViewModel> managedProjectSessions,
        ObservableCollection<TaskViewModel> tasks,
        ObservableCollection<ChatTimelineItemViewModel> messages,
        ObservableCollection<EventViewModel> events,
        ObservableCollection<EventViewModel> traceEvents,
        ObservableCollection<EventViewModel> syncClients,
        ObservableCollection<EventViewModel> diagnosticChecks,
        ObservableCollection<ActionItemViewModel> diagnosticIssues,
        ObservableCollection<LogViewModel> logs,
        ObservableCollection<EventViewModel> actionLogEvents,
        ObservableCollection<ActionItemViewModel> dailyItems,
        ObservableCollection<QuickCommandViewModel> quickCommands,
        Dictionary<string, string> logPaths,
        HashSet<string> pendingDeletedSessionIds,
        HashSet<string> pendingDeletedProjectIds,
        HashSet<string> pendingDeletedTaskIds,
        Dictionary<string, HashSet<string>> pendingDeletedMessageIds,
        HashSet<string> expandedProjectIds,
        Dispatcher dispatcher)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        TitleBar = titleBar;
        _http = http;
        _desktopApi = desktopApi;
        _jsonOptions = jsonOptions;
        _rootDir = rootDir;
        _getState = getState;
        _setState = setState;
        _isRendering = isRendering;
        _setRendering = setRendering;
        _latestCommandRequestIdAccessor = latestCommandRequestIdAccessor;
        _setLatestCommandRequestId = setLatestCommandRequestId;
        _workspaceController = workspaceController;
        _safetyController = safetyController;
        _moduleManagementController = moduleManagementController;
        _evolutionController = evolutionController;
        _workflowController = workflowController;
        _learningController = learningController;
        _mobileManagementController = mobileManagementController;
        _navigationController = navigationController;
        _contextController = contextController;
        _workbenchController = workbenchController;
        _servicesController = servicesController;
        _agentsController = agentsController;
        _composerController = composerController;
        _loadSkillsAsync = loadSkillsAsync;
        _sessions = sessions;
        _projects = projects;
        _managedSessions = managedSessions;
        _managedProjects = managedProjects;
        _managedProjectSessions = managedProjectSessions;
        _tasks = tasks;
        _messages = messages;
        _events = events;
        _traceEvents = traceEvents;
        _syncClients = syncClients;
        _diagnosticChecks = diagnosticChecks;
        _diagnosticIssues = diagnosticIssues;
        _logs = logs;
        _actionLogEvents = actionLogEvents;
        _dailyItems = dailyItems;
        _quickCommands = quickCommands;
        _logPaths = logPaths;
        _pendingDeletedSessionIds = pendingDeletedSessionIds;
        _pendingDeletedProjectIds = pendingDeletedProjectIds;
        _pendingDeletedTaskIds = pendingDeletedTaskIds;
        _pendingDeletedMessageIds = pendingDeletedMessageIds;
        _expandedProjectIds = expandedProjectIds;
        _dispatcher = dispatcher;
    }

    private DesktopState _state
    {
        get => _getState();
        set => _setState(value);
    }

    private bool _rendering
    {
        get => _isRendering();
        set => _setRendering(value);
    }

    private string _latestCommandRequestId
    {
        get => _latestCommandRequestIdAccessor();
        set => _setLatestCommandRequestId(value);
    }

    private WorkspaceController _workspaceControllerValue => _workspaceController();
    private SafetyController _safetyControllerValue => _safetyController();
    private ModuleManagementController _moduleManagementControllerValue => _moduleManagementController();
    private EvolutionController _evolutionControllerValue => _evolutionController();
    private WorkflowController _workflowControllerValue => _workflowController();
    private LearningController _learningControllerValue => _learningController();
    private MobileManagementController _mobileManagementControllerValue => _mobileManagementController();
    private NavigationController _navigationControllerValue => _navigationController();
    private ContextController _contextControllerValue => _contextController();
    private WorkbenchController _workbenchControllerValue => _workbenchController();
    private ServicesController _servicesControllerValue => _servicesController();
    private AgentsController _agentsControllerValue => _agentsController();
    private ComposerController _composerControllerValue => _composerController();
    private Task LoadSkillsAsync() => _loadSkillsAsync();
    private Dispatcher Dispatcher => _dispatcher;
}
