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
    internal async Task LoadLearningAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/learning");
            var learning = doc.RootElement.GetProperty("learning");
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = await BuildModelLearningSummaryAsync(learning);
            var previousAssistModel = WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue as string;
            _assistModels.Clear();
            if (learning.TryGetProperty("assist_models", out var assistModels) && assistModels.ValueKind == JsonValueKind.Array)
            {
                foreach (var model in assistModels.EnumerateArray())
                {
                    _assistModels.Add(new AssistModelViewModel(
                        ReadJsonString(model, "model_id"),
                        ReadJsonString(model, "display_name"),
                        ReadJsonString(model, "provider"),
                        ReadJsonString(model, "endpoint"),
                        ReadJsonString(model, "model"),
                        ReadJsonBool(model, "enabled", true),
                        ReadJsonBool(model, "api_key_set", false),
                        ReadJsonString(model, "role"),
                        ReadJsonInt(model, "priority"),
                        ReadJsonString(model, "notes"),
                        ReadJsonBool(model, "configured", false),
                        ReadRequestParamsJson(model)));
                }
            }
            WorkbenchShell.ManagementPanels.AssistModelsList.SelectedValue = SelectExistingId(previousAssistModel, _assistModels.Select(item => item.ModelId))
                ?? _assistModels.FirstOrDefault()?.ModelId;
            WorkbenchShell.ManagementPanels.LearningAssistModelBox.SelectedValue = SelectExistingId(previousAssistModel, _assistModels.Select(item => item.ModelId))
                ?? _assistModels.Where(item => item.Enabled && item.Configured).OrderByDescending(item => item.Priority).FirstOrDefault()?.ModelId
                ?? _assistModels.FirstOrDefault()?.ModelId;
            NormalizeAssistModelComboSelection(WorkbenchShell.ManagementPanels.LearningAssistModelBox);
            NormalizeAssistModelComboSelection(WorkbenchShell.ManagementPanels.AgentModelSelectBox);
            RenderSelectedAssistModelEditor();
            if (learning.TryGetProperty("provider_definitions", out var definitions) && definitions.ValueKind == JsonValueKind.Array)
            {
                var previousProvider = WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue as string ?? ComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox);
                _providerDefinitions.Clear();
                foreach (var provider in definitions.EnumerateArray())
                {
                    _providerDefinitions.Add(new ModelProviderDefinitionViewModel(
                        ReadJsonString(provider, "provider"),
                        ReadJsonString(provider, "display_name"),
                        ReadJsonString(provider, "default_endpoint"),
                        ReadJsonString(provider, "default_model"),
                        ReadJsonString(provider, "env_key"),
                        ReadJsonBool(provider, "requires_api_key", true),
                        ReadJsonBool(provider, "local_service", false),
                        ReadJsonBool(provider, "supports_model_sync", true),
                        ReadJsonString(provider, "protocol")));
                }
                EnsureProviderComboItems();
                WorkbenchShell.ManagementPanels.ProviderManageBox.SelectedValue = SelectExistingId(previousProvider, _providerDefinitions.Select(item => item.Provider))
                    ?? SelectExistingId(ComboText(WorkbenchShell.ManagementPanels.AssistModelProviderBox), _providerDefinitions.Select(item => item.Provider))
                    ?? _providerDefinitions.FirstOrDefault()?.Provider;
                RefreshProviderComboDisplay();
            }
            if (_assistModels.Count == 0 && learning.TryGetProperty("model_provider_settings", out var settings))
            {
                WorkbenchShell.ManagementPanels.CloudProviderEnabledBox.IsChecked = settings.TryGetProperty("enabled", out var enabled) && enabled.ValueKind == JsonValueKind.True;
                WorkbenchShell.ManagementPanels.AssistModelNameBox.Text = ReadJsonString(settings, "display_name");
                WorkbenchShell.ManagementPanels.CloudBaseUrlBox.Text = ReadJsonString(settings, "endpoint");
                WorkbenchShell.ManagementPanels.CloudModelBox.Text = ReadJsonString(settings, "model");
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = settings.TryGetProperty("api_key_set", out var keySet) && keySet.ValueKind == JsonValueKind.True ? "API Key 已保存" : "未保存 API Key";
            }
            if (learning.TryGetProperty("review_committee_policy", out var committeePolicy) && committeePolicy.ValueKind == JsonValueKind.Object)
            {
                RenderReviewCommitteePolicy(committeePolicy, learning);
            }
            _modelProviders.Clear();
            foreach (var provider in learning.GetProperty("model_providers").EnumerateArray())
            {
                var configured = provider.TryGetProperty("configured", out var configuredEl) && configuredEl.GetBoolean();
                _modelProviders.Add(new EventViewModel(
                    $"{(configured ? "已配置" : "未配置")} · {ReadJsonString(provider, "display_name")}",
                    $"{UiDisplayText.Provider(ReadJsonString(provider, "provider"))} · {UiDisplayText.ShortTechnical(ReadJsonString(provider, "model"))} · {UiDisplayText.ShortTechnical(ReadJsonString(provider, "endpoint"), 42)}"));
            }
            SyncProviderServiceButtonState();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"学习模块加载失败：{ex.Message}";
        }
    }

    internal static string BuildLearningStatusSummary(JsonElement learning)
    {
        var recordCount = learning.TryGetProperty("records", out var records) && records.ValueKind == JsonValueKind.Array
            ? records.GetArrayLength()
            : 0;
        var datasetPath = "";
        var datasetCount = 0;
        if (learning.TryGetProperty("dataset", out var dataset) && dataset.ValueKind == JsonValueKind.Object)
        {
            datasetPath = ReadJsonString(dataset, "path");
            datasetCount = ReadJsonInt(dataset, "count");
        }

        if (!learning.TryGetProperty("self_improvement_summary", out var summary) || summary.ValueKind != JsonValueKind.Object)
        {
            return $"学习记录: {recordCount}{Environment.NewLine}训练集: {datasetPath}{Environment.NewLine}样本数: {datasetCount}";
        }

        TryReadJsonObject(summary, "counts", out var counts);
        TryReadJsonObject(summary, "loop", out var loop);
        var status = ReadJsonString(summary, "status", "collecting");
        var autoApply = ReadSafeJsonBool(loop, "auto_code_apply_enabled", false) ? "开启" : "关闭";
        var reviewGate = ReadSafeJsonBool(loop, "human_review_required", true) ? "需人工审核" : "无需人工审核";
        var feedback = ReadSafeJsonBool(loop, "runtime_feedback_collected", false) ? "已收集" : "等待样本";
        var training = ReadSafeJsonBool(loop, "training_dataset_exported", false) ? "已导出" : "未导出";
        var nextSteps = ReadSummaryStepTitles(summary, "next_steps", 2);
        var committeeLine = "";
        if (learning.TryGetProperty("review_committee_summary", out var committee) && committee.ValueKind == JsonValueKind.Object)
        {
            committeeLine = $"评审团: {UiDisplayText.Status(ReadJsonString(committee, "status", "--"))} · 配置 {ReadJsonInt(committee, "configured_count")}/{ReadJsonInt(committee, "selected_count")} · 阈值 {ReadJsonString(committee, "min_success_count", "--")}/{ReadJsonString(committee, "pass_threshold", "--")}";
        }
        return string.Join(Environment.NewLine, new[]
        {
            $"自我进化: {status} · 自动改代码 {autoApply} · {reviewGate}",
            committeeLine,
            $"闭环: 反馈 {feedback} · 训练集 {training} · 改进动作 {ReadSafeJsonInt(counts, "improvement_actions")} · eval {ReadSafeJsonInt(counts, "eval_cases")}",
            $"样本: 学习 {ReadSafeJsonInt(counts, "learning_records", recordCount)} · dataset {ReadSafeJsonInt(counts, "dataset_examples", datasetCount)} · self-training {ReadSafeJsonInt(counts, "self_training_examples")}",
            $"路径: {(string.IsNullOrWhiteSpace(datasetPath) ? "--" : datasetPath)}",
            $"下一步: {(nextSteps.Length == 0 ? "--" : string.Join("；", nextSteps))}",
        }.Where(line => !string.IsNullOrWhiteSpace(line)));
    }

    internal async Task<string> BuildModelLearningSummaryAsync(JsonElement learning)
    {
        var summary = BuildLearningStatusSummary(learning);
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/model-catalog");
            return string.Join(Environment.NewLine, new[]
            {
                summary,
                BuildModelGovernanceSummary(doc.RootElement),
            }.Where(line => !string.IsNullOrWhiteSpace(line)));
        }
        catch (Exception ex)
        {
            return $"{summary}{Environment.NewLine}模型策略: 读取失败 · {ex.Message}";
        }
    }

    internal async Task RunSchedulerBenchmarkAsync()
    {
        try
        {
            var outputs = new Dictionary<string, object?>
            {
                ["json_validity_route_plan"] = new Dictionary<string, object?>
                {
                    ["route"] = "tool",
                    ["tool_calls"] = Array.Empty<object>(),
                    ["workflow_steps"] = Array.Empty<object>(),
                    ["confidence"] = 0.9,
                },
                ["tool_call_accuracy_browser"] = new Dictionary<string, object?>
                {
                    ["route"] = "executor",
                    ["tool_calls"] = new[]
                    {
                        new Dictionary<string, object?> { ["name"] = "browser.open_url", ["url"] = "https://example.com" },
                    },
                },
                ["workflow_step_completeness_publish"] = new Dictionary<string, object?>
                {
                    ["route"] = "workflow",
                    ["workflow_steps"] = new[] { "intake", "asset_check", "review_gate", "upload_product" },
                },
                ["context_drift_followup"] = new Dictionary<string, object?>
                {
                    ["route"] = "agent",
                    ["context_retained_ids"] = new[] { "order-42", "ecom-demo" },
                    ["irrelevant_context_ids"] = Array.Empty<object>(),
                },
            };
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/model-catalog", new Dictionary<string, object?>
            {
                ["action"] = "evaluate_scheduler_benchmark",
                ["outputs_by_case_id"] = outputs,
                ["actor"] = "wpf_desktop",
            });
            EnsureOkResponse(doc.RootElement, "Scheduler benchmark 运行失败");
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = BuildModelGovernanceSummary(doc.RootElement);
            await LoadLearningAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"Scheduler benchmark 失败：{ex.Message}";
        }
    }

    internal static string BuildModelGovernanceSummary(JsonElement root)
    {
        TryReadJsonObject(root, "local_model_policy", out var localPolicy);
        TryReadJsonObject(localPolicy, "hardware", out var hardware);
        TryReadJsonObject(localPolicy, "scheduler_benchmark", out var benchmark);
        TryReadJsonObject(root, "brain_replacement", out var brainReplacement);
        TryReadJsonObject(brainReplacement, "adapter_registry", out var adapterRegistry);
        TryReadJsonObject(brainReplacement, "replacement_gate", out var replacementGate);
        var roleCount = localPolicy.TryGetProperty("role_assignments", out var roles) && roles.ValueKind == JsonValueKind.Array ? roles.GetArrayLength() : 0;
        var adapterCount = ReadSafeJsonInt(adapterRegistry, "adapter_count");
        var autoReplace = ReadSafeJsonBool(replacementGate, "auto_replace_allowed", false) ? "自动替换开启" : "自动替换关闭";
        return string.Join(Environment.NewLine, new[]
        {
            $"模型策略: {ReadSafeJsonString(hardware, "hardware_class", "--")} · 本地角色 {roleCount} · Benchmark {ReadSafeJsonString(benchmark, "status", "not_run")}/{ReadSafeJsonInt(benchmark, "case_count")}",
            $"Brain Adapter: {adapterCount} · 替换Gate 阈值 {ReadSafeJsonString(replacementGate, "minimum_average_score", "--")} · {autoReplace}",
        });
    }

}

