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
    internal void QuickCommandsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_rendering)
        {
            return;
        }
        RenderSelectedQuickCommand();
    }

    internal void NewQuickCommand()
    {
        WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue = null;
        WorkbenchShell.ManagementPanels.QuickCommandTitleBox.Text = $"指令 {DateTime.Now:HH:mm:ss}";
        WorkbenchShell.ManagementPanels.QuickCommandTextBox.Text = "";
        WorkbenchShell.ManagementPanels.QuickCommandStatusText.Text = "输入名称和内容后点击保存。";
        WorkbenchShell.ManagementPanels.QuickCommandTitleBox.Focus();
        WorkbenchShell.ManagementPanels.QuickCommandTitleBox.SelectAll();
    }

    internal void RenderSelectedQuickCommand()
    {
        var id = WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue as string;
        var command = _state.QuickCommands.FirstOrDefault(item => item.Id == id);
        WorkbenchShell.ManagementPanels.QuickCommandTitleBox.Text = command?.Title ?? "";
        WorkbenchShell.ManagementPanels.QuickCommandTextBox.Text = command?.Command ?? "";
    }

    internal async Task SaveQuickCommandAsync()
    {
        var title = WorkbenchShell.ManagementPanels.QuickCommandTitleBox.Text.Trim();
        var commandText = WorkbenchShell.ManagementPanels.QuickCommandTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(commandText))
        {
            WorkspaceSidebar.ConnectionStatusText.Text = "快速指令需要名称和内容。";
            return;
        }
        var id = WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue as string;
        var existing = _state.QuickCommands.FirstOrDefault(item => item.Id == id);
        if (existing is null)
        {
            existing = new QuickCommand { Id = NewId("quick_command") };
            _state.QuickCommands.Add(existing);
        }
        existing.Title = title;
        existing.Command = commandText;
        existing.UpdatedAt = NowSeconds();
        RenderState();
        WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue = existing.Id;
        WorkbenchShell.ManagementPanels.QuickCommandStatusText.Text = $"已保存快速指令：{existing.Title}";
        WorkspaceSidebar.ConnectionStatusText.Text = $"快速指令已保存：{existing.Title}";
        await SaveStateAsync();
    }

    internal async Task DeleteQuickCommandAsync()
    {
        var id = WorkbenchShell.ManagementPanels.QuickCommandsList.SelectedValue as string;
        if (string.IsNullOrWhiteSpace(id))
        {
            return;
        }
        var command = _state.QuickCommands.FirstOrDefault(item => item.Id == id);
        var title = command?.Title ?? id;
        if (!ConfirmDestructiveAction("删除快速指令", $"确定要删除快速指令“{title}”吗？"))
        {
            return;
        }
        _state.QuickCommands.RemoveAll(item => item.Id == id);
        WorkbenchShell.ManagementPanels.QuickCommandTitleBox.Clear();
        WorkbenchShell.ManagementPanels.QuickCommandTextBox.Clear();
        RenderState();
        WorkbenchShell.ManagementPanels.QuickCommandStatusText.Text = "快速指令已删除。";
        WorkspaceSidebar.ConnectionStatusText.Text = "快速指令已删除。";
        await SaveStateAsync();
    }

}

