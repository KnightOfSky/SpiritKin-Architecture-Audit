using System;
using System.Linq;
using System.Windows;
using System.Windows.Input;

namespace SpiritKinDesktop;

public partial class MainWindowInteractionTemplates : ResourceDictionary
{
    public MainWindowInteractionTemplates()
    {
        InitializeComponent();
    }

    private static void Forward(MainWindowInteractionTemplateAction action, object sender, EventArgs args)
    {
        var mainWindow = ResolveMainWindow(sender);
        if (mainWindow is null)
        {
            return;
        }

        mainWindow.HandleInteractionTemplateAction(action, sender, args);
    }

    private static MainWindow? ResolveMainWindow(object sender)
    {
        if (Application.Current?.MainWindow is MainWindow current)
        {
            return current;
        }
        if (sender is DependencyObject dependency && Window.GetWindow(dependency) is MainWindow owner)
        {
            return owner;
        }
        return Application.Current?.Windows.OfType<MainWindow>().FirstOrDefault();
    }

    private void ForwardRouted(MainWindowInteractionTemplateAction action, object sender, RoutedEventArgs e)
    {
        Forward(action, sender, e);
    }

    private void ForwardMouse(MainWindowInteractionTemplateAction action, object sender, MouseEventArgs e)
    {
        Forward(action, sender, e);
    }

    private void ForwardMouseButton(MainWindowInteractionTemplateAction action, object sender, MouseButtonEventArgs e)
    {
        Forward(action, sender, e);
    }

