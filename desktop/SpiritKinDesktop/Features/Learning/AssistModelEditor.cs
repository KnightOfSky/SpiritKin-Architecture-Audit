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
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal void RenderSelectedAssistModelEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue as string;
        var model = _assistModels.FirstOrDefault(item => item.ModelId == selectedId) ?? _assistModels.FirstOrDefault();
        if (model is null)
        {
            WorkbenchShell.ManagementPanels.AssistModelIdBox.Clear();
            WorkbenchShell.ManagementPanels.AssistModelNameBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox, "openai_compatible");
            WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Clear();
            WorkbenchShell.ManagementPanels.CloudModelBox.Text = string.Empty;
            WorkbenchShell.ManagementPanels.CloudApiKeyBox.Clear();
            WorkbenchShell.ManagementPanels.CloudProviderEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.AssistModelRoleBox.Text = "reviewer";
            WorkbenchShell.ManagementPanels.AssistModelPriorityBox.Text = "50";
            WorkbenchShell.ManagementPanels.AssistModelNotesBox.Clear();
            WorkbenchShell.ManagementPanels.AssistModelRequestParamsBox.Clear();
            SetApiKeyStateIndicator(false);
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "暂无协助模型。";
            return;
        }
        WorkbenchShell.ManagementPanels.AssistModelIdBox.Text = model.ModelId;
        WorkbenchShell.ManagementPanels.AssistModelNameBox.Text = model.DisplayName;
        SetComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox, model.Provider);
        WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text = model.Endpoint;
        WorkbenchShell.ManagementPanels.CloudModelBox.Text = model.Model;
        WorkbenchShell.ManagementPanels.CloudApiKeyBox.Clear();
        WorkbenchShell.ManagementPanels.CloudProviderEnabledBox.IsChecked = model.Enabled;
        WorkbenchShell.ManagementPanels.AssistModelRoleBox.Text = model.Role;
        WorkbenchShell.ManagementPanels.AssistModelPriorityBox.Text = model.Priority.ToString();
        WorkbenchShell.ManagementPanels.AssistModelNotesBox.Text = model.Notes;
        WorkbenchShell.ManagementPanels.AssistModelRequestParamsBox.Text = model.RequestParamsJson;
        SetApiKeyStateIndicator(model.ApiKeySet);
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{model.DisplayName} · {(model.Configured ? "已配置" : "未完整配置")} · Key {(model.ApiKeySet ? "已保存" : "未保存")}";
    }

    private void SetApiKeyStateIndicator(bool apiKeySet)
    {
        var label = WorkbenchShell.ManagementPanels.AssistModelApiKeyStateText;
        if (apiKeySet)
        {
            label.Text = "●●●●●●●● 已保存";
            label.Foreground = new SolidColorBrush(Color.FromRgb(22, 163, 74));
        }
        else
        {
            label.Text = "未保存 Key";
            label.Foreground = new SolidColorBrush(Color.FromRgb(107, 114, 128));
        }
    }

    internal void RenderReviewCommitteePolicy(JsonElement policy, JsonElement learning)
    {
        WorkbenchShell.ManagementPanels.ReviewCommitteePolicyIdBox.Text = ReadJsonString(policy, "policy_id", "default_committee");
        WorkbenchShell.ManagementPanels.ReviewCommitteeLabelBox.Text = ReadJsonString(policy, "label", "默认云端评审团");
        WorkbenchShell.ManagementPanels.ReviewCommitteeEnabledBox.IsChecked = ReadJsonBool(policy, "enabled", true);
        WorkbenchShell.ManagementPanels.ReviewCommitteeHumanFinalBox.IsChecked = ReadJsonBool(policy, "require_human_final", true);
        WorkbenchShell.ManagementPanels.ReviewCommitteeModelsBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(policy, "model_ids"));
        WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredModelsBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(policy, "required_model_ids"));
        WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredRolesBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(policy, "required_roles"));
        WorkbenchShell.ManagementPanels.ReviewCommitteeActionsBox.Text = string.Join(Environment.NewLine, ReadJsonStringArray(policy, "apply_to_actions"));
        WorkbenchShell.ManagementPanels.ReviewCommitteeMinSuccessBox.Text = ReadJsonInt(policy, "min_success_count").ToString();
        WorkbenchShell.ManagementPanels.ReviewCommitteePassThresholdBox.Text = ReadJsonString(policy, "pass_threshold", "0.5");
        WorkbenchShell.ManagementPanels.ReviewCommitteeNotesBox.Text = ReadJsonString(policy, "notes");
        if (learning.TryGetProperty("review_committee_summary", out var summary) && summary.ValueKind == JsonValueKind.Object)
        {
            var missingModels = ReadJsonStringArray(summary, "missing_required_model_ids");
            var missingRoles = ReadJsonStringArray(summary, "missing_required_roles");
            WorkbenchShell.ManagementPanels.ReviewCommitteeSummaryText.Text =
                $"状态 {UiDisplayText.Status(ReadJsonString(summary, "status", "--"))} · 模型 {ReadJsonInt(summary, "configured_count")}/{ReadJsonInt(summary, "selected_count")} · 最少成功 {ReadJsonInt(summary, "min_success_count")}{Environment.NewLine}" +
                $"缺模型: {(missingModels.Length == 0 ? "无" : string.Join(", ", missingModels))}{Environment.NewLine}" +
                $"缺角色: {(missingRoles.Length == 0 ? "无" : string.Join(", ", missingRoles))}";
        }
    }

    internal void NewAssistModel()
    {
        var config = BuildSelectedProviderConfig();
        var model = new AssistModelViewModel(
            UniqueId("assist", _assistModels.Select(item => item.ModelId)),
            $"{config.DisplayName} 配置",
            config.Provider,
            string.IsNullOrWhiteSpace(config.Endpoint) ? "https://api.example.com/v1" : config.Endpoint,
            config.Model,
            true,
            false,
            "reviewer",
            50,
            "",
            false);
        _assistModels.Add(model);
        WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = model.ModelId;
        RenderSelectedAssistModelEditor();
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已新增协助模型，填写后保存。";
    }

    internal void NewLocalAssistModel()
    {
        var modelId = UniqueId("local", _assistModels.Select(item => item.ModelId));
        var model = new AssistModelViewModel(
            modelId,
            "本地 Ollama",
            "ollama",
            "http://127.0.0.1:11434",
            "qwen2.5-coder:7b",
            true,
            false,
            "primary_worker",
            100,
            "本地模型配置。确认 Ollama 已运行并已 pull 对应模型。",
            true);
        _assistModels.Add(model);
        WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = model.ModelId;
        RenderSelectedAssistModelEditor();
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已新增本地模型。保存前可修改模型名，例如 qwen2.5-coder:7b。";
    }

    internal void NewLmStudioAssistModel()
    {
        var modelId = UniqueId("lmstudio", _assistModels.Select(item => item.ModelId));
        var model = new AssistModelViewModel(
            modelId,
            "LM Studio",
            "lmstudio",
            "http://127.0.0.1:1234/v1",
            "local-model",
            true,
            false,
            "primary_worker",
            95,
            "LM Studio 本地 OpenAI-compatible 服务。先在 LM Studio 选择模型并启动本地服务器。",
            true);
        _assistModels.Add(model);
        WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = model.ModelId;
        RenderSelectedAssistModelEditor();
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已新增 LM Studio 模型。启动 LM Studio 服务后，把模型名改成实际加载的模型。";
    }

    internal async Task SaveAssistModelAsync()
    {
        try
        {
            var modelId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AssistModelIdBox.Text)
                ? UniqueId("assist", _assistModels.Select(item => item.ModelId))
                : WorkbenchShell.ManagementPanels.AssistModelIdBox.Text.Trim();
            var provider = ProviderIdFromCombo(WorkbenchShell.ManagementPanels.AssistModelProviderBox);
            var priority = int.TryParse(WorkbenchShell.ManagementPanels.AssistModelPriorityBox.Text.Trim(), out var parsedPriority) ? parsedPriority : 50;
            var endpoint = WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text.Trim();
            var modelName = WorkbenchShell.ManagementPanels.CloudModelBox.Text.Trim();
            var definition = _providerDefinitions.FirstOrDefault(item => string.Equals(item.Provider, provider, StringComparison.OrdinalIgnoreCase))
                ?? SelectedProviderDefinition();
            if (string.IsNullOrWhiteSpace(endpoint) && !string.IsNullOrWhiteSpace(definition.DefaultEndpoint))
            {
                endpoint = definition.DefaultEndpoint;
                WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text = endpoint;
            }
            if (string.IsNullOrWhiteSpace(modelName) && !string.IsNullOrWhiteSpace(definition.DefaultModel))
            {
                modelName = definition.DefaultModel;
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = modelName;
            }
            var payload = new
            {
                action = "save_assist_model",
                model = new
                {
                    model_id = modelId,
                    display_name = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AssistModelNameBox.Text) ? modelId : WorkbenchShell.ManagementPanels.AssistModelNameBox.Text.Trim(),
                    provider,
                    endpoint,
                    model = modelName,
                    api_key = WorkbenchShell.ManagementPanels.CloudApiKeyBox.Password.Trim(),
                    keep_existing_key = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.CloudApiKeyBox.Password),
                    enabled = WorkbenchShell.ManagementPanels.CloudProviderEnabledBox.IsChecked == true,
                    role = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AssistModelRoleBox.Text) ? "reviewer" : WorkbenchShell.ManagementPanels.AssistModelRoleBox.Text.Trim(),
                    priority,
                    notes = WorkbenchShell.ManagementPanels.AssistModelNotesBox.Text.Trim(),
                    request_params = WorkbenchShell.ManagementPanels.AssistModelRequestParamsBox.Text.Trim(),
                }
            };
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", payload);
            var model = doc.RootElement.GetProperty("assist_model");
            var local = string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase)
                || string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase);
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = local
                ? $"已保存本地模型：{ReadJsonString(model, "display_name")} · {ReadJsonString(model, "model")}"
                : $"已保存：{ReadJsonString(model, "display_name")} · {(ReadJsonBool(model, "api_key_set", false) ? "API Key 已保存" : "未保存 API Key")}";
            WorkbenchShell.ManagementPanels.CloudApiKeyBox.Clear();
            await LoadLearningAsync();
            WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = SelectExistingId(modelId, _assistModels.Select(item => item.ModelId));
            RenderSelectedAssistModelEditor();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"保存模型失败：{ex.Message}";
        }
    }

    internal async Task SaveReviewCommitteePolicyAsync()
    {
        try
        {
            var minSuccess = int.TryParse(WorkbenchShell.ManagementPanels.ReviewCommitteeMinSuccessBox.Text.Trim(), out var parsedMin) ? parsedMin : 1;
            var passThreshold = double.TryParse(WorkbenchShell.ManagementPanels.ReviewCommitteePassThresholdBox.Text.Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out var parsedThreshold)
                ? parsedThreshold
                : 0.5;
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
            {
                action = "save_review_committee_policy",
                policy = new
                {
                    policy_id = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ReviewCommitteePolicyIdBox.Text) ? "default_committee" : WorkbenchShell.ManagementPanels.ReviewCommitteePolicyIdBox.Text.Trim(),
                    label = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ReviewCommitteeLabelBox.Text) ? "默认云端评审团" : WorkbenchShell.ManagementPanels.ReviewCommitteeLabelBox.Text.Trim(),
                    enabled = WorkbenchShell.ManagementPanels.ReviewCommitteeEnabledBox.IsChecked == true,
                    model_ids = SplitLines(WorkbenchShell.ManagementPanels.ReviewCommitteeModelsBox.Text),
                    required_model_ids = SplitLines(WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredModelsBox.Text),
                    required_roles = SplitLines(WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredRolesBox.Text),
                    min_success_count = minSuccess,
                    pass_threshold = passThreshold,
                    require_human_final = WorkbenchShell.ManagementPanels.ReviewCommitteeHumanFinalBox.IsChecked == true,
                    apply_to_actions = SplitLines(WorkbenchShell.ManagementPanels.ReviewCommitteeActionsBox.Text),
                    notes = WorkbenchShell.ManagementPanels.ReviewCommitteeNotesBox.Text.Trim(),
                }
            });
            var learning = doc.RootElement.GetProperty("learning");
            RenderReviewCommitteePolicy(learning.GetProperty("review_committee_policy"), learning);
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = BuildLearningStatusSummary(learning);
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已保存评审团策略：{ReadJsonString(doc.RootElement.GetProperty("review_committee_policy"), "label")}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.ReviewCommitteeSummaryText.Text = $"保存评审团策略失败：{ex.Message}";
        }
    }

    internal void AppendReviewCommitteeModelFromPicker()
    {
        if (AppendUniqueLine(WorkbenchShell.ManagementPanels.ReviewCommitteeModelsBox, WorkbenchShell.ManagementPanels.ReviewCommitteeModelPickerBox.SelectedValue as string))
        {
            WorkbenchShell.ManagementPanels.ReviewCommitteeModelPickerBox.SelectedIndex = -1;
        }
    }

    internal void AppendReviewCommitteeRoleFromPicker()
    {
        if (AppendUniqueLine(WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredRolesBox, (WorkbenchShell.ManagementPanels.ReviewCommitteeRolePickerBox.SelectedItem as ComboBoxItem)?.Content as string))
        {
            WorkbenchShell.ManagementPanels.ReviewCommitteeRolePickerBox.SelectedIndex = -1;
        }
    }

    internal void AppendReviewCommitteeActionFromPicker()
    {
        if (AppendUniqueLine(WorkbenchShell.ManagementPanels.ReviewCommitteeActionsBox, (WorkbenchShell.ManagementPanels.ReviewCommitteeActionPickerBox.SelectedItem as ComboBoxItem)?.Content as string))
        {
            WorkbenchShell.ManagementPanels.ReviewCommitteeActionPickerBox.SelectedIndex = -1;
        }
    }

    private static bool AppendUniqueLine(TextBox box, string? value)
    {
        var trimmed = value?.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return false;
        }
        var lines = box.Text
            .Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .ToList();
        if (!lines.Any(line => string.Equals(line, trimmed, StringComparison.OrdinalIgnoreCase)))
        {
            lines.Add(trimmed);
            box.Text = string.Join(Environment.NewLine, lines);
        }
        return true;
    }

    internal async Task DeleteAssistModelAsync()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedIndex;
        var selectedModel = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedItem as AssistModelViewModel;
        var modelId = selectedModel?.ModelId ?? WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue as string ?? WorkbenchShell.ManagementPanels.AssistModelIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(modelId))
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "请先选择要删除的协助模型。";
            return;
        }
        if (!ConfirmDestructiveAction("删除协助模型", $"确定要删除协助模型“{modelId}”吗？"))
        {
            return;
        }
        try
        {
            WorkbenchShell.ManagementPanels.AssistModelsList.SelectedItem = null;
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new { action = "delete_assist_model", model_id = modelId });
            var deleted = ReadJsonBool(doc.RootElement, "ok", false);
            if (deleted)
            {
                await LoadLearningAsync();
                if (string.Equals(SelectedComposerModelId(), modelId, StringComparison.OrdinalIgnoreCase))
                {
                    SelectDefaultComposerModel(persist: true);
                }
                WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = _assistModels.FirstOrDefault()?.ModelId;
                RenderSelectedAssistModelEditor();
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已删除：{modelId}";
                return;
            }
            if (selectedIndex >= 0 && selectedIndex < _assistModels.Count && string.Equals(_assistModels[selectedIndex].ModelId, modelId, StringComparison.OrdinalIgnoreCase))
            {
                _assistModels.RemoveAt(selectedIndex);
            }
            else
            {
                var local = _assistModels.FirstOrDefault(item => string.Equals(item.ModelId, modelId, StringComparison.OrdinalIgnoreCase));
                if (local is not null)
                {
                    _assistModels.Remove(local);
                }
            }
            WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = _assistModels.Count == 0 ? null : _assistModels[Math.Min(Math.Max(selectedIndex, 0), _assistModels.Count - 1)].ModelId;
            RenderSelectedAssistModelEditor();
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"已移除未保存模型：{modelId}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"删除模型失败：{ex.Message}";
            await LoadLearningAsync();
        }
    }

    private static string ReadRequestParamsJson(JsonElement model)
    {
        if (!model.TryGetProperty("request_params", out var raw) || raw.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        using var enumerator = raw.EnumerateObject();
        return enumerator.MoveNext() ? raw.GetRawText() : "";
    }

}

