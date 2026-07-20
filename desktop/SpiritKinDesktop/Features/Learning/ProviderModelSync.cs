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
    internal async Task TestSelectedProviderAsync()
    {
        var config = BuildSelectedProviderConfig();
        if (string.IsNullOrWhiteSpace(config.Endpoint))
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 缺少接口地址。";
            return;
        }
        var selectedModel = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedItem as AssistModelViewModel;
        var hasSavedKey = selectedModel is not null
            && string.Equals(selectedModel.Provider, config.Provider, StringComparison.OrdinalIgnoreCase)
            && selectedModel.ApiKeySet;
        if (config.RequiresApiKey && string.IsNullOrWhiteSpace(config.ApiKey) && !hasSavedKey)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 测试连接需要在 API Key 输入框临时填入 Key；保存过的 Key 不会在桌面端明文回显。";
            return;
        }
        WorkbenchShell.ManagementPanels.TestProviderButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"正在测试 {config.DisplayName}...";
        try
        {
            if (config.RequiresApiKey && string.IsNullOrWhiteSpace(config.ApiKey) && hasSavedKey)
            {
                await TestProviderViaBackendAsync(config, selectedModel!.ModelId);
                return;
            }
            var models = await FetchProviderModelsAsync(config);
            var sample = models.Count == 0 ? "--" : string.Join(", ", models.Take(3));
            PopulateCloudModelOptions(models);
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 连接正常 · 发现 {models.Count} 个模型 · {sample}";
            if (models.Count > 0 && string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.CloudModelBox.Text))
            {
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = models[0];
            }
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 测试失败：{ex.Message}";
        }
        finally
        {
            WorkbenchShell.ManagementPanels.TestProviderButton.IsEnabled = true;
            SyncProviderServiceButtonState();
        }
    }

    internal async Task SyncSelectedProviderModelsAsync(bool promptForPath = false)
    {
        var config = BuildSelectedProviderConfig();
        if (!config.SupportsModelSync)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 暂不支持模型同步。";
            return;
        }
        WorkbenchShell.ManagementPanels.SyncProviderModelsButton.IsEnabled = false;
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"正在同步 {config.DisplayName} 模型...";
        try
        {
            IReadOnlyList<string> models;
            try
            {
                models = await FetchProviderModelsAsync(config);
            }
            catch when (config.LocalService && IsLibraryScanProvider(config.Provider))
            {
                await SyncModelLibraryAsync(config.Provider, promptForPath);
                return;
            }

            if (models.Count == 0 && config.LocalService && IsLibraryScanProvider(config.Provider))
            {
                await SyncModelLibraryAsync(config.Provider, promptForPath);
                return;
            }
            if (models.Count == 0)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 没有返回可同步模型。";
                return;
            }

            if (!config.LocalService)
            {
                PopulateCloudModelOptions(models);
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.CloudModelBox.Text) ? models[0] : WorkbenchShell.ManagementPanels.CloudModelBox.Text;
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 已读取 {models.Count} 个模型；云端通道不批量登记，确认模型名后保存当前配置。";
                return;
            }

            var saved = await SaveDiscoveredProviderModelsAsync(config, models, "由 Provider 服务同步");
            await LoadLearningAsync();
            if (saved.Count > 0)
            {
                WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = saved[0];
                RenderSelectedAssistModelEditor();
            }
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已同步 {config.DisplayName}：{saved.Count}/{models.Count} 个模型。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 同步失败：{ex.Message}";
        }
        finally
        {
            WorkbenchShell.ManagementPanels.SyncProviderModelsButton.IsEnabled = true;
            SyncProviderServiceButtonState();
        }
    }

    internal async Task<List<string>> SaveDiscoveredProviderModelsAsync(SelectedProviderConfig config, IEnumerable<string> models, string notes)
    {
        var savedIds = new List<string>();
        var uniqueModels = models
            .Where(model => !string.IsNullOrWhiteSpace(model))
            .Select(model => model.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
        for (var index = 0; index < uniqueModels.Length; index++)
        {
            var modelName = uniqueModels[index];
            var modelId = ResolveProviderModelId(config.Provider, modelName);
            using var _ = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
            {
                action = "save_assist_model",
                model = new
                {
                    model_id = modelId,
                    display_name = $"{config.DisplayName} · {modelName}",
                    provider = config.Provider,
                    endpoint = config.Endpoint,
                    model = modelName,
                    api_key = config.ApiKey,
                    keep_existing_key = true,
                    enabled = true,
                    role = config.LocalService ? "primary_worker" : "reviewer",
                    priority = (config.LocalService ? 100 : 70) - Math.Min(index, 30),
                    notes,
                }
            });
            savedIds.Add(modelId);
        }
        return savedIds;
    }

    internal string ResolveProviderModelId(string provider, string modelName)
    {
        var existing = _assistModels.FirstOrDefault(item =>
            string.Equals(item.Provider, provider, StringComparison.OrdinalIgnoreCase)
            && string.Equals(item.Model, modelName, StringComparison.OrdinalIgnoreCase));
        return existing?.ModelId ?? StableModelId(provider, modelName);
    }

    internal async Task<IReadOnlyList<string>> FetchProviderModelsAsync(SelectedProviderConfig config)
    {
        if (string.Equals(config.Protocol, "ollama", StringComparison.OrdinalIgnoreCase)
            || string.Equals(config.Provider, "ollama", StringComparison.OrdinalIgnoreCase))
        {
            using var doc = await GetProviderJsonAsync($"{config.Endpoint.TrimEnd('/')}/api/tags", "");
            return ReadOllamaModelNames(doc.RootElement);
        }
        if (string.Equals(config.Protocol, "openai_compatible", StringComparison.OrdinalIgnoreCase)
            || string.Equals(config.Provider, "lmstudio", StringComparison.OrdinalIgnoreCase))
        {
            using var doc = await GetProviderJsonAsync($"{config.Endpoint.TrimEnd('/')}/models", config.ApiKey);
            return ReadOpenAiCompatibleModelNames(doc.RootElement);
        }
        return string.IsNullOrWhiteSpace(config.Model)
            ? Array.Empty<string>()
            : new[] { config.Model };
    }

    internal async Task<JsonDocument> GetProviderJsonAsync(string url, string apiKey)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        }
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        using var response = await _http.SendAsync(request, cts.Token);
        var text = await response.Content.ReadAsStringAsync();
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(text);
    }

    internal static IReadOnlyList<string> ReadOllamaModelNames(JsonElement root)
    {
        if (!root.TryGetProperty("models", out var models) || models.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return models.EnumerateArray()
            .Select(item => ReadJsonString(item, "name"))
            .Where(model => !string.IsNullOrWhiteSpace(model))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(model => model, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    internal static IReadOnlyList<string> ReadOpenAiCompatibleModelNames(JsonElement root)
    {
        if (!root.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return data.EnumerateArray()
            .Select(item => ReadJsonString(item, "id"))
            .Where(model => !string.IsNullOrWhiteSpace(model))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(model => model, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static bool IsLibraryScanProvider(string provider)
    {
        return string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase)
            || string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase);
    }

    private void PopulateCloudModelOptions(IEnumerable<string> models)
    {
        var box = WorkbenchShell.ManagementPanels.CloudModelBox;
        var keep = box.Text;
        box.ItemsSource = models
            .Where(model => !string.IsNullOrWhiteSpace(model))
            .Select(model => model.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
        box.Text = keep;
    }

    private async Task TestProviderViaBackendAsync(SelectedProviderConfig config, string modelId)
    {
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
            {
                action = "test_provider",
                provider = new
                {
                    provider = config.Provider,
                    model_id = modelId,
                    endpoint = config.Endpoint,
                    model = config.Model,
                }
            });
            var action = doc.RootElement.GetProperty("provider_action");
            var models = ReadJsonStringArray(action, "models");
            PopulateCloudModelOptions(models);
            var sample = models.Length == 0 ? "--" : string.Join(", ", models.Take(3));
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 连接正常 · 发现 {models.Length} 个模型 · {sample} · 已复用保存的 Key";
            if (models.Length > 0 && string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.CloudModelBox.Text))
            {
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = models[0];
            }
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{config.DisplayName} 测试失败（后端复用 Key）：{ex.Message}";
        }
    }

}

