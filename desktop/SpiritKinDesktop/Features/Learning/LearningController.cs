using SpiritKinDesktop.Controls;
using System;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    private readonly WorkbenchShellView WorkbenchShell;
    private readonly HttpClient _http;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly Func<DesktopSession> _activeSession;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<Window> _ownerWindow;

    private readonly ObservableCollection<EventViewModel> _modelProviders = new();
    private readonly ObservableCollection<ModelProviderDefinitionViewModel> _providerDefinitions = new();
    private readonly ObservableCollection<AssistModelViewModel> _assistModels = new();

    private Func<string> _selectedComposerModelId = () => "";
    private Action<bool> _selectDefaultComposerModel = _ => { };
    private bool _syncingProviderSelection;
    private Process? _ollamaProcess;
    private Process? _lmStudioProcess;
    private Process? _llamaCppProcess;
    private Process? _llamaCppEmbeddingProcess;

    public LearningController(
        WorkbenchShellView workbenchShell,
        HttpClient http,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        Func<DesktopSession> activeSession,
        Func<string, string, bool> confirmDestructiveAction,
        Func<Window> ownerWindow)
    {
        WorkbenchShell = workbenchShell;
        _http = http;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _activeSession = activeSession;
        _confirmDestructiveAction = confirmDestructiveAction;
        _ownerWindow = ownerWindow;
    }

    internal ObservableCollection<EventViewModel> ModelProviders => _modelProviders;
    internal ObservableCollection<ModelProviderDefinitionViewModel> ProviderDefinitions => _providerDefinitions;
    internal ObservableCollection<AssistModelViewModel> AssistModels => _assistModels;

    internal void SetComposerModelCallbacks(Func<string> selectedComposerModelId, Action<bool> selectDefaultComposerModel)
    {
        _selectedComposerModelId = selectedComposerModelId;
        _selectDefaultComposerModel = selectDefaultComposerModel;
    }

    private string ApiBase() => _apiBase();
    private Task<JsonDocument> GetJsonAsync(string url) => _getJsonAsync(url);
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private DesktopSession ActiveSession() => _activeSession();
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private Window OwnerWindow() => _ownerWindow();
    private string SelectedComposerModelId() => _selectedComposerModelId();
    private void SelectDefaultComposerModel(bool persist) => _selectDefaultComposerModel(persist);

    internal sealed record SelectedProviderConfig(
        string Provider,
        string DisplayName,
        string Endpoint,
        string Model,
        string ApiKey,
        bool RequiresApiKey,
        bool LocalService,
        bool SupportsModelSync,
        string Protocol);
}