    private void SessionPinMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionPinMenu_Click, sender, e);
    private void SessionRenameMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionRenameMenu_Click, sender, e);
    private void SessionArchiveMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionArchiveMenu_Click, sender, e);
    private void SessionUnreadMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionUnreadMenu_Click, sender, e);
    private void SessionOpenExplorerMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionOpenExplorerMenu_Click, sender, e);
    private void SessionCopyWorkingDirMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionCopyWorkingDirMenu_Click, sender, e);
    private void SessionCopyIdMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionCopyIdMenu_Click, sender, e);
    private void SessionCopyDeeplinkMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionCopyDeeplinkMenu_Click, sender, e);
    private void SessionDeleteMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SessionDeleteMenu_Click, sender, e);
    private void ProjectNewChatMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectNewChatMenu_Click, sender, e);
    private void ProjectRenameMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectRenameMenu_Click, sender, e);
    private void ProjectArchiveMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectArchiveMenu_Click, sender, e);
    private void ProjectOpenManagementMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectOpenManagementMenu_Click, sender, e);
    private void SidebarOpenExplorerMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SidebarOpenExplorerMenu_Click, sender, e);
    private void SidebarCopyWorkingDirMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SidebarCopyWorkingDirMenu_Click, sender, e);
    private void ProjectSessionPinMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectSessionPinMenu_Click, sender, e);
    private void ProjectSessionRenameMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectSessionRenameMenu_Click, sender, e);
    private void ProjectSessionArchiveMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectSessionArchiveMenu_Click, sender, e);
    private void ProjectSessionUnreadMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ProjectSessionUnreadMenu_Click, sender, e);
    private void SidebarCopyIdMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SidebarCopyIdMenu_Click, sender, e);
    private void SidebarCopyDeeplinkMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SidebarCopyDeeplinkMenu_Click, sender, e);
    private void SidebarDeleteMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.SidebarDeleteMenu_Click, sender, e);
    private void TaskStart_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.TaskStart_Click, sender, e);
    private void TaskComplete_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.TaskComplete_Click, sender, e);
    private void TaskBlocked_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.TaskBlocked_Click, sender, e);
    private void TaskDelete_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.TaskDelete_Click, sender, e);
    private void WorkflowGraphEdge_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphEdge_MouseLeftButtonDown, sender, e);
    private void WorkflowGraphEdgeEditNoteMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphEdgeEditNoteMenu_Click, sender, e);
    private void WorkflowGraphEdgeBreakMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphEdgeBreakMenu_Click, sender, e);
    private void WorkflowGraphInputPort_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphInputPort_MouseLeftButtonUp, sender, e);
    private void WorkflowGraphOutputPort_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonDown, sender, e);
    private void WorkflowGraphOutputPort_MouseMove(object sender, MouseEventArgs e) => ForwardMouse(MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseMove, sender, e);
    private void WorkflowGraphOutputPort_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonUp, sender, e);
    private void WorkflowGraphNode_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonDown, sender, e);
    private void WorkflowGraphNode_MouseMove(object sender, MouseEventArgs e) => ForwardMouse(MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseMove, sender, e);
    private void WorkflowGraphNode_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => ForwardMouseButton(MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonUp, sender, e);
    private void WorkflowGraphNodeEditMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeEditMenu_Click, sender, e);
    private void WorkflowGraphNodeApplyInspectorMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeApplyInspectorMenu_Click, sender, e);
    private void WorkflowGraphNodeConnectSourceMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeConnectSourceMenu_Click, sender, e);
    private void WorkflowGraphNodeAddAgentChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddAgentChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddToolChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddToolChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddSkillChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSkillChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddReviewChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddReviewChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddBranchChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddBranchChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddWaiterChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddWaiterChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddCallbackChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCallbackChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddSubgraphChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSubgraphChildMenu_Click, sender, e);
    private void WorkflowGraphNodeAddCustomChildMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCustomChildMenu_Click, sender, e);
    private void WorkflowGraphNodeDuplicateMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeDuplicateMenu_Click, sender, e);
    private void WorkflowGraphNodeDisconnectInputsMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectInputsMenu_Click, sender, e);
    private void WorkflowGraphNodeDisconnectOutputsMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectOutputsMenu_Click, sender, e);
    private void WorkflowGraphNodeRepairMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeRepairMenu_Click, sender, e);
    private void WorkflowGraphNodeDeleteMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkflowGraphNodeDeleteMenu_Click, sender, e);
    private void GitChangeItem_MouseEnter(object sender, MouseEventArgs e) => ForwardMouse(MainWindowInteractionTemplateAction.GitChangeItem_MouseEnter, sender, e);
    private void RemoveAttachmentButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.RemoveAttachmentButton_Click, sender, e);
    private void ServiceStart_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ServiceStart_Click, sender, e);
    private void ServiceRestart_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ServiceRestart_Click, sender, e);
    private void ServiceStop_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.ServiceStop_Click, sender, e);
    private void MessageItem_MouseEnter(object sender, MouseEventArgs e) => ForwardMouse(MainWindowInteractionTemplateAction.MessageItem_MouseEnter, sender, e);
    private void MessageCopyMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageCopyMenu_Click, sender, e);
    private void MessageEditMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageEditMenu_Click, sender, e);
    private void MessageForkMenu_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageForkMenu_Click, sender, e);
    private void WorkMessageExpander_Expanded(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkMessageExpander_Expanded, sender, e);
    private void WorkMessageExpander_Collapsed(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.WorkMessageExpander_Collapsed, sender, e);
    private void MessageCancelEditButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageCancelEditButton_Click, sender, e);
    private void MessageSendEditButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageSendEditButton_Click, sender, e);
    private void MessageCopyButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageCopyButton_Click, sender, e);
    private void MessageEditButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageEditButton_Click, sender, e);
    private void MessageForkButton_Click(object sender, RoutedEventArgs e) => ForwardRouted(MainWindowInteractionTemplateAction.MessageForkButton_Click, sender, e);
    private void MessageExpandButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: MessageViewModel message })
        {
            message.ToggleTextExpanded();
            e.Handled = true;
        }
    }

    private void WorkCommandCopyButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { DataContext: WorkCommandInvocationViewModel command }
            && !string.IsNullOrWhiteSpace(command.CommandText))
        {
            Clipboard.SetText(command.CommandText);
            e.Handled = true;
        }
    }
}
