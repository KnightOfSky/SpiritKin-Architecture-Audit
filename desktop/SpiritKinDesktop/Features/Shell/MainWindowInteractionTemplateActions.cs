using System;
using System.Windows;
using System.Windows.Input;

namespace SpiritKinDesktop;

internal enum MainWindowInteractionTemplateAction
{
    SessionPinMenu_Click,
    SessionRenameMenu_Click,
    SessionArchiveMenu_Click,
    SessionUnreadMenu_Click,
    SessionOpenExplorerMenu_Click,
    SessionCopyWorkingDirMenu_Click,
    SessionCopyIdMenu_Click,
    SessionCopyDeeplinkMenu_Click,
    SessionDeleteMenu_Click,
    ProjectNewChatMenu_Click,
    ProjectRenameMenu_Click,
    ProjectArchiveMenu_Click,
    ProjectOpenManagementMenu_Click,
    SidebarOpenExplorerMenu_Click,
    SidebarCopyWorkingDirMenu_Click,
    ProjectSessionPinMenu_Click,
    ProjectSessionRenameMenu_Click,
    ProjectSessionArchiveMenu_Click,
    ProjectSessionUnreadMenu_Click,
    SidebarCopyIdMenu_Click,
    SidebarCopyDeeplinkMenu_Click,
    SidebarDeleteMenu_Click,
    TaskStart_Click,
    TaskComplete_Click,
    TaskBlocked_Click,
    TaskDelete_Click,
    WorkflowGraphEdge_MouseLeftButtonDown,
    WorkflowGraphEdgeEditNoteMenu_Click,
    WorkflowGraphEdgeBreakMenu_Click,
    WorkflowGraphInputPort_MouseLeftButtonUp,
    WorkflowGraphOutputPort_MouseLeftButtonDown,
    WorkflowGraphOutputPort_MouseMove,
    WorkflowGraphOutputPort_MouseLeftButtonUp,
    WorkflowGraphNode_MouseLeftButtonDown,
    WorkflowGraphNode_MouseMove,
    WorkflowGraphNode_MouseLeftButtonUp,
    WorkflowGraphNodeEditMenu_Click,
    WorkflowGraphNodeApplyInspectorMenu_Click,
    WorkflowGraphNodeConnectSourceMenu_Click,
    WorkflowGraphNodeAddAgentChildMenu_Click,
    WorkflowGraphNodeAddToolChildMenu_Click,
    WorkflowGraphNodeAddSkillChildMenu_Click,
    WorkflowGraphNodeAddReviewChildMenu_Click,
    WorkflowGraphNodeAddBranchChildMenu_Click,
    WorkflowGraphNodeAddWaiterChildMenu_Click,
    WorkflowGraphNodeAddCallbackChildMenu_Click,
    WorkflowGraphNodeAddSubgraphChildMenu_Click,
    WorkflowGraphNodeAddCustomChildMenu_Click,
    WorkflowGraphNodeDuplicateMenu_Click,
    WorkflowGraphNodeDisconnectInputsMenu_Click,
    WorkflowGraphNodeDisconnectOutputsMenu_Click,
    WorkflowGraphNodeRepairMenu_Click,
    WorkflowGraphNodeDeleteMenu_Click,
    GitChangeItem_MouseEnter,
    RemoveAttachmentButton_Click,
    ServiceStart_Click,
    ServiceRestart_Click,
    ServiceStop_Click,
    MessageItem_MouseEnter,
    MessageCopyMenu_Click,
    MessageEditMenu_Click,
    MessageForkMenu_Click,
    WorkMessageExpander_Expanded,
    WorkMessageExpander_Collapsed,
    MessageCancelEditButton_Click,
    MessageSendEditButton_Click,
    MessageCopyButton_Click,
    MessageEditButton_Click,
    MessageForkButton_Click,
}

