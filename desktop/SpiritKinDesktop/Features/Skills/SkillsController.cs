using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class SkillsController
{
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly Func<string> _apiBase;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<IEnumerable<AgentViewModel>> _agents;

    private readonly ObservableCollection<SkillViewModel> _skills = new();
    private readonly ObservableCollection<SkillSourceViewModel> _skillSources = new();

    internal SkillsController(
        WorkbenchShellView workbenchShell,
        Func<string> apiBase,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        JsonSerializerOptions jsonOptions,
        Func<string, string, bool> confirmDestructiveAction,
        Func<IEnumerable<AgentViewModel>> agents)
    {
        WorkbenchShell = workbenchShell;
        _apiBase = apiBase;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _jsonOptions = jsonOptions;
        _confirmDestructiveAction = confirmDestructiveAction;
        _agents = agents;
    }

    internal ObservableCollection<SkillViewModel> Skills => _skills;
    internal ObservableCollection<SkillSourceViewModel> SkillSources => _skillSources;
    internal string CurrentSkillEditorName() => WorkbenchShell.ManagementPanels.SkillNameBox.Text.Trim();

    private string ApiBase() => _apiBase();
    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private IEnumerable<AgentViewModel> Agents => _agents();
}
