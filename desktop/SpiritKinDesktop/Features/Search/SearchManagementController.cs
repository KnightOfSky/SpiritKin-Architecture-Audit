using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed class SearchManagementController
{
    private readonly ManagementPanelsView _panels;
    private readonly Func<string, Task<JsonDocument>> _getJsonAsync;
    private readonly Func<string, object, Task<JsonDocument>> _postJsonAsync;
    private readonly Func<string> _apiBase;
    private readonly Func<Task> _loadAgentManagementAsync;
    private readonly Func<Task> _loadModuleManagementAsync;

    public ObservableCollection<EventViewModel> Gaps { get; } = new();

    public ObservableCollection<SearchCapabilityViewModel> ModelCapabilities { get; } = new();

    public ObservableCollection<KnowledgeJobViewModel> KnowledgeJobs { get; } = new();

    public SearchManagementController(
        ManagementPanelsView panels,
        Func<string, Task<JsonDocument>> getJsonAsync,
        Func<string, object, Task<JsonDocument>> postJsonAsync,
        Func<string> apiBase,
        Func<Task> loadAgentManagementAsync,
        Func<Task> loadModuleManagementAsync)
    {
        _panels = panels;
        _getJsonAsync = getJsonAsync;
        _postJsonAsync = postJsonAsync;
        _apiBase = apiBase;
        _loadAgentManagementAsync = loadAgentManagementAsync;
        _loadModuleManagementAsync = loadModuleManagementAsync;
    }

    public async Task LoadAsync()
    {
        try
        {
            using var doc = await _getJsonAsync($"{_apiBase()}/desktop/search-management");
            Render(doc.RootElement.GetProperty("search_management"));
        }
        catch (Exception ex)
        {
            Gaps.Clear();
            ModelCapabilities.Clear();
            KnowledgeJobs.Clear();
            _panels.SearchManagementSummaryText.Text = $"搜索检索加载失败：{ex.Message}";
            _panels.SearchRuntimeStatusText.Text = "请确认 command gateway 正在运行并支持 /desktop/search-management。";
        }
    }

    public async Task SaveRuntimeConfigAsync()
    {
        try
        {
            _panels.SearchManagementSummaryText.Text = "正在保存搜索检索运行时配置...";
            var payload = new Dictionary<string, object?>
            {
                ["action"] = "save_runtime_config",
                ["web_search_provider"] = ComboText(_panels.SearchWebProviderBox),
                ["knowledge_backend"] = ComboText(_panels.SearchKnowledgeBackendBox),
                ["embedding_provider"] = ComboText(_panels.SearchEmbeddingProviderBox),
                ["embedding_model"] = _panels.SearchEmbeddingModelBox.Text.Trim(),
                ["embedding_base_url"] = _panels.SearchEmbeddingBaseUrlBox.Text.Trim(),
                ["reranker"] = ComboText(_panels.SearchRerankerProviderBox),
                ["reranker_model"] = _panels.SearchRerankerModelBox.Text.Trim(),
                ["reranker_base_url"] = _panels.SearchRerankerBaseUrlBox.Text.Trim(),
            };
            if (!string.IsNullOrWhiteSpace(_panels.SearchEmbeddingApiKeyBox.Password))
            {
                payload["embedding_api_key"] = _panels.SearchEmbeddingApiKeyBox.Password.Trim();
            }
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/search-management", payload);
            Render(doc.RootElement.GetProperty("search_management"));
            _panels.SearchEmbeddingApiKeyBox.Clear();
            _panels.SearchManagementSummaryText.Text = $"运行时配置已保存。{Environment.NewLine}{_panels.SearchManagementSummaryText.Text}";
            await _loadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            _panels.SearchManagementSummaryText.Text = $"搜索检索配置保存失败：{ex.Message}";
        }
    }

    public async Task IndexUnindexedKnowledgeAsync()
    {
        try
        {
            _panels.SearchManagementSummaryText.Text = "正在索引未索引知识库...";
            using var doc = await _postJsonAsync($"{_apiBase()}/desktop/search-management", new { action = "index_unindexed_knowledge" });
            EnsureOkResponse(doc.RootElement, "索引未索引知识库失败");
            Render(doc.RootElement.GetProperty("search_management"));
            var indexed = doc.RootElement.TryGetProperty("indexing", out var indexing)
                && indexing.TryGetProperty("indexed", out var indexedElement)
                && indexedElement.ValueKind == JsonValueKind.Array
                    ? indexedElement.GetArrayLength()
                    : 0;
            _panels.SearchManagementSummaryText.Text = $"已索引 {indexed} 个知识库。{Environment.NewLine}{_panels.SearchManagementSummaryText.Text}";
            await _loadAgentManagementAsync();
            await _loadModuleManagementAsync();
        }
        catch (Exception ex)
        {
            _panels.SearchManagementSummaryText.Text = $"索引未索引知识库失败：{ex.Message}";
        }
    }

    private void Render(JsonElement state)
    {
        var web = state.TryGetProperty("web_search", out var webSearch) && webSearch.ValueKind == JsonValueKind.Object ? webSearch : default;
        var retrieval = state.TryGetProperty("knowledge_retrieval", out var knowledgeRetrieval) && knowledgeRetrieval.ValueKind == JsonValueKind.Object ? knowledgeRetrieval : default;
        if (web.ValueKind == JsonValueKind.Object)
        {
            SetComboText(_panels.SearchWebProviderBox, JsonHelpers.ReadString(web, "preferred", "brave,duckduckgo"));
        }
        if (retrieval.ValueKind == JsonValueKind.Object)
        {
            SetComboText(_panels.SearchKnowledgeBackendBox, JsonHelpers.ReadString(retrieval, "backend", "keyword"));
            SetComboText(_panels.SearchEmbeddingProviderBox, JsonHelpers.ReadString(retrieval, "embedding_provider", "hashing"));
            _panels.SearchEmbeddingModelBox.Text = JsonHelpers.ReadString(retrieval, "embedding_model");
            _panels.SearchEmbeddingBaseUrlBox.Text = JsonHelpers.ReadString(retrieval, "embedding_base_url");
            SetComboText(_panels.SearchRerankerProviderBox, JsonHelpers.ReadString(retrieval, "reranker", "token_overlap"));
            _panels.SearchRerankerModelBox.Text = JsonHelpers.ReadString(retrieval, "reranker_model");
            _panels.SearchRerankerBaseUrlBox.Text = JsonHelpers.ReadString(retrieval, "reranker_base_url");
        }

        Gaps.Clear();
        if (state.TryGetProperty("missing_capabilities", out var gaps) && gaps.ValueKind == JsonValueKind.Array)
        {
            foreach (var gap in gaps.EnumerateArray())
            {
                Gaps.Add(new EventViewModel(
                    $"{UiDisplayText.Priority(JsonHelpers.ReadString(gap, "priority", "medium"))} · {JsonHelpers.ReadString(gap, "title", "搜索检索缺口")}",
                    JsonHelpers.ReadString(gap, "detail")));
            }
        }
        if (state.TryGetProperty("recommendations", out var recommendations) && recommendations.ValueKind == JsonValueKind.Array)
        {
            foreach (var recommendation in recommendations.EnumerateArray())
            {
                var text = recommendation.ValueKind == JsonValueKind.String ? recommendation.GetString() ?? "" : recommendation.GetRawText();
                if (!string.IsNullOrWhiteSpace(text))
                {
                    Gaps.Add(new EventViewModel("建议", text));
                }
            }
        }

        KnowledgeJobs.Clear();
        var knowledgeJobs = state.TryGetProperty("knowledge_jobs", out var jobsState) && jobsState.ValueKind == JsonValueKind.Object
            ? jobsState
            : default;
        if (knowledgeJobs.ValueKind == JsonValueKind.Object
            && knowledgeJobs.TryGetProperty("jobs", out var jobs)
            && jobs.ValueKind == JsonValueKind.Array)
        {
            foreach (var job in jobs.EnumerateArray())
            {
                KnowledgeJobs.Add(KnowledgeJobViewModel.FromJson(job));
            }
        }
        if (KnowledgeJobs.Count == 0)
        {
            KnowledgeJobs.Add(KnowledgeJobViewModel.Empty());
        }

        ModelCapabilities.Clear();
        if (state.TryGetProperty("model_capability_matrix", out var matrix) && matrix.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in matrix.EnumerateArray())
            {
                ModelCapabilities.Add(new SearchCapabilityViewModel(
                    JsonHelpers.ReadString(item, "model"),
                    JsonHelpers.ReadString(item, "provider"),
                    JsonHelpers.ReadStringArray(item, "strengths"),
                    JsonHelpers.ReadString(item, "best_for"),
                    JsonHelpers.ReadBool(item, "local", false)));
            }
        }

        var catalogRoles = state.TryGetProperty("catalog_roles", out var roles) && roles.ValueKind == JsonValueKind.Object
            ? string.Join(" · ", roles.EnumerateObject().Select(prop => $"{UiDisplayText.Role(prop.Name)} {prop.Value.GetRawText()}"))
            : "--";
        _panels.SearchRuntimeStatusText.Text = BuildRuntimeStatus(state, web, retrieval, knowledgeJobs, catalogRoles);
        var failedJobs = knowledgeJobs.ValueKind == JsonValueKind.Object ? JsonHelpers.ReadInt(knowledgeJobs, "failed_count") : 0;
        _panels.SearchManagementSummaryText.Text = $"状态 {UiDisplayText.Status(JsonHelpers.ReadString(state, "status", "--"))} · 缺口 {Gaps.Count(item => !string.Equals(item.Type, "建议", StringComparison.OrdinalIgnoreCase))} · 失败任务 {failedJobs} · 模型快照 {ModelCapabilities.Count}";
    }

    public static string BuildRuntimeStatus(JsonElement state, JsonElement web, JsonElement retrieval, JsonElement knowledgeJobs, string catalogRoles)
    {
        var lines = new List<string>
        {
            $"配置版本：{UiDisplayText.ShortTechnical(JsonHelpers.ReadString(state, "schema_version", "--"), 42)}",
        };
        if (web.ValueKind == JsonValueKind.Object)
        {
            lines.Add($"联网搜索：{UiDisplayText.Provider(JsonHelpers.ReadString(web, "provider", "--"))} · 首选 {UiDisplayText.Provider(JsonHelpers.ReadString(web, "preferred", "--"))} · Brave Key {(JsonHelpers.ReadBool(web, "brave_configured", false) ? "已配置" : "未配置")}");
        }
        if (retrieval.ValueKind == JsonValueKind.Object)
        {
            lines.Add($"知识检索：{UiDisplayText.KnowledgeBackend(JsonHelpers.ReadString(retrieval, "backend", "--"))} · 知识库 {JsonHelpers.ReadInt(retrieval, "knowledge_base_count")}");
            lines.Add($"向量召回：{UiDisplayText.Provider(JsonHelpers.ReadString(retrieval, "embedding_provider", "--"))} · {UiDisplayText.ShortTechnical(JsonHelpers.ReadString(retrieval, "embedding_model", "--"))} · {(JsonHelpers.ReadBool(retrieval, "embedding_configured", false) ? "已配置" : "占位或未完整配置")}");
            lines.Add($"结果重排：{UiDisplayText.Provider(JsonHelpers.ReadString(retrieval, "reranker", "--"))} · {UiDisplayText.ShortTechnical(JsonHelpers.ReadString(retrieval, "reranker_model", "--"))} · {(JsonHelpers.ReadBool(retrieval, "reranker_configured", false) ? "已配置" : "占位或未完整配置")}");
        }
        if (knowledgeJobs.ValueKind == JsonValueKind.Object)
        {
            var lastError = JsonHelpers.ReadString(knowledgeJobs, "last_error");
            var jobLine = $"任务历史：总计 {JsonHelpers.ReadInt(knowledgeJobs, "count")} · 失败 {JsonHelpers.ReadInt(knowledgeJobs, "failed_count")} · 最近状态 {UiDisplayText.Status(JsonHelpers.ReadString(knowledgeJobs, "last_status", "--"))}";
            lines.Add(string.IsNullOrWhiteSpace(lastError) ? jobLine : $"{jobLine} · 最近错误 {UiDisplayText.ShortTechnical(lastError, 96)}");
        }
        lines.Add($"模型目录角色：{catalogRoles}");
        return string.Join(Environment.NewLine, lines);
    }

    private static string ComboText(ComboBox combo)
    {
        return (combo.SelectedItem as ComboBoxItem)?.Content?.ToString()?.Trim()
            ?? combo.SelectedValue?.ToString()?.Trim()
            ?? combo.Text.Trim();
    }

    private static void SetComboText(ComboBox combo, string value)
    {
        var text = (value ?? "").Trim();
        foreach (var item in combo.Items)
        {
            if (item is ComboBoxItem comboBoxItem
                && string.Equals(comboBoxItem.Content?.ToString(), text, StringComparison.OrdinalIgnoreCase))
            {
                combo.SelectedItem = comboBoxItem;
                combo.Text = text;
                return;
            }
        }
        combo.SelectedItem = null;
        combo.Text = text;
    }

    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

    internal static class JsonHelpers
    {
        public static string ReadString(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return "";
            }
            return value.ValueKind switch
            {
                JsonValueKind.String => value.GetString() ?? "",
                JsonValueKind.Number => value.GetRawText(),
                JsonValueKind.True => "true",
                JsonValueKind.False => "false",
                JsonValueKind.Null => "",
                _ => value.GetRawText(),
            };
        }

        public static string ReadString(JsonElement element, string key, string fallback)
        {
            var value = ReadString(element, key);
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        public static int ReadInt(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return 0;
            }
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
            {
                return number;
            }
            return int.TryParse(ReadString(element, key), out var parsed) ? parsed : 0;
        }

        public static bool ReadBool(JsonElement element, string key, bool fallback = false)
        {
            if (!element.TryGetProperty(key, out var value))
            {
                return fallback;
            }
            return value.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
                _ => fallback,
            };
        }

        public static string[] ReadStringArray(JsonElement element, string key)
        {
            if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
            {
                return Array.Empty<string>();
            }
            return value.EnumerateArray()
                .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .ToArray();
        }
    }
}
