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
    internal async Task NewKnowledgeBaseAsync()
    {
        var knowledge = new KnowledgeBaseViewModel(
            UniqueId("kb", _knowledgeBases.Select(item => item.KnowledgeBaseId)),
            "新知识库",
            "",
            "general",
            "state/knowledge_bases/custom",
            "agent",
            true,
            "");
        _knowledgeBases.Add(knowledge);
        WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = knowledge.KnowledgeBaseId;
        RenderSelectedKnowledgeBaseEditor();
        await SaveKnowledgeBaseConfigurationAsync($"已新增并保存知识库：{knowledge.KnowledgeBaseId}。");
    }

    internal bool ApplySelectedKnowledgeBaseFromEditor(bool showMessage)
    {
        if (_knowledgeBases.Count == 0 && !KnowledgeBaseEditorHasContent())
        {
            return true;
        }
        var selectedIndex = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _knowledgeBases.Count
            ? _knowledgeBases[selectedIndex].KnowledgeBaseId
            : UniqueId("kb", _knowledgeBases.Select(item => item.KnowledgeBaseId));
        var updated = BuildKnowledgeBaseFromEditor(fallbackId);
        if (_knowledgeBases.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.KnowledgeBaseId, updated.KnowledgeBaseId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"知识库 ID 已存在：{updated.KnowledgeBaseId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _knowledgeBases.Count)
            {
                _knowledgeBases[selectedIndex] = updated;
            }
            else
            {
                _knowledgeBases.Add(updated);
            }
            WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = updated.KnowledgeBaseId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用知识库修改：{updated.KnowledgeBaseId}";
        }
        return true;
    }

    internal KnowledgeBaseViewModel BuildKnowledgeBaseFromEditor(string fallbackId)
    {
        var knowledgeBaseId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBaseIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.KnowledgeBaseIdBox.Text.Trim();
        return new KnowledgeBaseViewModel(
            knowledgeBaseId,
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBaseLabelBox.Text) ? knowledgeBaseId : WorkbenchShell.ManagementPanels.KnowledgeBaseLabelBox.Text.Trim(),
            ((WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.Text).Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseDomainBox)) ? "general" : ComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseDomainBox),
            WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseScopeBox)) ? "agent" : ComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseScopeBox),
            WorkbenchShell.ManagementPanels.KnowledgeBaseEnabledBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.KnowledgeBaseNotesBox.Text.Trim());
    }

    internal async Task SaveSelectedKnowledgeBaseFromEditorAsync(bool showMessage)
    {
        if (!ApplySelectedKnowledgeBaseFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
        if (string.IsNullOrWhiteSpace(selectedId))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择知识库。";
            return;
        }
        var message = showMessage
            ? $"已应用并保存知识库修改：{selectedId}"
            : $"已保存知识库配置：{selectedId}";
        await SaveKnowledgeBaseConfigurationAsync(message);
    }

    internal async Task DeleteSelectedKnowledgeBaseAsync()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _knowledgeBases.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的知识库。";
            return;
        }
        var removed = _knowledgeBases[selectedIndex];
        var usedByAgent = _agents.FirstOrDefault(agent => string.Equals(agent.KnowledgeBaseId, removed.KnowledgeBaseId, StringComparison.OrdinalIgnoreCase));
        if (usedByAgent is not null)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"知识库正在被 Agent 使用：{usedByAgent.AgentId}";
            return;
        }
        if (!ConfirmDestructiveAction("删除知识库", $"确定要删除知识库“{removed.KnowledgeBaseId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _knowledgeBases.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = _knowledgeBases.Count == 0 ? null : _knowledgeBases[Math.Min(selectedIndex, _knowledgeBases.Count - 1)].KnowledgeBaseId;
        RenderSelectedKnowledgeBaseEditor();
        await SaveKnowledgeBaseConfigurationAsync($"已删除并保存知识库：{removed.KnowledgeBaseId}。");
    }

    internal void OpenSelectedAgentKnowledgeBase()
    {
        if (!ApplySelectedAgentFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.AgentsList.SelectedValue as string;
        var agent = _agents.FirstOrDefault(item => item.AgentId == selectedId);
        if (agent is null)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择 Agent。";
            return;
        }
        var knowledge = EnsureKnowledgeBaseForAgent(agent);
        SelectListBoxItemByTag(WorkbenchShell.ManagementPanels.AgentSubNavList, "knowledge");
        WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = knowledge.KnowledgeBaseId;
        RenderSelectedKnowledgeBaseEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已定位知识库：{knowledge.KnowledgeBaseId}";
    }

    internal KnowledgeBaseViewModel EnsureKnowledgeBaseForAgent(AgentViewModel agent)
    {
        var knowledgeId = string.IsNullOrWhiteSpace(agent.KnowledgeBaseId) ? $"kb_{agent.AgentId}" : agent.KnowledgeBaseId;
        var existing = _knowledgeBases.FirstOrDefault(item => string.Equals(item.KnowledgeBaseId, knowledgeId, StringComparison.OrdinalIgnoreCase));
        if (existing is not null)
        {
            return existing;
        }
        var knowledge = BuildDefaultKnowledgeBaseForAgent(agent.WithKnowledgeBase(knowledgeId, string.IsNullOrWhiteSpace(agent.KnowledgeBasePath) ? $"state/knowledge_bases/agents/{agent.AgentId}" : agent.KnowledgeBasePath));
        _knowledgeBases.Add(knowledge);
        return knowledge;
    }

    internal KnowledgeBaseViewModel BuildDefaultKnowledgeBaseForAgent(AgentViewModel agent)
    {
        var knowledgeId = string.IsNullOrWhiteSpace(agent.KnowledgeBaseId) ? $"kb_{agent.AgentId}" : agent.KnowledgeBaseId;
        var path = string.IsNullOrWhiteSpace(agent.KnowledgeBasePath) ? $"state/knowledge_bases/agents/{agent.AgentId}" : agent.KnowledgeBasePath;
        return new KnowledgeBaseViewModel(
            knowledgeId,
            $"{agent.Label} 知识库",
            agent.AgentId,
            string.IsNullOrWhiteSpace(agent.Domain) ? "general" : agent.Domain,
            path,
            "agent",
            true,
            "");
    }

    internal void OpenSelectedKnowledgeBaseFolder()
    {
        if (!ApplySelectedKnowledgeBaseFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
        var knowledge = _knowledgeBases.FirstOrDefault(item => item.KnowledgeBaseId == selectedId);
        if (knowledge is null)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择知识库。";
            return;
        }
        var path = ResolveKnowledgeBasePath(knowledge.Path);
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", path) { UseShellExecute = true });
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已打开知识库目录：{path}";
    }

    internal async Task ImportKnowledgeBaseFilesAsync()
    {
        if (!ApplySelectedKnowledgeBaseFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
        var knowledge = _knowledgeBases.FirstOrDefault(item => item.KnowledgeBaseId == selectedId);
        if (knowledge is null)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择知识库。";
            return;
        }
        var dialog = new OpenFileDialog
        {
            Title = "导入知识库文件",
            Multiselect = true,
            CheckFileExists = true,
            Filter = "Knowledge files|*.md;*.markdown;*.txt;*.rst;*.log;*.py;*.json;*.jsonl;*.yaml;*.yml;*.csv|All files|*.*",
        };
        if (dialog.ShowDialog(Application.Current.MainWindow) != true || dialog.FileNames.Length == 0)
        {
            return;
        }

        var targetRoot = ResolveKnowledgeBasePath(knowledge.Path);
        Directory.CreateDirectory(targetRoot);
        var imported = 0;
        foreach (var source in dialog.FileNames.Where(File.Exists))
        {
            var safeName = SafeKnowledgeFileName(Path.GetFileName(source));
            if (string.IsNullOrWhiteSpace(safeName))
            {
                continue;
            }
            var destination = UniqueFilePath(Path.Combine(targetRoot, safeName));
            File.Copy(source, destination);
            imported++;
        }
        var report = BuildKnowledgeBaseIndexReport(knowledge, writeManifest: true);
        WorkbenchShell.ManagementPanels.KnowledgeBaseIndexStatusText.Text = report;
        await SaveKnowledgeBaseConfigurationAsync($"已导入 {imported} 个文件到 {knowledge.KnowledgeBaseId}，并保存配置。{report}");
    }

    internal async Task IndexSelectedKnowledgeBaseAsync()
    {
        if (!ApplySelectedKnowledgeBaseFromEditor(showMessage: false))
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
        var knowledge = _knowledgeBases.FirstOrDefault(item => item.KnowledgeBaseId == selectedId);
        if (knowledge is null)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择知识库。";
            return;
        }
        var report = BuildKnowledgeBaseIndexReport(knowledge, writeManifest: true);
        WorkbenchShell.ManagementPanels.KnowledgeBaseIndexStatusText.Text = report;
        await SaveKnowledgeBaseConfigurationAsync($"{report} · 配置已保存");
    }

    internal async Task RebuildAllKnowledgeIndexesAsync()
    {
        try
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "正在重建全部启用知识库索引...";
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/knowledge-base", new { action = "index_all" });
            EnsureOkResponse(doc.RootElement, "重建知识库索引失败");
            var indexed = doc.RootElement.TryGetProperty("indexed", out var indexedElement) && indexedElement.ValueKind == JsonValueKind.Array ? indexedElement.GetArrayLength() : 0;
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已重建 {indexed} 个知识库索引。";
            if (doc.RootElement.TryGetProperty("knowledge_base", out var kb))
            {
                RenderKnowledgeSources(kb);
            }
            await LoadSearchManagementAsync();
            await LoadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"重建知识库索引失败：{ex.Message}";
        }
    }

}
