using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class ManagementPanelsView : UserControl
{
    public ManagementPanelsView()
    {
        InitializeComponent();
    }

    public event System.Windows.Controls.SelectionChangedEventHandler? AgentAdaptersListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? AgentsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? AssistModelsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? CollaborationMessagesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? CollaborationTasksListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ContextSuggestionsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? DailyItemsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? DiagnosticIssuesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ExternalAssistantsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? LogsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ManagedSessionsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ModuleManagementActionsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ModuleManagementModulesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ProjectOverviewChangesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ProjectSessionsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? QuickCommandsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? RemoteTargetsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? RightProjectsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? RightTasksListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? RouteProfilesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? ServicePortsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? SkillsListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? WorkflowDefinitionCatalogListSelectionChanged;
    public event System.Windows.Input.MouseButtonEventHandler? WorkflowGraphCanvasMouseLeftButtonDown;
    public event System.Windows.Input.MouseButtonEventHandler? WorkflowGraphCanvasMouseLeftButtonUp;
    public event System.Windows.Input.MouseEventHandler? WorkflowGraphCanvasMouseMove;
    public event System.Windows.Input.MouseButtonEventHandler? WorkflowGraphCanvasMouseRightButtonDown;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddAgentNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddBranchNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddCallbackNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddCustomNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddReviewNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddSkillNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddSubgraphNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddToolNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAddWaiterNodeMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasAutoLayoutMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasResetViewMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasSaveDefinitionMenuClick;
    public event System.Windows.RoutedEventHandler? WorkflowGraphCanvasValidateDefinitionMenuClick;
    public event System.Windows.Input.MouseWheelEventHandler? WorkflowGraphScrollViewerPreviewMouseWheel;
    public event System.Windows.SizeChangedEventHandler? WorkflowGraphScrollViewerSizeChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? WorkflowRunNodesListSelectionChanged;
    public event System.Windows.Controls.SelectionChangedEventHandler? WorkflowRunsListSelectionChanged;

    private void AgentAdaptersList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => AgentAdaptersListSelectionChanged?.Invoke(sender, e);
    private void AgentsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => AgentsListSelectionChanged?.Invoke(sender, e);
    private void AssistModelsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => AssistModelsListSelectionChanged?.Invoke(sender, e);
    private void CollaborationMessagesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => CollaborationMessagesListSelectionChanged?.Invoke(sender, e);
    private void CollaborationTasksList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => CollaborationTasksListSelectionChanged?.Invoke(sender, e);
    private void ContextSuggestionsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ContextSuggestionsListSelectionChanged?.Invoke(sender, e);
    private void DailyItemsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => DailyItemsListSelectionChanged?.Invoke(sender, e);
    private void DiagnosticIssuesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => DiagnosticIssuesListSelectionChanged?.Invoke(sender, e);
    private void ExternalAssistantsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ExternalAssistantsListSelectionChanged?.Invoke(sender, e);
    private void LogsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => LogsListSelectionChanged?.Invoke(sender, e);
    private void ManagedSessionsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ManagedSessionsListSelectionChanged?.Invoke(sender, e);
    private void ModuleManagementActionsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ModuleManagementActionsListSelectionChanged?.Invoke(sender, e);
    private void ModuleManagementModulesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ModuleManagementModulesListSelectionChanged?.Invoke(sender, e);
    private void ProjectOverviewChangesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ProjectOverviewChangesListSelectionChanged?.Invoke(sender, e);
    private void ProjectSessionsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ProjectSessionsListSelectionChanged?.Invoke(sender, e);
    private void QuickCommandsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => QuickCommandsListSelectionChanged?.Invoke(sender, e);
    private void RemoteTargetsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => RemoteTargetsListSelectionChanged?.Invoke(sender, e);
    private void RightProjectsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => RightProjectsListSelectionChanged?.Invoke(sender, e);
    private void RightTasksList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => RightTasksListSelectionChanged?.Invoke(sender, e);
    private void RouteProfilesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => RouteProfilesListSelectionChanged?.Invoke(sender, e);
    private void ServicePortsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => ServicePortsListSelectionChanged?.Invoke(sender, e);
    private void SkillsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => SkillsListSelectionChanged?.Invoke(sender, e);
    private void WorkflowDefinitionCatalogList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => WorkflowDefinitionCatalogListSelectionChanged?.Invoke(sender, e);
    private void WorkflowGraphCanvas_MouseLeftButtonDown(object sender, System.Windows.Input.MouseButtonEventArgs e) => WorkflowGraphCanvasMouseLeftButtonDown?.Invoke(sender, e);
    private void WorkflowGraphCanvas_MouseLeftButtonUp(object sender, System.Windows.Input.MouseButtonEventArgs e) => WorkflowGraphCanvasMouseLeftButtonUp?.Invoke(sender, e);
    private void WorkflowGraphCanvas_MouseMove(object sender, System.Windows.Input.MouseEventArgs e) => WorkflowGraphCanvasMouseMove?.Invoke(sender, e);
    private void WorkflowGraphCanvas_MouseRightButtonDown(object sender, System.Windows.Input.MouseButtonEventArgs e) => WorkflowGraphCanvasMouseRightButtonDown?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddAgentNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddAgentNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddBranchNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddBranchNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddCallbackNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddCallbackNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddCustomNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddCustomNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddReviewNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddReviewNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddSkillNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddSkillNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddSubgraphNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddSubgraphNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddToolNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddToolNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAddWaiterNodeMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAddWaiterNodeMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasAutoLayoutMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasAutoLayoutMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasResetViewMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasResetViewMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasSaveDefinitionMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasSaveDefinitionMenuClick?.Invoke(sender, e);
    private void WorkflowGraphCanvasValidateDefinitionMenu_Click(object sender, System.Windows.RoutedEventArgs e) => WorkflowGraphCanvasValidateDefinitionMenuClick?.Invoke(sender, e);
    private void WorkflowGraphScrollViewer_PreviewMouseWheel(object sender, System.Windows.Input.MouseWheelEventArgs e) => WorkflowGraphScrollViewerPreviewMouseWheel?.Invoke(sender, e);
    private void WorkflowGraphScrollViewer_SizeChanged(object sender, System.Windows.SizeChangedEventArgs e) => WorkflowGraphScrollViewerSizeChanged?.Invoke(sender, e);
    private void WorkflowRunNodesList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => WorkflowRunNodesListSelectionChanged?.Invoke(sender, e);
    private void WorkflowRunsList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) => WorkflowRunsListSelectionChanged?.Invoke(sender, e);
}
