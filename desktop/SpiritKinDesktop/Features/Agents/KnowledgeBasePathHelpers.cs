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
    internal void BrowseKnowledgeBasePath()
    {
        var selected = SelectKnowledgeBasePath(WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text);
        if (string.IsNullOrWhiteSpace(selected))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text = ToWorkspaceRelativePath(selected);
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已选择知识库路径：{WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text}";
    }

    internal string? SelectKnowledgeBasePath(string currentValue)
    {
        var current = ResolveKnowledgeBasePath(currentValue);
        var initial = Directory.Exists(current)
            ? current
            : Directory.Exists(Path.Combine(_rootDir, "state", "knowledge_bases"))
                ? Path.Combine(_rootDir, "state", "knowledge_bases")
                : _rootDir;
        var dialog = new OpenFolderDialog
        {
            Title = "选择知识库目录",
            InitialDirectory = initial,
            Multiselect = false,
        };
        return dialog.ShowDialog(Application.Current.MainWindow) == true ? dialog.FolderName : null;
    }

    internal string ResolveKnowledgeBasePath(string raw)
    {
        var value = string.IsNullOrWhiteSpace(raw) ? "state/knowledge_bases/custom" : raw.Trim();
        var expanded = Environment.ExpandEnvironmentVariables(value);
        return Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(_rootDir, expanded));
    }

    internal static bool IsKnowledgeTextFile(string path)
    {
        var ext = Path.GetExtension(path).ToLowerInvariant();
        return ext is ".md" or ".markdown" or ".txt" or ".rst" or ".log" or ".py" or ".json" or ".jsonl" or ".yaml" or ".yml" or ".csv";
    }

    internal static string SafeKnowledgeFileName(string fileName)
    {
        var name = Regex.Replace(fileName, @"[^0-9A-Za-z\u4e00-\u9fff._ -]+", "_").Trim(' ', '.');
        return string.IsNullOrWhiteSpace(name) ? "knowledge.txt" : name;
    }

    internal static string UniqueFilePath(string path)
    {
        if (!File.Exists(path))
        {
            return path;
        }
        var directory = Path.GetDirectoryName(path) ?? "";
        var name = Path.GetFileNameWithoutExtension(path);
        var extension = Path.GetExtension(path);
        for (var i = 2; i < 1000; i++)
        {
            var candidate = Path.Combine(directory, $"{name}-{i}{extension}");
            if (!File.Exists(candidate))
            {
                return candidate;
            }
        }
        return Path.Combine(directory, $"{name}-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}{extension}");
    }

    internal static string FormatByteCount(long bytes)
    {
        if (bytes <= 0)
        {
            return "--";
        }
        if (bytes < 1024)
        {
            return $"{bytes} B";
        }
        if (bytes < 1024 * 1024)
        {
            return $"{bytes / 1024.0:0.#} KB";
        }
        return $"{bytes / 1024.0 / 1024.0:0.#} MB";
    }

    internal string ToWorkspaceRelativePath(string path)
    {
        var full = Path.GetFullPath(path);
        var root = Path.GetFullPath(_rootDir);
        if (!root.EndsWith(Path.DirectorySeparatorChar))
        {
            root += Path.DirectorySeparatorChar;
        }
        return full.StartsWith(root, StringComparison.OrdinalIgnoreCase)
            ? Path.GetRelativePath(_rootDir, full)
            : full;
    }

}
