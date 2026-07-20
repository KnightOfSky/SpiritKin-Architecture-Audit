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
    internal void NewAgent()
    {
        var agent = new AgentViewModel(
            UniqueId("agent", _agents.Select(item => item.AgentId)),
            "新 Agent",
            "general",
            "specialist",
            "cloud_openai_compatible",
            "",
            "",
            "native",
            _agentAdapters.FirstOrDefault()?.AdapterId ?? "coordinator_router",
            true,
            50,
            Array.Empty<string>(),
            Array.Empty<string>(),
            "",
            "",
            "",
            "");
        var defaultKb = BuildDefaultKnowledgeBaseForAgent(agent);
        _knowledgeBases.Add(defaultKb);
        agent = agent.WithKnowledgeBase(defaultKb.KnowledgeBaseId, defaultKb.Path);
        _agents.Add(agent);
        WorkbenchShell.ManagementPanels.AgentsList.SelectedValue = agent.AgentId;
        RenderSelectedAgentEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已新增 Agent：{agent.AgentId}。编辑后点“应用修改”，再保存集群配置。";
    }

    internal bool ApplySelectedAgentFromEditor(bool showMessage)
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.AgentsList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _agents.Count ? _agents[selectedIndex].AgentId : UniqueId("agent", _agents.Select(item => item.AgentId));
        var updated = BuildAgentFromEditor(fallbackId);
        if (_agents.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.AgentId, updated.AgentId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"Agent ID 已存在：{updated.AgentId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _agents.Count)
            {
                _agents[selectedIndex] = updated;
            }
            else
            {
                _agents.Add(updated);
            }
            WorkbenchShell.ManagementPanels.AgentsList.SelectedValue = updated.AgentId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用 Agent 修改：{updated.AgentId}";
        }
        return true;
    }

    internal AgentViewModel BuildAgentFromEditor(string fallbackId)
    {
        var priority = int.TryParse(WorkbenchShell.ManagementPanels.AgentPriorityBox.Text.Trim(), out var parsedPriority) ? parsedPriority : 50;
        var agentId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.AgentIdBox.Text.Trim();
        var label = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentLabelBox.Text) ? agentId : WorkbenchShell.ManagementPanels.AgentLabelBox.Text.Trim();
        var selectedModelId = (WorkbenchShell.ManagementPanels.AgentModelSelectBox.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.AgentModelIdBox.Text.Trim();
        var selectedModel = _assistModels.FirstOrDefault(item => string.Equals(item.ModelId, selectedModelId, StringComparison.OrdinalIgnoreCase));
        var provider = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentProviderBox.Text) && selectedModel is not null ? selectedModel.Provider : WorkbenchShell.ManagementPanels.AgentProviderBox.Text.Trim();
        var model = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentModelBox.Text) && selectedModel is not null ? selectedModel.Model : WorkbenchShell.ManagementPanels.AgentModelBox.Text.Trim();
        selectedModelId = string.IsNullOrWhiteSpace(selectedModelId) && selectedModel is not null ? selectedModel.ModelId : selectedModelId;
        var knowledgeBaseId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentKnowledgeBaseIdBox.Text) ? $"kb_{agentId}" : WorkbenchShell.ManagementPanels.AgentKnowledgeBaseIdBox.Text.Trim();
        var knowledgeBasePath = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text) ? $"state/knowledge_bases/agents/{agentId}" : WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text.Trim();
        var brainProfile = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentBrainProfileBox.Text) ? $"{agentId}_brain" : WorkbenchShell.ManagementPanels.AgentBrainProfileBox.Text.Trim();
        var selectedAdapter = ((WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.Text).Trim();
        if (string.IsNullOrWhiteSpace(selectedAdapter))
        {
            selectedAdapter = _agentAdapters.FirstOrDefault()?.AdapterId ?? "coordinator_router";
        }
        return new AgentViewModel(
            agentId,
            label,
            WorkbenchShell.ManagementPanels.AgentDomainBox.Text.Trim(),
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentRoleBox.Text) ? "specialist" : WorkbenchShell.ManagementPanels.AgentRoleBox.Text.Trim(),
            provider,
            model,
            selectedModelId,
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.AgentFrameworkBox)) ? "native" : ComboText(WorkbenchShell.ManagementPanels.AgentFrameworkBox),
            selectedAdapter,
            WorkbenchShell.ManagementPanels.AgentEnabledBox.IsChecked == true,
            priority,
            WorkbenchShell.ManagementPanels.AgentCapabilitiesBox.Text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
            WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries),
            knowledgeBaseId,
            knowledgeBasePath,
            brainProfile,
            WorkbenchShell.ManagementPanels.AgentNotesBox.Text.Trim());
    }

    internal void DeleteSelectedAgent()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.AgentsList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _agents.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的 Agent。";
            return;
        }
        if (_agents.Count <= 1)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "至少保留一个 Agent。";
            return;
        }
        var removed = _agents[selectedIndex];
        if (!ConfirmDestructiveAction("删除 Agent", $"确定要删除 Agent“{removed.AgentId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _agents.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.AgentsList.SelectedValue = _agents[Math.Min(selectedIndex, _agents.Count - 1)].AgentId;
        RenderSelectedAgentEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已删除 Agent：{removed.AgentId}。保存集群配置后生效。";
    }

    internal void OpenAgentAssistantMenu()
    {
        var selectedAgent = BuildAgentFromEditor(WorkbenchShell.ManagementPanels.AgentsList.SelectedIndex >= 0 && WorkbenchShell.ManagementPanels.AgentsList.SelectedIndex < _agents.Count
            ? _agents[WorkbenchShell.ManagementPanels.AgentsList.SelectedIndex].AgentId
            : string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentIdBox.Text) ? "agent" : WorkbenchShell.ManagementPanels.AgentIdBox.Text.Trim());
        var selectedIds = WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var menu = new ContextMenu { PlacementTarget = WorkbenchShell.ManagementPanels.AgentChooseAssistantsButton, Placement = PlacementMode.Bottom };
        AddDisabledMenuHeader(menu, "Agent 助手白名单");
        var candidates = _externalAssistants
            .Where(item => item.Enabled)
            .OrderByDescending(item => IsAssistantRelevantToAgent(item, selectedAgent))
            .ThenBy(item => item.Category)
            .ThenBy(item => item.Label)
            .ToList();
        if (candidates.Count == 0)
        {
            AddDisabledMenuHeader(menu, "No enabled assistants");
        }
        foreach (var assistant in candidates)
        {
            var matched = selectedIds.Contains(assistant.AssistantId);
            var category = string.IsNullOrWhiteSpace(assistant.Category) ? "general" : assistant.Category;
            AddContextMenuItem(menu, $"{(matched ? "✓ " : "")}{assistant.Label} · {category}", (_, _) =>
            {
                ToggleAgentAssistantId(assistant.AssistantId);
            });
        }
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "Use relevant enabled assistants", (_, _) =>
        {
            var ids = candidates
                .Where(item => IsAssistantRelevantToAgent(item, selectedAgent))
                .Select(item => item.AssistantId)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text = string.Join(Environment.NewLine, ids);
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = ids.Length == 0
                ? "当前 Agent 没有匹配领域的已启用助手。"
                : $"已设置 {selectedAgent.AgentId} 的助手白名单：{ids.Length} 个。";
        });
        AddContextMenuItem(menu, "Clear allowlist", (_, _) =>
        {
            WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Clear();
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "已清空当前 Agent 的助手白名单。";
        });
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void ToggleAgentAssistantId(string assistantId)
    {
        var selected = WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToList();
        var index = selected.FindIndex(item => string.Equals(item, assistantId, StringComparison.OrdinalIgnoreCase));
        if (index >= 0)
        {
            selected.RemoveAt(index);
        }
        else
        {
            selected.Add(assistantId);
        }
        WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text = string.Join(Environment.NewLine, selected.Distinct(StringComparer.OrdinalIgnoreCase));
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已更新助手白名单：{assistantId}";
    }

    internal static bool IsAssistantRelevantToAgent(ExternalAssistantViewModel assistant, AgentViewModel agent)
    {
        var category = assistant.Category;
        return string.Equals(category, "general", StringComparison.OrdinalIgnoreCase)
            || string.Equals(category, agent.Domain, StringComparison.OrdinalIgnoreCase)
            || agent.Capabilities.Any(capability => string.Equals(category, capability, StringComparison.OrdinalIgnoreCase));
    }

    internal void BrowseAgentKnowledgeBasePath()
    {
        var selected = SelectKnowledgeBasePath(WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text);
        if (string.IsNullOrWhiteSpace(selected))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text = ToWorkspaceRelativePath(selected);
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已选择 Agent 知识库路径：{WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text}";
    }

}
