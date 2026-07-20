using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    internal void RunStartupSmokeChecks()
    {
        var failures = new List<string>();

        Require(WorkspaceSidebar.WorkspaceNavList, nameof(WorkspaceSidebar.WorkspaceNavList), failures);
        Require(WorkbenchShell.RightNavList, nameof(WorkbenchShell.RightNavList), failures);
        Require(WorkbenchShell.ManagementPanels.TaskSubNavList, nameof(WorkbenchShell.ManagementPanels.TaskSubNavList), failures);
        Require(WorkbenchShell.ManagementPanels.AgentSubNavList, nameof(WorkbenchShell.ManagementPanels.AgentSubNavList), failures);
        Require(ChatWorkspace.PromptBox, nameof(ChatWorkspace.PromptBox), failures);
        Require(ChatWorkspace.EmptyPromptBox, nameof(ChatWorkspace.EmptyPromptBox), failures);
        Require(ChatWorkspace.MessagesList, nameof(ChatWorkspace.MessagesList), failures);
        Require(WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList, nameof(WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList), failures);
        Require(WorkbenchShell.ManagementPanels.WorkflowNodeTemplateBox, nameof(WorkbenchShell.ManagementPanels.WorkflowNodeTemplateBox), failures);
        Require(WorkbenchShell.ManagementPanels.ArchiveWorkflowRunButton, nameof(WorkbenchShell.ManagementPanels.ArchiveWorkflowRunButton), failures);
        Require(WorkbenchShell.ManagementPanels.CleanupWorkflowRunsButton, nameof(WorkbenchShell.ManagementPanels.CleanupWorkflowRunsButton), failures);
        Require(WorkbenchShell.ManagementPanels.MobileAndroidReceiverUrlBox, nameof(WorkbenchShell.ManagementPanels.MobileAndroidReceiverUrlBox), failures);
        Require(WorkbenchShell.ManagementPanels.MobileSecurityText, nameof(WorkbenchShell.ManagementPanels.MobileSecurityText), failures);
        Require(WorkbenchShell.ManagementPanels.MobileWorkspaceBox, nameof(WorkbenchShell.ManagementPanels.MobileWorkspaceBox), failures);
        Require(WorkbenchShell.ManagementPanels.StateMaintenanceText, nameof(WorkbenchShell.ManagementPanels.StateMaintenanceText), failures);
        Require(WorkbenchShell.ManagementPanels.ServicesList, nameof(WorkbenchShell.ManagementPanels.ServicesList), failures);
        Require(WorkspaceSidebar.ConnectionStatusText, nameof(WorkspaceSidebar.ConnectionStatusText), failures);
        Require(WorkbenchShell.ManagementPanelHost, nameof(WorkbenchShell.ManagementPanelHost), failures);

        Expect(WorkspaceSidebar.ApiUrlBox.Text.StartsWith("http://127.0.0.1:", StringComparison.OrdinalIgnoreCase), "WorkspaceSidebar.ApiUrlBox default local URL is missing.", failures);
        Expect(WorkspaceSidebar.WsUrlBox.Text.StartsWith("ws://127.0.0.1:", StringComparison.OrdinalIgnoreCase), "WorkspaceSidebar.WsUrlBox default local URL is missing.", failures);
        Expect(WorkspaceSidebar.WorkspaceNavList.SelectedItem is ListBoxItem { Tag: "chat" }, "WorkspaceSidebar.WorkspaceNavList should default to chat.", failures);
        Expect(WorkbenchShell.RightNavList.SelectedItem is ListBoxItem, "WorkbenchShell.RightNavList should have a selected management page.", failures);
        Expect(WorkbenchShell.ManagementPanelHost.Visibility == Visibility.Collapsed, "WorkbenchShell.ManagementPanelHost should be collapsed on startup.", failures);
        Expect(ChatWorkspace.MessagesList.ItemsSource is not null, "ChatWorkspace.MessagesList ItemsSource is not wired.", failures);
        Expect(WorkbenchShell.ManagementPanels.ServicesList.ItemsSource is not null, "ServicesList ItemsSource is not wired.", failures);
        Expect(WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.ItemsSource is not null, "WorkflowDefinitionCatalogList ItemsSource is not wired.", failures);
        Expect(WorkbenchShell.ManagementPanels.WorkflowNodeTemplateBox.ItemsSource is not null, "WorkflowNodeTemplateBox ItemsSource is not wired.", failures);
        Expect(_workflowController.NodeTemplates.Count >= 8, "Workflow node templates were not initialized.", failures);
        Expect(_workflowController.NodeTemplates.Any(item => string.Equals(item.NodeType, "workflow.android_step", StringComparison.OrdinalIgnoreCase)), "Android workflow node template is missing.", failures);
        Expect(ChatWorkspace.SendButton.IsEnabled, "ChatWorkspace.SendButton should be enabled on startup.", failures);
        Expect(WorkbenchShell.ManagementPanels.RefreshWorkflowsButton.IsEnabled, "RefreshWorkflowsButton should be enabled on startup.", failures);
        Expect(WorkbenchShell.ManagementPanels.ArchiveWorkflowRunButton.IsEnabled, "ArchiveWorkflowRunButton should be enabled on startup.", failures);
        Expect(WorkbenchShell.ManagementPanels.CleanupWorkflowRunsButton.IsEnabled, "CleanupWorkflowRunsButton should be enabled on startup.", failures);
        Expect(WorkbenchShell.ManagementPanels.RefreshMobileManagementButton.IsEnabled, "RefreshMobileManagementButton should be enabled on startup.", failures);
        Expect(WorkbenchShell.ManagementPanels.CleanupStateMaintenanceButton.IsEnabled, "CleanupStateMaintenanceButton should be enabled on startup.", failures);
        Expect(ChatWorkspace.SafetyStopButton.IsEnabled, "ChatWorkspace.SafetyStopButton should be enabled on startup.", failures);
        Expect(ChatWorkspace.SafetyStopButton.Visibility == Visibility.Visible, "ChatWorkspace.SafetyStopButton should remain visible on startup.", failures);
        Expect(ChatWorkspace.SafetyResumeButton.Visibility == Visibility.Visible, "ChatWorkspace.SafetyResumeButton should remain visible on startup.", failures);

        if (failures.Count > 0)
        {
            throw new InvalidOperationException("Startup smoke checks failed: " + string.Join("; ", failures));
        }
    }

    private static void Require(object? value, string name, ICollection<string> failures)
    {
        if (value is null)
        {
            failures.Add($"{name} is null");
        }
    }

    private static void Expect(bool condition, string message, ICollection<string> failures)
    {
        if (!condition)
        {
            failures.Add(message);
        }
    }
}


