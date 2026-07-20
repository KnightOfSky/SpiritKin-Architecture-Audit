using Microsoft.Web.WebView2.Wpf;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private readonly HttpClient _http = new();
    private readonly DesktopApiClient _desktopApi;
    private readonly SafetyController _safetyController;
    private readonly ModuleManagementController _moduleManagementController;
    private readonly SearchManagementController _searchManagementController;
    private readonly GovernanceController _governanceController;
    private readonly McpManagementController _mcpManagementController;
    private readonly EvolutionController _evolutionController;
    private readonly WorkflowController _workflowController;
    private readonly LearningController _learningController;
    private readonly MobileManagementController _mobileManagementController;
    private readonly MemoryManagementController _memoryManagementController;
    private readonly WorkspaceController _workspaceController;
    private readonly NavigationController _navigationController;
    private readonly RuntimeController _runtimeController;
    private readonly ContextController _contextController;
    private readonly WorkbenchController _workbenchController;
    private readonly ServicesController _servicesController;
    private readonly AgentsController _agentsController;
    private readonly ComposerController _composerController;
    private readonly SkillsController _skillsController;
    private readonly ShellInteractionController _shellInteractionController;
    private readonly GlobalSearchController _globalSearchController;
    private readonly MusicPlayerController _musicPlayerController;
    private readonly ObservableCollection<SessionViewModel> _sessions = new();
    private readonly ObservableCollection<ProjectViewModel> _projects = new();
    private readonly ObservableCollection<ProjectViewModel> _managedSessions = new();
    private readonly ObservableCollection<ProjectViewModel> _managedProjects = new();
    private readonly ObservableCollection<ProjectViewModel> _managedProjectSessions = new();
    private readonly ObservableCollection<TaskViewModel> _tasks = new();
    private readonly ObservableCollection<ChatTimelineItemViewModel> _messages = new BulkObservableCollection<ChatTimelineItemViewModel>();
    private readonly ObservableCollection<EventViewModel> _events = new();
    private readonly ObservableCollection<EventViewModel> _traceEvents = new();
    private readonly ObservableCollection<ComposerAttachmentViewModel> _composerAttachments = new();
    private readonly ObservableCollection<EventViewModel> _diagnosticChecks = new();
    private readonly ObservableCollection<ActionItemViewModel> _diagnosticIssues = new();
    private readonly ObservableCollection<LogViewModel> _logs = new();
    private readonly ObservableCollection<EventViewModel> _actionLogEvents = new();
    private readonly Dictionary<string, string> _logPaths = new(StringComparer.OrdinalIgnoreCase);
    private readonly ObservableCollection<EventViewModel> _syncClients = new();
    private readonly ObservableCollection<ActionItemViewModel> _dailyItems = new();
    private readonly ObservableCollection<QuickCommandViewModel> _quickCommands = new();
    private readonly JsonSerializerOptions _jsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false,
    };

    private DesktopState _state = DesktopState.CreateDefault();
    private bool _rendering;
    private readonly HashSet<string> _pendingDeletedSessionIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _pendingDeletedProjectIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _pendingDeletedTaskIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, HashSet<string>> _pendingDeletedMessageIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly DispatcherTimer _assistantWorkTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer _controlMonitorTimer = new() { Interval = TimeSpan.FromSeconds(30) };
    private bool _controlMonitorInFlight;
    private bool _chatsCollapsed;
    private bool _projectsCollapsed;
    private string _latestCommandRequestId = "";
    private readonly HashSet<string> _expandedProjectIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly string _rootDir;
    private VoiceCallSessionController? _voiceCallController;

    private sealed record ComposerSendOptions(bool SteerConversation = false);
    private sealed record PendingConfirmationInfo(string Target, string Operation, string RiskLevel);

}
