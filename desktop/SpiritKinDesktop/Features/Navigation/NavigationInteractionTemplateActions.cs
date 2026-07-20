using System;
using System.Windows;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal void HandleInteractionTemplateAction(MainWindowInteractionTemplateAction action, object sender, EventArgs args)
    {
        switch (action)
        {
            case MainWindowInteractionTemplateAction.SessionPinMenu_Click:
                SessionPinMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionRenameMenu_Click:
                SessionRenameMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionArchiveMenu_Click:
                SessionArchiveMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionUnreadMenu_Click:
                SessionUnreadMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionOpenExplorerMenu_Click:
                SessionOpenExplorerMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionCopyWorkingDirMenu_Click:
                SessionCopyWorkingDirMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionCopyIdMenu_Click:
                SessionCopyIdMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionCopyDeeplinkMenu_Click:
                SessionCopyDeeplinkMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SessionDeleteMenu_Click:
                SessionDeleteMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectNewChatMenu_Click:
                ProjectNewChatMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectRenameMenu_Click:
                ProjectRenameMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectArchiveMenu_Click:
                ProjectArchiveMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectOpenManagementMenu_Click:
                ProjectOpenManagementMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SidebarOpenExplorerMenu_Click:
                SidebarOpenExplorerMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SidebarCopyWorkingDirMenu_Click:
                SidebarCopyWorkingDirMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectSessionPinMenu_Click:
                ProjectSessionPinMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectSessionRenameMenu_Click:
                ProjectSessionRenameMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectSessionArchiveMenu_Click:
                ProjectSessionArchiveMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.ProjectSessionUnreadMenu_Click:
                ProjectSessionUnreadMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SidebarCopyIdMenu_Click:
                SidebarCopyIdMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SidebarCopyDeeplinkMenu_Click:
                SidebarCopyDeeplinkMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.SidebarDeleteMenu_Click:
                SidebarDeleteMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.TaskStart_Click:
                TaskStart_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.TaskComplete_Click:
                TaskComplete_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.TaskBlocked_Click:
                TaskBlocked_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.TaskDelete_Click:
                TaskDelete_Click(sender, (RoutedEventArgs)args);
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(action), action, null);
        }
    }
}
