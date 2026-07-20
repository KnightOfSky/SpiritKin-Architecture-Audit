using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal void BrowseModelLibraryPath()
    {
        var selected = SelectModelLibraryPath();
        if (string.IsNullOrWhiteSpace(selected))
        {
            return;
        }
        WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text = selected;
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已选择模型库路径：{selected}";
    }

    internal string? SelectModelLibraryPath()
    {
        var initial = Directory.Exists(WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text.Trim()) ? WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text.Trim() : @"E:\AIModel";
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = "选择 Ollama / llama.cpp GGUF 模型库目录",
            InitialDirectory = initial,
            Multiselect = false,
        };
        return dialog.ShowDialog(OwnerWindow()) == true
            ? dialog.FolderName
            : null;
    }

    internal async Task SyncModelLibraryAsync(string? providerFilter = null, bool promptForPath = false)
    {
        if (promptForPath)
        {
            var selected = SelectModelLibraryPath();
            if (string.IsNullOrWhiteSpace(selected))
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已取消选择模型库路径。";
                return;
            }
            WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text = selected;
        }
        var root = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text) ? @"E:\AIModel" : WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text.Trim();
        if (!Directory.Exists(root))
        {
            var selected = SelectModelLibraryPath();
            if (string.IsNullOrWhiteSpace(selected))
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"模型库路径不存在：{root}";
                return;
            }
            WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text = selected;
            root = selected;
        }
        var discovered = DiscoverLocalLibraryModels(root)
            .Where(model => string.IsNullOrWhiteSpace(providerFilter) || string.Equals(model.Provider, providerFilter, StringComparison.OrdinalIgnoreCase))
            .GroupBy(model => $"{model.Provider}|{model.Model}", StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First())
            .ToList();
        if (discovered.Count == 0)
        {
            var label = string.IsNullOrWhiteSpace(providerFilter) ? "Ollama 或 llama.cpp" : providerFilter;
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"未在模型库中发现 {label} 模型：{root}";
            return;
        }
        var saved = 0;
        foreach (var model in discovered)
        {
            try
            {
                var modelId = ResolveSyncedModelId(model);
                using var _ = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
                {
                    action = "save_assist_model",
                    model = new
                    {
                        model_id = modelId,
                        display_name = model.DisplayName,
                        provider = model.Provider,
                        endpoint = model.Endpoint,
                        model = model.Model,
                        api_key = "",
                        keep_existing_key = true,
                        enabled = true,
                        role = "primary_worker",
                        priority = model.Provider == "ollama" ? 100 : 95,
                        notes = $"由模型库同步：{root}",
                    }
                });
                saved += 1;
            }
            catch (Exception ex)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"同步模型失败：{model.DisplayName} · {ex.Message}";
                return;
            }
        }
        await LoadLearningAsync();
        var providerText = string.IsNullOrWhiteSpace(providerFilter) ? "全部" : providerFilter;
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已同步 {providerText} 模型库：{saved} 个模型 · {root}";
    }

    internal string ResolveSyncedModelId(LibraryModelCandidate candidate)
    {
        var existing = _assistModels.FirstOrDefault(item =>
            string.Equals(item.Provider, candidate.Provider, StringComparison.OrdinalIgnoreCase)
            && string.Equals(item.Model, candidate.Model, StringComparison.OrdinalIgnoreCase));
        return existing?.ModelId ?? candidate.ModelId;
    }

    internal static IEnumerable<LibraryModelCandidate> DiscoverLocalLibraryModels(string root)
    {
        var manifestRoots = new[]
        {
            Path.Combine(root, "manifests"),
            Path.Combine(root, "models", "manifests"),
            Path.Combine(root, ".ollama", "models", "manifests"),
        }
        .Concat(FindOllamaManifestRoots(root))
        .Distinct(StringComparer.OrdinalIgnoreCase);
        foreach (var manifests in manifestRoots.Where(Directory.Exists))
        {
            foreach (var file in Directory.EnumerateFiles(manifests, "*", SearchOption.AllDirectories))
            {
                var relative = Path.GetRelativePath(manifests, file);
                var parts = relative.Split(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 3)
                {
                    continue;
                }
                var name = parts[^2];
                var tag = parts[^1];
                var modelName = $"{name}:{tag}";
                yield return new LibraryModelCandidate(
                    StableModelId("ollama", modelName),
                    $"Ollama · {modelName}",
                    "ollama",
                    "http://127.0.0.1:11434",
                    modelName);
            }
        }

        var ggufRoots = new[]
        {
            Path.Combine(root, "lmstudio-community"),
            Path.Combine(root, "models"),
            root,
        }
        .Distinct(StringComparer.OrdinalIgnoreCase);
        foreach (var ggufRoot in ggufRoots.Where(Directory.Exists))
        {
            foreach (var file in Directory.EnumerateFiles(ggufRoot, "*.gguf", SearchOption.AllDirectories))
            {
                var folder = Path.GetFileName(Path.GetDirectoryName(file) ?? "");
                var modelName = Path.GetFileNameWithoutExtension(file);
                if (modelName.StartsWith("mmproj-", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                var display = string.IsNullOrWhiteSpace(folder) ? modelName : $"{folder} / {modelName}";
                yield return new LibraryModelCandidate(
                    StableModelId("llamacpp", display),
                    $"llama.cpp · {display}",
                    "llamacpp",
                    "http://127.0.0.1:8080/v1",
                    modelName);
            }
        }
    }

    internal static IEnumerable<string> FindOllamaManifestRoots(string root)
    {
        if (!Directory.Exists(root))
        {
            yield break;
        }

        var pending = new Queue<(string Path, int Depth)>();
        pending.Enqueue((root, 0));
        while (pending.Count > 0)
        {
            var current = pending.Dequeue();
            if (current.Depth > 5)
            {
                continue;
            }

            IEnumerable<string> children;
            try
            {
                children = Directory.EnumerateDirectories(current.Path).ToArray();
            }
            catch
            {
                continue;
            }

            foreach (var child in children)
            {
                if (string.Equals(Path.GetFileName(child), "manifests", StringComparison.OrdinalIgnoreCase))
                {
                    yield return child;
                    continue;
                }
                pending.Enqueue((child, current.Depth + 1));
            }
        }
    }

    internal static string StableModelId(string prefix, string value)
    {
        var normalized = new string(value.ToLowerInvariant().Select(ch => char.IsLetterOrDigit(ch) ? ch : '_').ToArray());
        normalized = string.Join("_", normalized.Split('_', StringSplitOptions.RemoveEmptyEntries));
        return $"{prefix}_{normalized}".TrimEnd('_');
    }

    internal sealed record LibraryModelCandidate(string ModelId, string DisplayName, string Provider, string Endpoint, string Model);

}

