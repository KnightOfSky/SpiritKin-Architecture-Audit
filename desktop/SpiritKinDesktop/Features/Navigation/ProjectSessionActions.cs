using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class NavigationController
{
    internal ProjectViewModel? SelectedManagedProjectSession()
    {
        return WorkbenchShell.ManagementPanels.ProjectSessionsList.SelectedItem as ProjectViewModel;
    }

    internal async Task RenameSelectedProjectSessionAsync()
    {
        if (SelectedManagedProjectSession() is not { } item)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionStatusText.Text = "请先选择项目会话。";
            return;
        }
        await RenameSessionAsync(item.SessionId);
    }

    internal async Task ToggleSelectedProjectSessionArchiveAsync()
    {
        if (SelectedManagedProjectSession() is not { } item)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionStatusText.Text = "请先选择项目会话。";
            return;
        }
        await ToggleSessionArchiveAsync(item.SessionId);
        Workspace.OpenManagementPage("tasks", "projects");
    }

    internal async Task DeleteSelectedProjectSessionAsync()
    {
        if (SelectedManagedProjectSession() is not { } item)
        {
            WorkbenchShell.ManagementPanels.ProjectSessionStatusText.Text = "请先选择项目会话。";
            return;
        }
        await DeleteSessionByIdAsync(item.SessionId);
        Workspace.OpenManagementPage("tasks", "projects");
    }

}
