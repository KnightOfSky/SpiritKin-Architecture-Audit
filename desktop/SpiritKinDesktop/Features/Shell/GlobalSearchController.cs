using SpiritKinDesktop.Controls;
using System;
using System.Collections.ObjectModel;

namespace SpiritKinDesktop;

internal sealed partial class GlobalSearchController
{
    private readonly GlobalSearchOverlayView GlobalSearchOverlay;
    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly Func<WorkspaceController> _workspaceController;
    private readonly Func<NavigationController> _navigationController;
    private readonly Func<WorkflowController> _workflowController;
    private readonly Func<SkillsController> _skillsController;
    private readonly Func<AgentsController> _agentsController;
    private readonly Func<LearningController> _learningController;
    private readonly Func<ModuleManagementController> _moduleManagementController;
    private readonly Func<ServicesController> _servicesController;
    private readonly Func<RuntimeController> _runtimeController;
    private readonly ObservableCollection<SessionViewModel> _sessions;
    private readonly ObservableCollection<ProjectViewModel> _projects;
    private readonly ObservableCollection<TaskViewModel> _tasks;
    private readonly ObservableCollection<LogViewModel> _logs;
    private readonly ObservableCollection<QuickCommandViewModel> _quickCommands;
    private readonly ObservableCollection<GlobalSearchResultViewModel> _globalSearchResults = new();
    private readonly Action<string> _setConnectionStatus;

    internal GlobalSearchController(
        GlobalSearchOverlayView globalSearchOverlay,
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        Func<WorkspaceController> workspaceController,
        Func<NavigationController> navigationController,
        Func<WorkflowController> workflowController,
        Func<SkillsController> skillsController,
        Func<AgentsController> agentsController,
        Func<LearningController> learningController,
        Func<ModuleManagementController> moduleManagementController,
        Func<ServicesController> servicesController,
        Func<RuntimeController> runtimeController,
        ObservableCollection<SessionViewModel> sessions,
        ObservableCollection<ProjectViewModel> projects,
        ObservableCollection<TaskViewModel> tasks,
        ObservableCollection<LogViewModel> logs,
        ObservableCollection<QuickCommandViewModel> quickCommands,
        Action<string> setConnectionStatus)
    {
        GlobalSearchOverlay = globalSearchOverlay;
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        _workspaceController = workspaceController;
        _navigationController = navigationController;
        _workflowController = workflowController;
        _skillsController = skillsController;
        _agentsController = agentsController;
        _learningController = learningController;
        _moduleManagementController = moduleManagementController;
        _servicesController = servicesController;
        _runtimeController = runtimeController;
        _sessions = sessions;
        _projects = projects;
        _tasks = tasks;
        _logs = logs;
        _quickCommands = quickCommands;
        _setConnectionStatus = setConnectionStatus;
    }

    internal ObservableCollection<GlobalSearchResultViewModel> Results => _globalSearchResults;

    private WorkspaceController Workspace => _workspaceController();
    private NavigationController Navigation => _navigationController();
    private WorkflowController Workflows => _workflowController();
    private SkillsController Skills => _skillsController();
    private AgentsController Agents => _agentsController();
    private LearningController Learning => _learningController();
    private ModuleManagementController Modules => _moduleManagementController();
    private ServicesController Services => _servicesController();
    private RuntimeController Runtime => _runtimeController();
}
