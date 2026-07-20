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
    internal void TasksList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkspaceSidebar.TasksList.SelectedValue is string id)
        {
            WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue = id;
        }
        Workspace.RenderEditors();
    }

    internal void RightTasksList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        if (WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue is string id)
        {
            WorkspaceSidebar.TasksList.SelectedValue = id;
        }
        Workspace.RenderEditors();
    }

    internal async Task SaveSelectedSessionAsync()
    {
        var session = ManagedEditorSession();
        session.Title = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.SessionTitleEditBox.Text) ? session.Title : WorkbenchShell.ManagementPanels.SessionTitleEditBox.Text.Trim();
        session.Status = ComboText(WorkbenchShell.ManagementPanels.SessionStatusBox);
        session.UpdatedAt = NowSeconds();
        _state.ActiveSessionId = session.Id;
        RenderState();
        WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = session.Id;
        WorkbenchShell.ManagementPanels.SessionManagementStatusText.Text = $"已保存会话：{session.Title}";
        await SaveStateAsync();
    }

    internal async Task DeleteSelectedSessionAsync()
    {
        var session = ManagedEditorSession();
        await DeleteSessionByIdAsync(session.Id);
    }

    internal async Task ArchiveSelectedSessionAsync()
    {
        var session = ManagedEditorSession();
        await ToggleSessionArchiveAsync(session.Id);
        WorkbenchShell.ManagementPanels.ManagedSessionsList.SelectedValue = session.Id;
    }

}

