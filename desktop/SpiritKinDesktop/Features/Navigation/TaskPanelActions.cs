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
    internal async Task SaveSelectedTaskAsync()
    {
        var task = Workspace.SelectedTask();
        if (task is null)
        {
            return;
        }
        task.Title = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.TaskTitleEditBox.Text) ? task.Title : WorkbenchShell.ManagementPanels.TaskTitleEditBox.Text.Trim();
        task.Status = ComboText(WorkbenchShell.ManagementPanels.TaskStatusBox);
        task.Detail = WorkbenchShell.ManagementPanels.TaskDetailEditBox.Text.Trim();
        task.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

    internal async Task DeleteSelectedTaskAsync()
    {
        var task = Workspace.SelectedTask();
        if (task is null)
        {
            return;
        }
        await DeleteTaskByIdAsync(task.Id);
    }

    internal async void TaskStart_Click(object sender, RoutedEventArgs e) => await UpdateTaskStatusFromButtonAsync(sender, "running");

    internal async void TaskComplete_Click(object sender, RoutedEventArgs e) => await UpdateTaskStatusFromButtonAsync(sender, "complete");

    internal async void TaskBlocked_Click(object sender, RoutedEventArgs e) => await UpdateTaskStatusFromButtonAsync(sender, "blocked");

    internal async void TaskDelete_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string id)
        {
            await DeleteTaskByIdAsync(id);
        }
    }

    internal async Task DeleteTaskByIdAsync(string id)
    {
        var task = _state.Tasks.FirstOrDefault(item => item.Id == id);
        if (task is null)
        {
            return;
        }
        if (!ConfirmDestructiveAction("删除任务", $"确定要删除任务“{task.Title}”吗？"))
        {
            return;
        }
        _state.Tasks.RemoveAll(item => item.Id == id);
        _pendingDeletedTaskIds.Add(id);
        RenderState();
        WorkspaceSidebar.ConnectionStatusText.Text = $"任务已删除：{task.Title}";
        await SaveStateAsync();
    }

    internal async Task UpdateTaskStatusFromButtonAsync(object sender, string status)
    {
        if (sender is not Button button || button.Tag is not string id)
        {
            return;
        }
        var task = _state.Tasks.FirstOrDefault(item => item.Id == id);
        if (task is null)
        {
            return;
        }
        task.Status = status;
        task.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
    }

}

