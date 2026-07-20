using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal async Task DeleteCurrentSelectionAsync()
    {
        if (WorkbenchShell.ManagementPanels.WorkflowsPanel.Visibility == Visibility.Visible)
        {
            if (WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedItem is WorkflowEditNodeViewModel)
            {
                Workflows.DeleteWorkflowEditNode();
                return;
            }
            await Workflows.DeleteWorkflowDefinitionAsync();
            return;
        }
        if (WorkbenchShell.ManagementPanels.TasksPanel.Visibility == Visibility.Visible)
        {
            var taskPage = (WorkbenchShell.ManagementPanels.TaskSubNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "sessions";
            switch (taskPage)
            {
                case "projects":
                    if (WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedItem is ProjectViewModel)
                    {
                        await DeleteSelectedProjectSessionAsync();
                    }
                    else
                    {
                        await DeleteSelectedProjectAsync();
                    }
                    return;
                case "tasks":
                    await DeleteSelectedTaskAsync();
                    return;
                default:
                    await DeleteSelectedSessionAsync();
                    return;
            }
        }
        if (WorkbenchShell.ManagementPanels.SkillsPanel.Visibility == Visibility.Visible)
        {
            await DeleteSkillAsync();
            return;
        }
        if (WorkbenchShell.ManagementPanels.QuickCommandsPanel.Visibility == Visibility.Visible)
        {
            await DeleteQuickCommandAsync();
            return;
        }
        if (WorkbenchShell.ManagementPanels.ModelsPanel.Visibility == Visibility.Visible)
        {
            await Learning.DeleteAssistModelAsync();
            return;
        }
        if (WorkbenchShell.ManagementPanels.LogsPanel.Visibility == Visibility.Visible)
        {
            await DeleteSelectedLogAsync();
            return;
        }
        if (WorkbenchShell.ManagementPanels.AgentManagementPanel.Visibility == Visibility.Visible)
        {
            var page = (WorkbenchShell.ManagementPanels.AgentSubNavList.SelectedItem as ListBoxItem)?.Tag as string ?? "policy";
            switch (page)
            {
                case "assistants":
                    Agents.DeleteSelectedExternalAssistant();
                    return;
                case "adapters":
                    Agents.DeleteSelectedAgentAdapter();
                    return;
                case "agents":
                    Agents.DeleteSelectedAgent();
                    return;
                case "knowledge":
                    await Agents.DeleteSelectedKnowledgeBaseAsync();
                    return;
                case "routes":
                    Agents.DeleteSelectedRouteProfile();
                    return;
                case "remote":
                    Agents.DeleteSelectedRemoteTarget();
                    return;
            }
        }
        await DeleteSelectedSessionAsync();
    }
}
