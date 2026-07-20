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

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    private readonly WorkspaceSidebarView WorkspaceSidebar;
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly string _rootDir;
    private readonly Func<DesktopState> _getState;
    private readonly Func<bool> _isRendering;
    private readonly Action<bool> _setRendering;
    private readonly Action _renderState;
    private readonly Action _renderActiveSessionSwitch;
    private readonly Func<Task> _saveStateAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string, Task> _loadLogsAsync;
    private readonly Func<Task> _loadDailyAsync;
    private readonly Func<Task> _loadDiagnosticsAsync;
    private readonly Func<Task> _loadModuleManagementAsync;
    private readonly Action _renderTracePanel;
    private readonly Action _renderSelectedSkillEditor;
    private readonly Func<Task> _deleteSkillAsync;
    private readonly Func<string?, Task> _newSessionAsync;
    private readonly Func<Task> _createProjectFromSidebarAsync;
    private readonly Func<Task> _addProjectAsync;
    private readonly Func<Task> _addProjectFromEditorAsync;
    private readonly Func<Task> _addTaskAsync;
    private readonly Func<Task> _addTaskFromEditorAsync;
    private readonly Func<WorkspaceController> _workspaceController;
    private readonly Func<ContextController> _contextController;
    private readonly Func<WorkbenchController> _workbenchController;
    private readonly Func<ServicesController> _servicesController;
    private readonly Func<AgentsController> _agentsController;
    private readonly Func<ModuleManagementController> _moduleManagementController;
    private readonly Func<WorkflowController> _workflowController;
    private readonly Func<LearningController> _learningController;
    private readonly Func<EvolutionController> _evolutionController;
    private readonly Func<SearchManagementController> _searchManagementController;
    private readonly Func<McpManagementController> _mcpManagementController;
    private readonly Func<MobileManagementController> _mobileManagementController;
    private readonly Func<MemoryManagementController> _memoryManagementController;
    private readonly Func<Task> _loadGovernanceAsync;
    private readonly Func<Window> _owner;
    private readonly Action<TextBox> _ensureTextEditContextMenu;
    private readonly Func<object, object?> _tryFindResource;
    private readonly ObservableCollection<LogViewModel> _logs;
    private readonly Dictionary<string, string> _logPaths;
    private readonly ObservableCollection<SkillViewModel> _skills;
    private readonly HashSet<string> _expandedProjectIds;
    private readonly HashSet<string> _pendingDeletedSessionIds;
    private readonly HashSet<string> _pendingDeletedProjectIds;
    private readonly HashSet<string> _pendingDeletedTaskIds;

    internal NavigationController(
        WorkspaceSidebarView workspaceSidebar,
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        string rootDir,
        Func<DesktopState> getState,
        Func<bool> isRendering,
        Action<bool> setRendering,
        Action renderState,
        Action renderActiveSessionSwitch,
        Func<Task> saveStateAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string, Task> loadLogsAsync,
        Func<Task> loadDailyAsync,
        Func<Task> loadDiagnosticsAsync,
        Func<Task> loadModuleManagementAsync,
        Action renderTracePanel,
        Action renderSelectedSkillEditor,
        Func<Task> deleteSkillAsync,
        Func<string?, Task> newSessionAsync,
        Func<Task> createProjectFromSidebarAsync,
        Func<Task> addProjectAsync,
        Func<Task> addProjectFromEditorAsync,
        Func<Task> addTaskAsync,
        Func<Task> addTaskFromEditorAsync,
        Func<WorkspaceController> workspaceController,
        Func<ContextController> contextController,
        Func<WorkbenchController> workbenchController,
        Func<ServicesController> servicesController,
        Func<AgentsController> agentsController,
        Func<ModuleManagementController> moduleManagementController,
        Func<WorkflowController> workflowController,
        Func<LearningController> learningController,
        Func<EvolutionController> evolutionController,
        Func<SearchManagementController> searchManagementController,
        Func<McpManagementController> mcpManagementController,
        Func<MobileManagementController> mobileManagementController,
        Func<MemoryManagementController> memoryManagementController,
        Func<Task> loadGovernanceAsync,
        Func<Window> owner,
        Action<TextBox> ensureTextEditContextMenu,
        Func<object, object?> tryFindResource,
        ObservableCollection<LogViewModel> logs,
        Dictionary<string, string> logPaths,
        ObservableCollection<SkillViewModel> skills,
        HashSet<string> expandedProjectIds,
        HashSet<string> pendingDeletedSessionIds,
        HashSet<string> pendingDeletedProjectIds,
        HashSet<string> pendingDeletedTaskIds)
    {
        WorkspaceSidebar = workspaceSidebar;
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        _rootDir = rootDir;
        _getState = getState;
        _isRendering = isRendering;
        _setRendering = setRendering;
        _renderState = renderState;
        _renderActiveSessionSwitch = renderActiveSessionSwitch;
        _saveStateAsync = saveStateAsync;
        _postJsonAsync = postJsonAsync;
        _loadLogsAsync = loadLogsAsync;
        _loadDailyAsync = loadDailyAsync;
        _loadDiagnosticsAsync = loadDiagnosticsAsync;
        _loadModuleManagementAsync = loadModuleManagementAsync;
        _renderTracePanel = renderTracePanel;
        _renderSelectedSkillEditor = renderSelectedSkillEditor;
        _deleteSkillAsync = deleteSkillAsync;
        _newSessionAsync = newSessionAsync;
        _createProjectFromSidebarAsync = createProjectFromSidebarAsync;
        _addProjectAsync = addProjectAsync;
        _addProjectFromEditorAsync = addProjectFromEditorAsync;
        _addTaskAsync = addTaskAsync;
        _addTaskFromEditorAsync = addTaskFromEditorAsync;
        _workspaceController = workspaceController;
        _contextController = contextController;
        _workbenchController = workbenchController;
        _servicesController = servicesController;
        _agentsController = agentsController;
        _moduleManagementController = moduleManagementController;
        _workflowController = workflowController;
        _learningController = learningController;
        _evolutionController = evolutionController;
        _searchManagementController = searchManagementController;
        _mcpManagementController = mcpManagementController;
        _mobileManagementController = mobileManagementController;
        _memoryManagementController = memoryManagementController;
        _loadGovernanceAsync = loadGovernanceAsync;
        _owner = owner;
        _ensureTextEditContextMenu = ensureTextEditContextMenu;
        _tryFindResource = tryFindResource;
        _logs = logs;
        _logPaths = logPaths;
        _skills = skills;
        _expandedProjectIds = expandedProjectIds;
        _pendingDeletedSessionIds = pendingDeletedSessionIds;
        _pendingDeletedProjectIds = pendingDeletedProjectIds;
        _pendingDeletedTaskIds = pendingDeletedTaskIds;
    }

    private DesktopState _state => _getState();
    private bool _rendering
    {
        get => _isRendering();
        set => _setRendering(value);
    }

    private WorkspaceController Workspace => _workspaceController();
    private ContextController Context => _contextController();
    private WorkbenchController Workbench => _workbenchController();
    private ServicesController Services => _servicesController();
    private AgentsController Agents => _agentsController();
    private ModuleManagementController Modules => _moduleManagementController();
    private WorkflowController Workflows => _workflowController();
    private LearningController Learning => _learningController();
    private EvolutionController Evolution => _evolutionController();
    private SearchManagementController Search => _searchManagementController();
    private McpManagementController Mcp => _mcpManagementController();
    private MobileManagementController Mobile => _mobileManagementController();
    private MemoryManagementController Memory => _memoryManagementController();

    private void RenderState() => _renderState();
    private void RenderActiveSessionSwitch() => _renderActiveSessionSwitch();
    private Task SaveStateAsync() => _saveStateAsync();
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private Task LoadLogsAsync(string logId = "") => _loadLogsAsync(logId);
    private Task LoadDailyAsync() => _loadDailyAsync();
    private Task LoadDiagnosticsAsync() => _loadDiagnosticsAsync();
    private Task LoadModuleManagementAsync() => _loadModuleManagementAsync();
    private void RenderTracePanel() => _renderTracePanel();
    private void RenderSelectedSkillEditor() => _renderSelectedSkillEditor();
    private Task DeleteSkillAsync() => _deleteSkillAsync();
    private Task NewSessionAsync(string? projectId = null) => _newSessionAsync(projectId);
    private Task CreateProjectFromSidebarAsync() => _createProjectFromSidebarAsync();
    private Task AddProjectAsync() => _addProjectAsync();
    private Task AddProjectFromEditorAsync() => _addProjectFromEditorAsync();
    private Task AddTaskAsync() => _addTaskAsync();
    private Task AddTaskFromEditorAsync() => _addTaskFromEditorAsync();
    private void EnsureTextEditContextMenu(TextBox textBox) => _ensureTextEditContextMenu(textBox);
    private object? TryFindResource(object key) => _tryFindResource(key);

    private static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    internal static string NewId(string prefix) => $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];
    private static string ComboText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            var tag = Convert.ToString(item.Tag);
            return string.IsNullOrWhiteSpace(tag) ? Convert.ToString(item.Content) ?? "" : tag;
        }
        if (combo.SelectedValue is string selectedValue && !string.IsNullOrWhiteSpace(selectedValue))
        {
            return selectedValue;
        }
        return combo.Text;
    }

    private static string ReadJsonString(JsonElement element, string key) => JsonResponseHelpers.ReadJsonString(element, key);
    private static string ReadJsonString(JsonElement element, string key, string fallback) => JsonResponseHelpers.ReadJsonString(element, key, fallback);
    private static bool ReadJsonBool(JsonElement element, string key, bool fallback = false) => JsonResponseHelpers.ReadJsonBool(element, key, fallback);
}
