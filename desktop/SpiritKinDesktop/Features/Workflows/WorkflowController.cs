using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    private const double WorkflowNodeHorizontalGap = WorkflowLaneLayout.NodeHorizontalGap;
    private const double WorkflowGraphMinCanvasWidth = 980;
    private const double WorkflowGraphMinCanvasHeight = 420;
    private const double WorkflowGraphCanvasPadding = 56;

    private readonly WorkbenchShellView WorkbenchShell;
    private readonly Func<string> _apiBase;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<Task> _loadModuleManagementAsync;
    private readonly Func<string, string, bool> _confirmDestructiveAction;
    private readonly Func<string, string, string, string?> _promptText;
    private readonly Action _renderTracePanel;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly string _rootDir;

    private readonly ObservableCollection<WorkflowDefinitionViewModel> _workflowDefinitions = new();
    private readonly ObservableCollection<WorkflowNodeViewModel> _workflowDefinitionNodes = new();
    private readonly ObservableCollection<WorkflowGraphNodeViewModel> _workflowGraphNodes = new();
    private readonly ObservableCollection<WorkflowGraphEdgeViewModel> _workflowGraphEdges = new();
    private readonly ObservableCollection<WorkflowSwimlaneViewModel> _workflowSwimlanes = new();
    private readonly ObservableCollection<WorkflowRunViewModel> _workflowRuns = new();
    private readonly ObservableCollection<WorkflowNodeViewModel> _workflowRunNodes = new();
    private readonly ObservableCollection<WorkflowTaskProgressViewModel> _workflowTaskProgress = new();
    private readonly ObservableCollection<WorkflowEditNodeViewModel> _workflowEditNodes = new();
    private readonly ObservableCollection<WorkflowNodeTemplateViewModel> _workflowNodeTemplates = new();
    private readonly ObservableCollection<WorkflowDependencyOptionViewModel> _workflowDependencyOptions = new();
    private readonly Dictionary<string, string> _workflowEdgeNotes = new(StringComparer.OrdinalIgnoreCase);
    private readonly ObservableCollection<WorkflowVersionViewModel> _workflowVersions = new();

    private JsonElement _lastWorkflowSnapshot;
    private JsonElement _activeWorkflowDefinition;
    private string _activeWorkflowName = "ecommerce.auto_listing.v1";
    private bool _syncingWorkflowSelection;
    private bool _syncingWorkflowNodeSelection;
    private bool _syncingWorkflowRunSelection;
    private bool _syncingWorkflowRunNodeSelection;
    private string _activeWorkflowRunId = "";
    private readonly Dictionary<string, FrameworkElement> _workflowParameterControls = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, object?> _workflowParameterDefaults = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, TextBox> _workflowNodeArgumentControls = new(StringComparer.OrdinalIgnoreCase);
    private bool _syncingWorkflowDependencySelection;
    private bool _syncingWorkflowNodeArgumentSchema;
    private bool _syncingWorkflowNodeEditor;
    private string _workflowDragNodeId = "";
    private Point _workflowDragStartPoint;
    private Point _workflowDragNodeStart;
    private Dictionary<string, Point> _workflowDragSelectionStart = new(StringComparer.OrdinalIgnoreCase);
    private bool _workflowDraggingNode;
    private bool _workflowDragHistoryPushed;
    private DateTime _lastWorkflowDragRenderAt = DateTime.MinValue;
    private string _workflowConnectSourceNodeId = "";
    private string _workflowConnectSourceOutputKind = "execution";
    private bool _workflowDraggingConnection;
    private Point _workflowConnectionStartPoint;
    private Point _workflowSelectionStartPoint;
    private bool _workflowSelecting;
    private bool _workflowPanning;
    private Point _workflowPanStartPoint;
    private double _workflowPanStartHorizontalOffset;
    private double _workflowPanStartVerticalOffset;
    private Point _workflowLastCanvasContextPoint = new(24, WorkflowLaneLayout.LanePaddingTop);
    private readonly HashSet<string> _selectedWorkflowNodeIds = new(StringComparer.OrdinalIgnoreCase);
    private readonly Stack<WorkflowEditorSnapshot> _workflowUndoStack = new();
    private readonly Stack<WorkflowEditorSnapshot> _workflowRedoStack = new();
    private bool _suppressWorkflowHistory;
    private double _workflowGraphZoom = 1.0;
    private double _workflowGraphContentWidth = WorkflowGraphMinCanvasWidth;
    private double _workflowGraphContentHeight = WorkflowGraphMinCanvasHeight;
    private bool _resetWorkflowGraphViewportOnNextRender;

    public WorkflowController(
        WorkbenchShellView workbenchShell,
        Func<string> apiBase,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<Task> loadModuleManagementAsync,
        Func<string, string, bool> confirmDestructiveAction,
        Func<string, string, string, string?> promptText,
        Action renderTracePanel,
        JsonSerializerOptions jsonOptions,
        string rootDir)
    {
        WorkbenchShell = workbenchShell;
        _apiBase = apiBase;
        _postJsonAsync = postJsonAsync;
        _loadModuleManagementAsync = loadModuleManagementAsync;
        _confirmDestructiveAction = confirmDestructiveAction;
        _promptText = promptText;
        _renderTracePanel = renderTracePanel;
        _jsonOptions = jsonOptions;
        _rootDir = rootDir;
    }

    internal ObservableCollection<WorkflowDefinitionViewModel> Definitions => _workflowDefinitions;
    internal ObservableCollection<WorkflowNodeViewModel> DefinitionNodes => _workflowDefinitionNodes;
    internal ObservableCollection<WorkflowGraphNodeViewModel> GraphNodes => _workflowGraphNodes;
    internal ObservableCollection<WorkflowGraphEdgeViewModel> GraphEdges => _workflowGraphEdges;
    internal ObservableCollection<WorkflowSwimlaneViewModel> Swimlanes => _workflowSwimlanes;
    internal ObservableCollection<WorkflowRunViewModel> Runs => _workflowRuns;
    internal ObservableCollection<WorkflowNodeViewModel> RunNodes => _workflowRunNodes;
    internal ObservableCollection<WorkflowTaskProgressViewModel> TaskProgress => _workflowTaskProgress;
    internal ObservableCollection<WorkflowEditNodeViewModel> EditNodes => _workflowEditNodes;
    internal ObservableCollection<WorkflowNodeTemplateViewModel> NodeTemplates => _workflowNodeTemplates;
    internal ObservableCollection<WorkflowDependencyOptionViewModel> DependencyOptions => _workflowDependencyOptions;
    internal ObservableCollection<WorkflowVersionViewModel> Versions => _workflowVersions;
    internal bool HasSnapshot => _lastWorkflowSnapshot.ValueKind == JsonValueKind.Object;
    internal string ActiveWorkflowRunIdValue => _activeWorkflowRunId;
    internal double WorkflowGraphZoom => _workflowGraphZoom;

    private Dispatcher Dispatcher => WorkbenchShell.Dispatcher;
    private string ApiBase() => _apiBase();
    private Task<JsonDocument> PostJsonAsync(string url, object payload) => _postJsonAsync(url, payload);
    private Task LoadModuleManagementAsync() => _loadModuleManagementAsync();
    private bool ConfirmDestructiveAction(string title, string message) => _confirmDestructiveAction(title, message);
    private string? PromptText(string title, string label, string defaultValue) => _promptText(title, label, defaultValue);
    private void RenderTracePanel() => _renderTracePanel();

    internal void CancelWorkflowGraphInteraction()
    {
        _workflowConnectSourceNodeId = "";
        _workflowConnectSourceOutputKind = "execution";
        _workflowDraggingConnection = false;
        _workflowSelecting = false;
        EndWorkflowGraphPan();
        WorkbenchShell.ManagementPanels.WorkflowSelectionRectangle.Visibility = Visibility.Collapsed;
        UpdateWorkflowConnectionPreview(default);
        RenderWorkflowGraph(ActiveWorkflowGraphDefinition(), FindWorkflowRun(_activeWorkflowRunId), ActiveWorkflowEditNodeId());
    }

    internal static bool IsInteractionTemplateAction(MainWindowInteractionTemplateAction action) => action switch
    {
        MainWindowInteractionTemplateAction.WorkflowGraphEdge_MouseLeftButtonDown
            or MainWindowInteractionTemplateAction.WorkflowGraphEdgeEditNoteMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphEdgeBreakMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphInputPort_MouseLeftButtonUp
            or MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonDown
            or MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseMove
            or MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonUp
            or MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonDown
            or MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseMove
            or MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonUp
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeEditMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeApplyInspectorMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeConnectSourceMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddAgentChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddToolChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSkillChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddReviewChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddBranchChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddWaiterChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCallbackChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSubgraphChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCustomChildMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeDuplicateMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectInputsMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectOutputsMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeRepairMenu_Click
            or MainWindowInteractionTemplateAction.WorkflowGraphNodeDeleteMenu_Click => true,
        _ => false,
    };

    internal void HandleInteractionTemplateAction(MainWindowInteractionTemplateAction action, object sender, EventArgs args)
    {
        switch (action)
        {
            case MainWindowInteractionTemplateAction.WorkflowGraphEdge_MouseLeftButtonDown:
                WorkflowGraphEdge_MouseLeftButtonDown(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphEdgeEditNoteMenu_Click:
                WorkflowGraphEdgeEditNoteMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphEdgeBreakMenu_Click:
                WorkflowGraphEdgeBreakMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphInputPort_MouseLeftButtonUp:
                WorkflowGraphInputPort_MouseLeftButtonUp(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonDown:
                WorkflowGraphOutputPort_MouseLeftButtonDown(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseMove:
                WorkflowGraphOutputPort_MouseMove(sender, (MouseEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonUp:
                WorkflowGraphOutputPort_MouseLeftButtonUp(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonDown:
                WorkflowGraphNode_MouseLeftButtonDown(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseMove:
                WorkflowGraphNode_MouseMove(sender, (MouseEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonUp:
                WorkflowGraphNode_MouseLeftButtonUp(sender, (MouseButtonEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeEditMenu_Click:
                WorkflowGraphNodeEditMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeApplyInspectorMenu_Click:
                WorkflowGraphNodeApplyInspectorMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeConnectSourceMenu_Click:
                WorkflowGraphNodeConnectSourceMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddAgentChildMenu_Click:
                WorkflowGraphNodeAddAgentChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddToolChildMenu_Click:
                WorkflowGraphNodeAddToolChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSkillChildMenu_Click:
                WorkflowGraphNodeAddSkillChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddReviewChildMenu_Click:
                WorkflowGraphNodeAddReviewChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddBranchChildMenu_Click:
                WorkflowGraphNodeAddBranchChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddWaiterChildMenu_Click:
                WorkflowGraphNodeAddWaiterChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCallbackChildMenu_Click:
                WorkflowGraphNodeAddCallbackChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSubgraphChildMenu_Click:
                WorkflowGraphNodeAddSubgraphChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCustomChildMenu_Click:
                WorkflowGraphNodeAddCustomChildMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDuplicateMenu_Click:
                WorkflowGraphNodeDuplicateMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectInputsMenu_Click:
                WorkflowGraphNodeDisconnectInputsMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectOutputsMenu_Click:
                WorkflowGraphNodeDisconnectOutputsMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeRepairMenu_Click:
                WorkflowGraphNodeRepairMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDeleteMenu_Click:
                WorkflowGraphNodeDeleteMenu_Click(sender, (RoutedEventArgs)args);
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(action), action, null);
        }
    }

    private static string ReadJsonString(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "",
            _ => value.GetRawText(),
        };
    }

    private static string ReadJsonString(JsonElement element, string key, string fallback)
    {
        var value = ReadJsonString(element, key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value;
    }

    private static int ReadJsonInt(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
        {
            return number;
        }
        return int.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    private static double ReadJsonDouble(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        {
            return number;
        }
        return double.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    private static bool ReadJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
            _ => fallback,
        };
    }

    private static bool TryReadJsonObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        value = candidate;
        return true;
    }

    private static bool TryReadJsonArray(JsonElement element, string key)
    {
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(key, out var candidate)
            && candidate.ValueKind == JsonValueKind.Array;
    }

    private static string ReadSafeJsonString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonString(element, key, fallback);
    }

    private static int ReadSafeJsonInt(JsonElement element, string key, int fallback = 0)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out _))
        {
            return fallback;
        }
        return ReadJsonInt(element, key);
    }

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

    private static string FormatJson(JsonElement element)
    {
        try
        {
            return JsonSerializer.Serialize(element, new JsonSerializerOptions { WriteIndented = true });
        }
        catch
        {
            return element.GetRawText();
        }
    }

    private static string[] SplitLines(string text)
    {
        return text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
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
        if (combo.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return definition.Provider;
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
        foreach (var definition in combo.Items.OfType<ModelProviderDefinitionViewModel>())
        {
            if (string.Equals(definition.Provider, value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = definition;
                SyncEditableComboSelectionText(combo);
                return;
            }
        }
        foreach (var agent in combo.Items.OfType<AgentViewModel>())
        {
            if (string.Equals(agent.AgentId, value, StringComparison.OrdinalIgnoreCase)
                || string.Equals(agent.Label, value, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = agent;
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

    private static void SyncEditableComboSelectionText(ComboBox combo)
    {
        if (!combo.IsEditable)
        {
            return;
        }
        var display = ComboDisplayText(combo).Trim();
        if (!string.IsNullOrWhiteSpace(display))
        {
            combo.Text = display;
        }
    }

    private static string ComboDisplayText(ComboBox combo)
    {
        if (combo.SelectedItem is ComboBoxItem item)
        {
            return Convert.ToString(item.Content) ?? "";
        }
        if (combo.SelectedItem is ModelProviderDefinitionViewModel definition)
        {
            return string.IsNullOrWhiteSpace(definition.DisplayName) ? definition.Provider : definition.DisplayName;
        }
        if (combo.SelectedItem is AssistModelViewModel model)
        {
            return string.IsNullOrWhiteSpace(model.DisplayName) ? model.ModelId : model.DisplayName;
        }
        if (combo.SelectedItem is AgentViewModel agent)
        {
            return agent.AgentId;
        }
        if (combo.SelectedItem is null && combo.SelectedValue is string selectedValue)
        {
            return selectedValue;
        }
        return Convert.ToString(combo.SelectedItem) ?? combo.Text;
    }

    private static string ComboBoxItemValue(ComboBoxItem item)
    {
        var tag = Convert.ToString(item.Tag);
        return string.IsNullOrWhiteSpace(tag) ? Convert.ToString(item.Content) ?? "" : tag;
    }

    private static string? SelectExistingId(string? preferred, IEnumerable<string> candidates)
    {
        if (string.IsNullOrWhiteSpace(preferred))
        {
            return null;
        }
        return candidates.FirstOrDefault(candidate => string.Equals(candidate, preferred, StringComparison.OrdinalIgnoreCase));
    }

    internal sealed record WorkflowEditorSnapshot(List<WorkflowEditNodeViewModel> Nodes, string SelectedNodeId, string Reason);
}
