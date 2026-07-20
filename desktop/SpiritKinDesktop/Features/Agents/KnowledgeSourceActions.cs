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
    internal void NewKnowledgeSource()
    {
        var source = new KnowledgeSourceViewModel(
            UniqueId("source", _knowledgeSources.Select(item => item.SourceId)),
            "新外部知识源",
            "folder",
            "",
            _knowledgeBases.FirstOrDefault()?.KnowledgeBaseId ?? "wiki_project_knowledge",
            true,
            true,
            Array.Empty<string>(),
            Array.Empty<string>(),
            "",
            "",
            0,
            0,
            "draft");
        _knowledgeSources.Add(source);
        WorkbenchShell.ManagementPanels.KnowledgeSourcesList.SelectedValue = source.SourceId;
        RenderSelectedKnowledgeSourceEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "已创建外部知识源草稿，保存后生效。";
    }

    internal async Task SaveKnowledgeSourceAsync()
    {
        var payload = BuildKnowledgeSourcePayload("register_source");
        if (payload is null)
        {
            return;
        }
        await KnowledgeSourceActionAsync(payload, "外部知识源已保存。");
    }

    internal async Task SyncKnowledgeSourceAsync()
    {
        var sourceId = WorkbenchShell.ManagementPanels.KnowledgeSourceIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(sourceId))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择或填写外部源 ID。";
            return;
        }
        await KnowledgeSourceActionAsync(new { action = "sync_source", source_id = sourceId, index_after = true }, "外部知识源已同步并索引。");
    }

    internal async Task DeleteKnowledgeSourceAsync()
    {
        var sourceId = WorkbenchShell.ManagementPanels.KnowledgeSourceIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(sourceId))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择外部源。";
            return;
        }
        if (!ConfirmDestructiveAction("删除外部知识源", $"确定要删除外部知识源“{sourceId}”吗？已同步到知识库的文件不会自动删除。"))
        {
            return;
        }
        await KnowledgeSourceActionAsync(new { action = "delete_source", source_id = sourceId }, "外部知识源已删除。");
    }

    internal Dictionary<string, object?>? BuildKnowledgeSourcePayload(string action)
    {
        var sourceId = WorkbenchShell.ManagementPanels.KnowledgeSourceIdBox.Text.Trim();
        var path = WorkbenchShell.ManagementPanels.KnowledgeSourcePathBox.Text.Trim();
        var kbId = (WorkbenchShell.ManagementPanels.KnowledgeSourceTargetBox.SelectedValue as string) ?? "";
        if (string.IsNullOrWhiteSpace(sourceId))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "外部源 ID 不能为空。";
            return null;
        }
        if (string.IsNullOrWhiteSpace(path))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "外部源路径不能为空。";
            return null;
        }
        if (string.IsNullOrWhiteSpace(kbId))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请选择目标知识库。";
            return null;
        }
        return new Dictionary<string, object?>
        {
            ["action"] = action,
            ["source_id"] = sourceId,
            ["label"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeSourceLabelBox.Text) ? sourceId : WorkbenchShell.ManagementPanels.KnowledgeSourceLabelBox.Text.Trim(),
            ["kind"] = ComboText(WorkbenchShell.ManagementPanels.KnowledgeSourceKindBox),
            ["path"] = path,
            ["knowledge_base_id"] = kbId,
            ["enabled"] = WorkbenchShell.ManagementPanels.KnowledgeSourceEnabledBox.IsChecked == true,
            ["recursive"] = WorkbenchShell.ManagementPanels.KnowledgeSourceRecursiveBox.IsChecked == true,
            ["ignore_patterns"] = SplitLines(WorkbenchShell.ManagementPanels.KnowledgeSourceIgnoreBox.Text),
            ["tag_filter"] = SplitLines(WorkbenchShell.ManagementPanels.KnowledgeSourceTagsBox.Text),
            ["notes"] = WorkbenchShell.ManagementPanels.KnowledgeSourceNotesBox.Text.Trim(),
        };
    }

    internal async Task KnowledgeSourceActionAsync(object payload, string successMessage)
    {
        try
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "正在处理外部知识源...";
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/knowledge-base", payload);
            EnsureOkResponse(doc.RootElement, successMessage);
            if (doc.RootElement.TryGetProperty("knowledge_base", out var kb))
            {
                RenderKnowledgeSources(kb);
                RefreshKnowledgeBaseIndexStatuses(kb);
            }
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = successMessage;
            await LoadSearchManagementAsync();
            await LoadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"外部知识源动作失败：{ex.Message}";
        }
    }

    internal void RefreshKnowledgeBaseIndexStatuses(JsonElement state)
    {
        if (!state.TryGetProperty("knowledge_bases", out var knowledgeBases) || knowledgeBases.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var item in knowledgeBases.EnumerateArray())
        {
            var id = ReadJsonString(item, "knowledge_base_id");
            var existing = _knowledgeBases.FirstOrDefault(kb => string.Equals(kb.KnowledgeBaseId, id, StringComparison.OrdinalIgnoreCase));
            if (existing is null)
            {
                continue;
            }
            var index = _knowledgeBases.IndexOf(existing);
            _knowledgeBases[index] = existing.WithServerIndex(
                ReadJsonInt(item, "file_count"),
                item.TryGetProperty("last_index", out var lastIndex) && lastIndex.ValueKind == JsonValueKind.Object ? ReadJsonString(lastIndex, "updated_at") : "");
        }
        RenderSelectedKnowledgeBaseEditor();
    }

    internal void BrowseKnowledgeSourcePath()
    {
        var current = WorkbenchShell.ManagementPanels.KnowledgeSourcePathBox.Text.Trim();
        var initial = Directory.Exists(current) ? current : _rootDir;
        var dialog = new OpenFolderDialog
        {
            Title = "选择外部知识源目录",
            InitialDirectory = initial,
            Multiselect = false,
        };
        if (dialog.ShowDialog(Application.Current.MainWindow) == true)
        {
            WorkbenchShell.ManagementPanels.KnowledgeSourcePathBox.Text = dialog.FolderName;
        }
    }

    internal string BuildKnowledgeBaseIndexReport(KnowledgeBaseViewModel knowledge, bool writeManifest)
    {
        var path = ResolveKnowledgeBasePath(knowledge.Path);
        Directory.CreateDirectory(path);
        var files = Directory
            .EnumerateFiles(path, "*", SearchOption.AllDirectories)
            .Where(IsKnowledgeTextFile)
            .ToList();
        var bytes = files.Sum(file => new FileInfo(file).Length);
        if (writeManifest)
        {
            var manifest = new
            {
                knowledge_base_id = knowledge.KnowledgeBaseId,
                path,
                file_count = files.Count,
                size_bytes = bytes,
                updated_at = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
                files = files
                    .Take(80)
                    .Select(file => Path.GetRelativePath(path, file).Replace('\\', '/'))
                    .ToArray(),
            };
            File.WriteAllText(Path.Combine(path, ".spiritkin_kb_index.json"), JsonSerializer.Serialize(manifest, _jsonOptions), Encoding.UTF8);
        }
        if (!writeManifest && !string.IsNullOrWhiteSpace(knowledge.ServerIndexedAt))
        {
            return $"索引状态：{knowledge.KnowledgeBaseId} · 服务端文件 {knowledge.ServerFileCount} · 最近索引 {knowledge.ServerIndexedAt}";
        }
        return $"索引状态：{knowledge.KnowledgeBaseId} · 文档 {files.Count} · {FormatByteCount(bytes)}";
    }

}
