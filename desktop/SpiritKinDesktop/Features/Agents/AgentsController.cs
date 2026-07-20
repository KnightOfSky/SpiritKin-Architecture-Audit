using Microsoft.Win32;
using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class AgentsController
{
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly ChatWorkspaceView ChatWorkspace;
    private readonly Func<string> _apiBase;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<Task> _loadModuleManagementAsync;
    private readonly Func<Task> _loadSearchManagementAsync;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<string, string, string, bool> _confirmAction;
    private readonly Func<string> _activeWorkspaceRoot;
    private readonly Func<ProjectRuntimeProfile> _activeProjectRuntimeProfile;
    private readonly Func<ProjectRuntimeProfile, Dictionary<string, string>> _buildProjectRuntimeEnvironment;
    private readonly Action<ProcessStartInfo, IReadOnlyDictionary<string, string>> _applyEnvironment;
    private readonly Func<FrameworkElement, object?, Style?> _tryFindResource;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly string _rootDir;
    private readonly Func<int> _remoteWorkerPort;
    private readonly IReadOnlyCollection<AssistModelViewModel> _assistModels;
    private readonly IReadOnlyCollection<SkillViewModel> _skills;
    private readonly Func<string> _currentSkillEditorName;
    private readonly Action<ListBox, string> _selectListBoxItemByTag;
    private readonly Func<bool> _getRendering;
    private readonly Action<bool> _setRendering;

    private readonly ObservableCollection<AgentViewModel> _agents = new();
    private readonly ObservableCollection<ExternalAssistantViewModel> _externalAssistants = new();
    private readonly ObservableCollection<AgentAdapterViewModel> _agentAdapters = new();
    private readonly ObservableCollection<KnowledgeBaseViewModel> _knowledgeBases = new();
    private readonly ObservableCollection<KnowledgeSourceViewModel> _knowledgeSources = new();
    private readonly ObservableCollection<RouteProfileViewModel> _routeProfiles = new();
    private readonly ObservableCollection<RemoteTargetViewModel> _remoteTargets = new();
    private readonly ObservableCollection<EventViewModel> _agentRecommendations = new();

    private JsonElement _lastAgentManagementState;
    private string _lastRemoteExportPath = "";
    private Process? _externalAssistantProcess;
    private CancellationTokenSource? _externalAssistantCts;

    public AgentsController(
        WorkbenchShellView workbenchShell,
        ChatWorkspaceView chatWorkspace,
        Func<string> apiBase,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<Task> loadModuleManagementAsync,
        Func<Task> loadSearchManagementAsync,
        Func<string, string, bool> confirmDestructiveAction,
        Func<string, string, string, bool> confirmAction,
        Func<string> activeWorkspaceRoot,
        Func<ProjectRuntimeProfile> activeProjectRuntimeProfile,
        Func<ProjectRuntimeProfile, Dictionary<string, string>> buildProjectRuntimeEnvironment,
        Action<ProcessStartInfo, IReadOnlyDictionary<string, string>> applyEnvironment,
        Func<FrameworkElement, object?, Style?> tryFindResource,
        JsonSerializerOptions jsonOptions,
        string rootDir,
        Func<int> remoteWorkerPort,
        IReadOnlyCollection<AssistModelViewModel> assistModels,
        IReadOnlyCollection<SkillViewModel> skills,
        Func<string> currentSkillEditorName,
        Action<ListBox, string> selectListBoxItemByTag,
        Func<bool> getRendering,
        Action<bool> setRendering)
    {
        WorkbenchShell = workbenchShell;
        ChatWorkspace = chatWorkspace;
        _apiBase = apiBase;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _loadModuleManagementAsync = loadModuleManagementAsync;
        _loadSearchManagementAsync = loadSearchManagementAsync;
        _confirmDestructiveAction = confirmDestructiveAction;
        _confirmAction = confirmAction;
        _activeWorkspaceRoot = activeWorkspaceRoot;
        _activeProjectRuntimeProfile = activeProjectRuntimeProfile;
        _buildProjectRuntimeEnvironment = buildProjectRuntimeEnvironment;
        _applyEnvironment = applyEnvironment;
        _tryFindResource = tryFindResource;
        _jsonOptions = jsonOptions;
        _rootDir = rootDir;
        _remoteWorkerPort = remoteWorkerPort;
        _assistModels = assistModels;
        _skills = skills;
        _currentSkillEditorName = currentSkillEditorName;
        _selectListBoxItemByTag = selectListBoxItemByTag;
        _getRendering = getRendering;
        _setRendering = setRendering;
    }

    internal ObservableCollection<AgentViewModel> Agents => _agents;
    internal ObservableCollection<ExternalAssistantViewModel> ExternalAssistants => _externalAssistants;
    internal ObservableCollection<AgentAdapterViewModel> AgentAdapters => _agentAdapters;
    internal ObservableCollection<KnowledgeBaseViewModel> KnowledgeBases => _knowledgeBases;
    internal ObservableCollection<KnowledgeSourceViewModel> KnowledgeSources => _knowledgeSources;
    internal ObservableCollection<RouteProfileViewModel> RouteProfiles => _routeProfiles;
    internal ObservableCollection<RemoteTargetViewModel> RemoteTargets => _remoteTargets;
    internal ObservableCollection<EventViewModel> Recommendations => _agentRecommendations;
    internal JsonElement LastAgentManagementState => _lastAgentManagementState;
    internal bool IsRendering => _getRendering();

    private Dispatcher Dispatcher => WorkbenchShell.Dispatcher;
    private string ApiBase() => _apiBase();
    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private Task LoadModuleManagementAsync() => _loadModuleManagementAsync();
    private Task LoadSearchManagementAsync() => _loadSearchManagementAsync();
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private bool ConfirmAction(string title, string message, string confirmText = "确定") => _confirmAction(title, message, confirmText);
    private string ActiveWorkspaceRoot() => _activeWorkspaceRoot();
    private ProjectRuntimeProfile ActiveProjectRuntimeProfile() => _activeProjectRuntimeProfile();
    private Dictionary<string, string> BuildProjectRuntimeEnvironment(ProjectRuntimeProfile runtime) => _buildProjectRuntimeEnvironment(runtime);
    private void ApplyEnvironment(ProcessStartInfo startInfo, IReadOnlyDictionary<string, string> env) => _applyEnvironment(startInfo, env);
    private void SelectListBoxItemByTag(ListBox listBox, string tag) => _selectListBoxItemByTag(listBox, tag);
    private void SetRendering(bool value) => _setRendering(value);
    private int RemoteWorkerPort => _remoteWorkerPort();
    private string CurrentSkillEditorName() => _currentSkillEditorName();

    internal void StopExternalAssistantPromptOnShutdown() => StopExternalAssistantPrompt(killOnly: true);
}