public partial class MainWindow
{
    internal void HandleInteractionTemplateAction(MainWindowInteractionTemplateAction action, object sender, EventArgs args)
    {
        switch (action)
        {
            case MainWindowInteractionTemplateAction.SessionPinMenu_Click:
            case MainWindowInteractionTemplateAction.SessionRenameMenu_Click:
            case MainWindowInteractionTemplateAction.SessionArchiveMenu_Click:
            case MainWindowInteractionTemplateAction.SessionUnreadMenu_Click:
            case MainWindowInteractionTemplateAction.SessionOpenExplorerMenu_Click:
            case MainWindowInteractionTemplateAction.SessionCopyWorkingDirMenu_Click:
            case MainWindowInteractionTemplateAction.SessionCopyIdMenu_Click:
            case MainWindowInteractionTemplateAction.SessionCopyDeeplinkMenu_Click:
            case MainWindowInteractionTemplateAction.SessionDeleteMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectNewChatMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectRenameMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectArchiveMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectOpenManagementMenu_Click:
            case MainWindowInteractionTemplateAction.SidebarOpenExplorerMenu_Click:
            case MainWindowInteractionTemplateAction.SidebarCopyWorkingDirMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectSessionPinMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectSessionRenameMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectSessionArchiveMenu_Click:
            case MainWindowInteractionTemplateAction.ProjectSessionUnreadMenu_Click:
            case MainWindowInteractionTemplateAction.SidebarCopyIdMenu_Click:
            case MainWindowInteractionTemplateAction.SidebarCopyDeeplinkMenu_Click:
            case MainWindowInteractionTemplateAction.SidebarDeleteMenu_Click:
            case MainWindowInteractionTemplateAction.TaskStart_Click:
            case MainWindowInteractionTemplateAction.TaskComplete_Click:
            case MainWindowInteractionTemplateAction.TaskBlocked_Click:
            case MainWindowInteractionTemplateAction.TaskDelete_Click:
                _navigationController.HandleInteractionTemplateAction(action, sender, args);
                break;
            case MainWindowInteractionTemplateAction.WorkflowGraphEdge_MouseLeftButtonDown:
            case MainWindowInteractionTemplateAction.WorkflowGraphEdgeEditNoteMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphEdgeBreakMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphInputPort_MouseLeftButtonUp:
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonDown:
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseMove:
            case MainWindowInteractionTemplateAction.WorkflowGraphOutputPort_MouseLeftButtonUp:
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonDown:
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseMove:
            case MainWindowInteractionTemplateAction.WorkflowGraphNode_MouseLeftButtonUp:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeEditMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeApplyInspectorMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeConnectSourceMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddAgentChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddToolChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSkillChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddReviewChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddBranchChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddWaiterChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCallbackChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddSubgraphChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeAddCustomChildMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDuplicateMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectInputsMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDisconnectOutputsMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeRepairMenu_Click:
            case MainWindowInteractionTemplateAction.WorkflowGraphNodeDeleteMenu_Click:
                _workflowController.HandleInteractionTemplateAction(action, sender, args);
                break;
            case MainWindowInteractionTemplateAction.GitChangeItem_MouseEnter:
                _workbenchController.GitChangeItem_MouseEnter(sender, (MouseEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.RemoveAttachmentButton_Click:
                _composerController.RemoveAttachmentButton_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ServiceStart_Click:
                _ = _servicesController.ServiceActionFromButtonAsync(sender, "start");
                break;
            case MainWindowInteractionTemplateAction.ServiceRestart_Click:
                _ = _servicesController.ServiceActionFromButtonAsync(sender, "restart");
                break;
            case MainWindowInteractionTemplateAction.ServiceStop_Click:
                _ = _servicesController.ServiceActionFromButtonAsync(sender, "stop");
                break;
            case MainWindowInteractionTemplateAction.MessageItem_MouseEnter:
            case MainWindowInteractionTemplateAction.MessageCopyMenu_Click:
            case MainWindowInteractionTemplateAction.MessageEditMenu_Click:
            case MainWindowInteractionTemplateAction.MessageForkMenu_Click:
            case MainWindowInteractionTemplateAction.WorkMessageExpander_Expanded:
            case MainWindowInteractionTemplateAction.WorkMessageExpander_Collapsed:
            case MainWindowInteractionTemplateAction.MessageCancelEditButton_Click:
            case MainWindowInteractionTemplateAction.MessageSendEditButton_Click:
            case MainWindowInteractionTemplateAction.MessageCopyButton_Click:
            case MainWindowInteractionTemplateAction.MessageEditButton_Click:
            case MainWindowInteractionTemplateAction.MessageForkButton_Click:
                _runtimeController.HandleInteractionTemplateAction(action, sender, args);
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(action), action, null);
        }
    }
}
