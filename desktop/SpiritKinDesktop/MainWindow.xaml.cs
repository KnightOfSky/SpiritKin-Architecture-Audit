using System;
using System.IO;
using System.Windows;

namespace SpiritKinDesktop;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        _http.Timeout = TimeSpan.FromMinutes(10);
        _rootDir = WorkspaceController.FindWorkspaceRoot();
        _workspaceController = new WorkspaceController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            ChatSplitterColumn,
            ChatColumn,
            RightSplitterColumn,
            RightPanelColumn,
            _http,
            _jsonOptions,
            _rootDir,
            () => _state,
            () => { },
            () => Task.CompletedTask,
            () => _workspaceController!.ActiveSession(),
            () => _rendering,
            () => false,
            () => _latestCommandRequestId,
            _ => { },
            () => this,
            Dispatcher,
            _managedProjects,
            _quickCommands);
        _desktopApi = new DesktopApiClient(_http, _jsonOptions, _workspaceController.ApiBase, () => WorkspaceSidebar.TokenBox.Password.Trim());
        _shellInteractionController = new ShellInteractionController(key => TryFindResource(key));
        _runtimeController = new RuntimeController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            TitleBar,
            _http,
            _desktopApi,
            _jsonOptions,
            _rootDir,
            () => _state,
            value => _state = value,
            () => _rendering,
            value => _rendering = value,
            () => _latestCommandRequestId,
            value => _latestCommandRequestId = value,
            () => _workspaceController,
            () => _safetyController!,
            () => _moduleManagementController!,
            () => _evolutionController!,
            () => _workflowController!,
            () => _learningController!,
            () => _mobileManagementController!,
            () => _navigationController!,
            () => _contextController!,
            () => _workbenchController!,
            () => _servicesController!,
            () => _agentsController!,
            () => _composerController!,
            LoadSkillsFromControllerAsync,
            _sessions,
            _projects,
            _managedSessions,
            _managedProjects,
            _managedProjectSessions,
            _tasks,
            _messages,
            _events,
            _traceEvents,
            _syncClients,
            _diagnosticChecks,
            _diagnosticIssues,
            _logs,
            _actionLogEvents,
            _dailyItems,
            _quickCommands,
            _logPaths,
            _pendingDeletedSessionIds,
            _pendingDeletedProjectIds,
            _pendingDeletedTaskIds,
            _pendingDeletedMessageIds,
            _expandedProjectIds,
            Dispatcher);
        _skillsController = new SkillsController(
            WorkbenchShell,
            _workspaceController.ApiBase,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _jsonOptions,
            (title, message) => _navigationController!.ConfirmDestructiveAction(title, message),
            () => _agentsController!.Agents);
        _navigationController = new NavigationController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            _rootDir,
            () => _state,
            () => _rendering,
            value => _rendering = value,
            _runtimeController.RenderState,
            _runtimeController.RenderActiveSessionSwitch,
            _runtimeController.SaveStateAsync,
            _runtimeController.PostJsonAsync,
            _runtimeController.LoadLogsAsync,
            _runtimeController.LoadDailyAsync,
            _runtimeController.LoadDiagnosticsAsync,
            _runtimeController.LoadModuleManagementAsync,
            _runtimeController.RenderTracePanel,
            _skillsController.RenderSelectedSkillEditor,
            _skillsController.DeleteSkillAsync,
            _runtimeController.NewSessionAsync,
            _runtimeController.CreateProjectFromSidebarAsync,
            _runtimeController.AddProjectAsync,
            _runtimeController.AddProjectFromEditorAsync,
            _runtimeController.AddTaskAsync,
            _runtimeController.AddTaskFromEditorAsync,
            () => _workspaceController,
            () => _contextController!,
            () => _workbenchController!,
            () => _servicesController!,
            () => _agentsController!,
            () => _moduleManagementController!,
            () => _workflowController!,
            () => _learningController!,
            () => _evolutionController!,
            () => _searchManagementController!,
            () => _mcpManagementController!,
            () => _mobileManagementController!,
            () => _memoryManagementController!,
            () => _governanceController!.LoadAsync(),
            () => this,
            _shellInteractionController.EnsureTextEditContextMenu,
            key => TryFindResource(key),
            _logs,
            _logPaths,
            _skillsController.Skills,
            _expandedProjectIds,
            _pendingDeletedSessionIds,
            _pendingDeletedProjectIds,
            _pendingDeletedTaskIds);
        _safetyController = new SafetyController(_runtimeController.GetJsonAsync, _runtimeController.PostJsonAsync, _workspaceController.ApiBase, ChatWorkspace, _navigationController.PromptText);
        _moduleManagementController = new ModuleManagementController(
            WorkbenchShell.ManagementPanels,
            _workspaceController.OpenManagementPage);
        _searchManagementController = new SearchManagementController(
            WorkbenchShell.ManagementPanels,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            LoadAgentManagementFromControllerAsync,
            _runtimeController.LoadModuleManagementAsync);
        _governanceController = new GovernanceController(
            WorkbenchShell.ManagementPanels,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            _navigationController.ConfirmDestructiveAction);
        _mcpManagementController = new McpManagementController(
            WorkbenchShell.ManagementPanels,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            _jsonOptions,
            _runtimeController.LoadModuleManagementAsync,
            _navigationController.ConfirmDestructiveAction,
            () => _rendering,
            value => _rendering = value);
        _evolutionController = new EvolutionController(
            WorkbenchShell.ManagementPanels,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            _skillsController.LoadSkillsAsync);
        _workflowController = new WorkflowController(
            WorkbenchShell,
            _workspaceController.ApiBase,
            _runtimeController.PostJsonAsync,
            _runtimeController.LoadModuleManagementAsync,
            _navigationController.ConfirmDestructiveAction,
            _navigationController.PromptText,
            _runtimeController.RenderTracePanel,
            _jsonOptions,
            _rootDir);
        _learningController = new LearningController(
            WorkbenchShell,
            _http,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            _workspaceController.ActiveSession,
            _navigationController.ConfirmDestructiveAction,
            () => this);
        _mobileManagementController = new MobileManagementController(
            WorkbenchShell,
            _workspaceController.ApiBase,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _runtimeController.LoadModuleManagementAsync,
            _workflowController,
            _rootDir);
        _memoryManagementController = new MemoryManagementController(
            WorkbenchShell.ManagementPanels,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase);
        _contextController = new ContextController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            _workspaceController,
            _rootDir,
            _jsonOptions,
            () => _state,
            _runtimeController.RenderState,
            _runtimeController.SaveStateAsync,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _runtimeController.RenderActiveMessages,
            _runtimeController.SyncQuickChatLayout,
            _runtimeController.ScrollMessagesToEnd,
            _navigationController.PromptText,
            _navigationController.ConfirmAction,
            _messages,
            Dispatcher);
        _workbenchController = new WorkbenchController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            TerminalPanel,
            TerminalRow,
            _rootDir,
            () => _state,
            _workspaceController.ActiveSession,
            _workspaceController.ActiveWorkspaceRoot,
            _workspaceController.ActiveProjectRuntimeProfile,
            _workspaceController.BuildProjectRuntimeEnvironment,
            _workspaceController.ApiBase,
            _workspaceController.CommandUrl,
            _workspaceController.FrontendBaseUrl,
            _workspaceController.EnsureFrontendService,
            _runtimeController.SaveStateAsync,
            _navigationController.ConfirmDestructiveAction,
            _navigationController.ConfirmAction,
            _navigationController.PromptText,
            _shellInteractionController.AddContextMenuItem,
            _shellInteractionController.CreateStyledSeparator,
            _shellInteractionController.ApplyMenuStyle,
            _shellInteractionController.AddDisabledMenuHeader,
            () => Dispatcher);
        _servicesController = new ServicesController(
            WorkspaceSidebar,
            WorkbenchShell,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _workspaceController.ApiBase,
            _runtimeController.LoadDiagnosticsAsync,
            () => _runtimeController.LoadLogsAsync(),
            _runtimeController.LoadDailyAsync,
            _workspaceController.ActiveSession,
            _workspaceController.ProjectForSession,
            _workspaceController.SelectedProject,
            _workspaceController.ResolveProjectWorkspace,
            _workspaceController.ActiveWorkspaceRoot,
            _navigationController.ConfirmDestructiveAction,
            _navigationController.ConfirmAction,
            _workspaceController.StartWebSocket);
        _agentsController = new AgentsController(
            WorkbenchShell,
            ChatWorkspace,
            _workspaceController.ApiBase,
            _runtimeController.GetJsonAsync,
            _runtimeController.PostJsonAsync,
            _runtimeController.LoadModuleManagementAsync,
            _searchManagementController.LoadAsync,
            _navigationController.ConfirmDestructiveAction,
            _navigationController.ConfirmAction,
            _workspaceController.ActiveWorkspaceRoot,
            _workspaceController.ActiveProjectRuntimeProfile,
            _workspaceController.BuildProjectRuntimeEnvironment,
            WorkspaceController.ApplyEnvironment,
            (element, key) => element.TryFindResource(key) as Style,
            _jsonOptions,
            _rootDir,
            () => _workspaceController.RemoteWorkerPort,
            _learningController.AssistModels,
            _skillsController.Skills,
            _skillsController.CurrentSkillEditorName,
            WorkspaceController.SelectListBoxItemByTag,
            () => _rendering,
            value => _rendering = value);
        _composerController = new ComposerController(
            WorkspaceSidebar,
            WorkbenchShell,
            ChatWorkspace,
            _http,
            _jsonOptions,
            _rootDir,
            () => _state,
            _runtimeController.RenderState,
            _runtimeController.SaveStateAsync,
            _workspaceController.ActiveSession,
            () => _runtimeController.AddMessage("system", "", "work", 0),
            _workspaceController.ActiveProjectRuntimeProfile,
            _contextController.CurrentSessionCollaborationThreadId,
            _contextController.ProjectCollaborationMessagesIntoActiveSessionFromCache,
            _runtimeController.RenderActiveMessages,
            _runtimeController.SyncQuickChatLayout,
            _workspaceController.OpenManagementPage,
            _navigationController.ConfirmAction,
            _navigationController.PromptText,
            _workspaceController.ApplyAuth,
            _workspaceController.ApiBase,
            _workbenchController.LaunchLocalEdgeBrowser,
            () => _contextController.CollaborationChatActive,
            _contextController.SetCollaborationChatActive,
            _workspaceController.SetQuickChatMode,
            _contextController.SetActiveCollaborationThread,
            _contextController.ClearCollaborationChatSignature,
            _contextController.ClearCollaborationContextSignature,
            _contextController.ClearCollaborationThreadSignature,
            () => _workbenchController.CachedGitDirtyCount,
            () => _workbenchController.GitChanges,
            _workbenchController.RunGit,
            _workbenchController.CurrentGitBranch,
            _workbenchController.GitBranches,
            _workbenchController.GitDirtyCount,
            _workbenchController.SetLastKnownBranch,
            _shellInteractionController.AddContextMenuItem,
            _shellInteractionController.CreateStyledSeparator,
            _shellInteractionController.ApplyMenuStyle,
            _shellInteractionController.AddDisabledMenuHeader,
            value => WorkspaceSidebar.ConnectionStatusText.Text = value,
            value =>
            {
                WorkspaceSidebar.ProjectsList.SelectedValue = value;
                WorkbenchShell.ManagementPanels.RightProjectsList.SelectedValue = value;
            },
            item => item is not null && WorkspaceController.IsArchived(item.Status),
            _learningController.AssistModels,
            () => _agentsController.Agents,
            () => _contextController.CollaborationParticipantOptions,
            _composerAttachments,
            _expandedProjectIds,
            _assistantWorkTimer);
        _contextController.SetComposerController(() => _composerController);
        _workspaceController.SetPrepareQuickChatCallback(() => _composerController.SetCollaborationComposerMode(false, persist: false));
        _workspaceController.SetWorkspaceChangedCallback(_workbenchController.InvalidateWorkspaceContext);
        _workspaceController.SetRuntimeCallbacks(
            _runtimeController.RenderState,
            _runtimeController.SaveStateAsync,
            () => _latestCommandRequestId,
            ev => _runtimeController.ApplyEvent(ev));
        _workspaceController.SetManagedEditorSessionCallback(_navigationController.ManagedEditorSession);
        _workbenchController.SetComposerStatusCallbacks(
            () => _composerController.AssistantWorkStartedAt,
            () => _composerController.AssistantWorkDuration,
            limit => _composerController.PendingAttachmentNames(limit),
            () => _composerController.HasPendingAttachments,
            () => _composerController.RuntimeDisplay);
        _workspaceController.SetWorkbenchPanelCollapsedCallback(() => _workbenchController.PanelCollapsed);
        _learningController.SetComposerModelCallbacks(
            () => _composerController.SelectedModelId,
            persist => _composerController.SelectDefaultComposerModel(persist));
        _globalSearchController = new GlobalSearchController(
            GlobalSearchOverlay,
            WorkspaceSidebar,
            WorkbenchShell,
            () => _workspaceController,
            () => _navigationController,
            () => _workflowController,
            () => _skillsController,
            () => _agentsController,
            () => _learningController,
            () => _moduleManagementController,
            () => _servicesController,
            () => _runtimeController,
            _sessions,
            _projects,
            _tasks,
            _logs,
            _quickCommands,
            value => WorkspaceSidebar.ConnectionStatusText.Text = value);
        _musicPlayerController = new MusicPlayerController(
            MusicPlayerBar,
            MusicPlayerRow,
            _runtimeController,
            _rootDir);
        InitializeMainWindowShell();
        Dispatcher.BeginInvoke(_learningController.AutoStartLlamaCpp);
    }

    private System.Threading.Tasks.Task LoadSkillsFromControllerAsync() =>
        _skillsController.LoadSkillsAsync();

    private System.Threading.Tasks.Task LoadAgentManagementFromControllerAsync() =>
        _agentsController.LoadAgentManagementAsync();
}
