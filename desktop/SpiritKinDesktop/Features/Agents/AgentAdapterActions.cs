using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace SpiritKinDesktop;

internal sealed partial class AgentsController
{
    internal void NewAgentAdapter()
    {
        var adapter = new AgentAdapterViewModel(
            UniqueId("adapter", _agentAdapters.Select(item => item.AdapterId)),
            "新 Agent Adapter",
            "native",
            "spiritkin_native",
            "",
            "",
            "",
            "",
            true,
            false,
            false,
            Array.Empty<string>(),
            Array.Empty<string>(),
            "unknown",
            "",
            "");
        _agentAdapters.Add(adapter);
        WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue = adapter.AdapterId;
        RenderSelectedAgentAdapterEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已新增 AgentAdapter：{adapter.AdapterId}。";
    }

    internal bool ApplySelectedAgentAdapterFromEditor(bool showMessage)
    {
        if (_agentAdapters.Count == 0 && !AgentAdapterEditorHasContent())
        {
            return true;
        }
        var selectedIndex = WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _agentAdapters.Count
            ? _agentAdapters[selectedIndex].AdapterId
            : UniqueId("adapter", _agentAdapters.Select(item => item.AdapterId));
        var updated = BuildAgentAdapterFromEditor(fallbackId);
        if (_agentAdapters.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.AdapterId, updated.AdapterId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"Adapter ID 已存在：{updated.AdapterId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _agentAdapters.Count)
            {
                _agentAdapters[selectedIndex] = updated;
            }
            else
            {
                _agentAdapters.Add(updated);
            }
            WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue = updated.AdapterId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用 Adapter 修改：{updated.AdapterId}";
        }
        return true;
    }

    internal AgentAdapterViewModel BuildAgentAdapterFromEditor(string fallbackId)
    {
        var adapterId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Text.Trim();
        return new AgentAdapterViewModel(
            adapterId,
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterLabelBox.Text) ? adapterId : WorkbenchShell.ManagementPanels.AgentAdapterLabelBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.AgentAdapterKindBox)) ? "native" : ComboText(WorkbenchShell.ManagementPanels.AgentAdapterKindBox),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.AgentAdapterFrameworkBox)) ? "spiritkin_native" : ComboText(WorkbenchShell.ManagementPanels.AgentAdapterFrameworkBox),
            WorkbenchShell.ManagementPanels.AgentAdapterCommandBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.AgentAdapterModuleBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.AgentAdapterEndpointBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.AgentAdapterWorkingDirectoryBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.AgentAdapterEnabledBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.AgentAdapterAllowWriteBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.AgentAdapterReviewOnlyBox.IsChecked == true,
            SplitLines(WorkbenchShell.ManagementPanels.AgentAdapterCapabilitiesBox.Text),
            SplitLines(WorkbenchShell.ManagementPanels.AgentAdapterOwnerAgentsBox.Text),
            "pending_save",
            "保存后由后端重新评估。",
            WorkbenchShell.ManagementPanels.AgentAdapterNotesBox.Text.Trim());
    }

    internal void UseSelectedAdapterForCurrentAgent()
    {
        if (!ApplySelectedAgentAdapterFromEditor(showMessage: false))
        {
            return;
        }
        var adapterId = (WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Text.Trim();
        var adapter = _agentAdapters.FirstOrDefault(item => string.Equals(item.AdapterId, adapterId, StringComparison.OrdinalIgnoreCase));
        if (adapter is null)
        {
            WorkbenchShell.ManagementPanels.AgentAdapterStatusText.Text = "请先选择 Adapter。";
            return;
        }
        SetComboText(WorkbenchShell.ManagementPanels.AgentFrameworkBox, adapter.Framework);
        WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedValue = adapter.AdapterId;
        if (WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedItem is null)
        {
            WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.Text = adapter.AdapterId;
        }
        ApplySelectedAgentFromEditor(showMessage: false);
        WorkbenchShell.ManagementPanels.AgentAdapterStatusText.Text = $"已将 {adapter.AdapterId} 设置给当前 Agent，保存集群配置后生效。";
    }

    internal void DeleteSelectedAgentAdapter()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _agentAdapters.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的 Adapter。";
            return;
        }
        var removed = _agentAdapters[selectedIndex];
        if (_agents.Any(agent => string.Equals(agent.Adapter, removed.AdapterId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"Adapter {removed.AdapterId} 仍被 Agent 使用，请先切换相关 Agent。";
            return;
        }
        if (!ConfirmDestructiveAction("删除 AgentAdapter", $"确定要删除 Adapter“{removed.AdapterId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _agentAdapters.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue = _agentAdapters.Count == 0 ? null : _agentAdapters[Math.Min(selectedIndex, _agentAdapters.Count - 1)].AdapterId;
        RenderSelectedAgentAdapterEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已删除 Adapter：{removed.AdapterId}。保存集群配置后生效。";
    }

}
