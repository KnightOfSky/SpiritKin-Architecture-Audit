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
    internal void OpenSelectedDailyItem()
    {
        if (WorkbenchShell.ManagementPanels.DailyItemsList.SelectedItem is not ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "先选择一个日报事项。";
            return;
        }
        switch (item.Kind)
        {
            case "task":
                Workspace.OpenManagementPage("tasks", "tasks");
                SelectTaskByTitle(item.Target);
                break;
            case "log_error":
                Workspace.OpenManagementPage("logs");
                SelectLogByIdOrTitle(item.Target);
                break;
            case "service":
                Workspace.OpenManagementPage("services");
                SelectServiceByLabel(item.Target);
                break;
            case "learning":
                SelectSkillFromDaily(item.Target);
                break;
            default:
                WorkbenchShell.ManagementPanels.DailyActionText.Text = $"暂无针对 {item.Kind} 的定位动作。";
                break;
        }
    }

    internal async Task UpdateSelectedDailyTaskStatusAsync(string status)
    {
        if (WorkbenchShell.ManagementPanels.DailyItemsList.SelectedItem is not ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "先选择一个日报任务。";
            return;
        }
        if (item.Kind != "task")
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "当前事项不是任务。";
            return;
        }
        var task = FindTaskByTitle(item.Target);
        if (task is null)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "未找到同名任务，无法更新状态。";
            return;
        }
        task.Status = status;
        task.UpdatedAt = NowSeconds();
        RenderState();
        await SaveStateAsync();
        await LoadDailyAsync();
        WorkbenchShell.ManagementPanels.DailyActionText.Text = $"任务已更新为 {status}：{task.Title}";
    }

    internal void CopySelectedContextSuggestion()
    {
        if (WorkbenchShell.ManagementPanels.ContextSuggestionsList.SelectedItem is not ActionItemViewModel item)
        {
            WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = "先选择一个项目建议。";
            return;
        }
        var text = string.IsNullOrWhiteSpace(item.Command) ? item.Target : item.Command;
        if (string.IsNullOrWhiteSpace(text))
        {
            WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = "该建议没有可复制内容。";
            return;
        }
        Clipboard.SetText(text);
        WorkbenchShell.ManagementPanels.ContextSuggestionActionText.Text = string.IsNullOrWhiteSpace(item.Command) ? "建议说明已复制。" : "建议命令已复制。";
    }

    internal void SelectTaskByTitle(string title)
    {
        var task = FindTask(title);
        if (task is null)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "已切换到任务管理；未找到对应任务。";
            return;
        }
        WorkspaceSidebar.TasksList.SelectedValue = task.Id;
        WorkbenchShell.ManagementPanels.RightTasksList.SelectedValue = task.Id;
        WorkbenchShell.ManagementPanels.DailyActionText.Text = $"已定位任务：{task.Title}";
        Workspace.RenderEditors();
    }

    internal DesktopItem? FindTaskByTitle(string title)
    {
        return FindTask(title);
    }

    internal DesktopItem? FindTask(string target)
    {
        if (string.IsNullOrWhiteSpace(target))
        {
            return null;
        }
        return _state.Tasks.FirstOrDefault(item => string.Equals(item.Id, target, StringComparison.OrdinalIgnoreCase))
            ?? _state.Tasks.FirstOrDefault(item => string.Equals(item.Title, target, StringComparison.OrdinalIgnoreCase))
            ?? _state.Tasks.FirstOrDefault(item => item.Title.Contains(target, StringComparison.OrdinalIgnoreCase) || target.Contains(item.Title, StringComparison.OrdinalIgnoreCase));
    }

    internal void SelectLogByIdOrTitle(string title)
    {
        var log = _logs.FirstOrDefault(item => string.Equals(item.LogId, title, StringComparison.OrdinalIgnoreCase) || item.LogId.Contains(title, StringComparison.OrdinalIgnoreCase) || title.Contains(item.LogId, StringComparison.OrdinalIgnoreCase));
        if (log is null)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "已切换到日志页；未找到对应日志。";
            return;
        }
        WorkbenchShell.ManagementPanels.LogsList.SelectedValue = log.LogId;
        WorkbenchShell.ManagementPanels.DailyActionText.Text = $"已定位日志：{log.Label}";
    }

    internal void SelectServiceByLabel(string title)
    {
        var service = Services.FindServiceForDailyTitle(title);
        if (service is null)
        {
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "已切换到服务页；未找到对应服务。";
            return;
        }
        WorkbenchShell.ManagementPanels.ServicesList.SelectedValue = service.ServiceId;
        WorkbenchShell.ManagementPanels.DailyActionText.Text = $"已定位服务：{service.Label}";
    }

    internal void SelectSkillFromDaily(string target)
    {
        var skill = _skills.FirstOrDefault(item => string.Equals(item.Name, target, StringComparison.OrdinalIgnoreCase))
            ?? _skills.FirstOrDefault(item => item.Name.Contains(target, StringComparison.OrdinalIgnoreCase) || target.Contains(item.Name, StringComparison.OrdinalIgnoreCase));
        if (skill is null)
        {
            Workspace.OpenManagementPage("learning");
            WorkbenchShell.ManagementPanels.DailyActionText.Text = "已切换到学习管理；未找到对应 Skill。";
            return;
        }
        Workspace.OpenManagementPage("skills");
        WorkbenchShell.ManagementPanels.SkillsList.SelectedValue = skill.Name;
        RenderSelectedSkillEditor();
        WorkbenchShell.ManagementPanels.DailyActionText.Text = $"已定位 Skill：{skill.Name}";
    }

}

