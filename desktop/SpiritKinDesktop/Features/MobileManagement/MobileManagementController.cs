using SpiritKinDesktop.Controls;
using System;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly Func<string> _apiBase;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<Task> _loadModuleManagementAsync;
    private readonly WorkflowController _workflowController;
    private readonly string _rootDir;

    public MobileManagementController(
        WorkbenchShellView workbenchShell,
        Func<string> apiBase,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<Task> loadModuleManagementAsync,
        WorkflowController workflowController,
        string rootDir)
    {
        WorkbenchShell = workbenchShell;
        _apiBase = apiBase;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _loadModuleManagementAsync = loadModuleManagementAsync;
        _workflowController = workflowController;
        _rootDir = rootDir;
    }

    private string ApiBase() => _apiBase();
    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private Task LoadModuleManagementAsync() => _loadModuleManagementAsync();
}
