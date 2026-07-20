using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal async Task BuildReviewPromptAsync()
    {
        using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
        {
            action = "review_prompt",
            skill_name = WorkbenchShell.ManagementPanels.LearningSkillBox.Text.Trim(),
            problem = WorkbenchShell.ManagementPanels.LearningProblemBox.Text.Trim(),
            context = WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text.Trim(),
        });
        WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text = ReadJsonString(doc.RootElement, "prompt");
    }

    internal async Task RequestModelReviewAsync()
    {
        var problem = WorkbenchShell.ManagementPanels.LearningProblemBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(problem))
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = "问题不能为空。";
            return;
        }
        WorkbenchShell.ManagementPanels.LearningSummaryText.Text = "正在请求模型评审...";
        try
        {
            var selectedId = WorkbenchShell.ManagementPanels.LearningAssistModelBox.SelectedValue as string;
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
            {
                action = "multi_model_review",
                skill_name = WorkbenchShell.ManagementPanels.LearningSkillBox.Text.Trim(),
                problem,
                context = WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text.Trim(),
                model_ids = string.IsNullOrWhiteSpace(selectedId) ? Array.Empty<string>() : new[] { selectedId },
            });
            var aggregate = doc.RootElement.GetProperty("multi_model_review");
            var review = aggregate.TryGetProperty("reviews", out var reviews) && reviews.ValueKind == JsonValueKind.Array && reviews.GetArrayLength() > 0
                ? reviews[0]
                : aggregate;
            var responseText = ReadJsonString(review, "response_text");
            if (string.IsNullOrWhiteSpace(responseText))
            {
                WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"{ReadJsonString(review, "status")} · {ReadJsonString(review, "error")}";
                WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text = ReadJsonString(aggregate, "prompt");
                return;
            }
            WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text = responseText;
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"模型评审完成：{ReadJsonString(review, "provider")} · {ReadJsonString(review, "model")}";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"模型评审失败：{ex.Message}";
        }
    }

    internal async Task RequestMultiModelReviewAsync()
    {
        var problem = WorkbenchShell.ManagementPanels.LearningProblemBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(problem))
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = "问题不能为空。";
            return;
        }
        WorkbenchShell.ManagementPanels.LearningSummaryText.Text = "正在请求多个协助模型...";
        try
        {
            using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new
            {
                action = "multi_model_review",
                skill_name = WorkbenchShell.ManagementPanels.LearningSkillBox.Text.Trim(),
                problem,
                context = WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text.Trim(),
            });
            var review = doc.RootElement.GetProperty("multi_model_review");
            var outputs = new List<string>();
            if (review.TryGetProperty("reviews", out var reviews) && reviews.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in reviews.EnumerateArray())
                {
                    var label = $"{ReadJsonString(item, "provider")} / {ReadJsonString(item, "model")}";
                    var text = ReadJsonString(item, "response_text");
                    var error = ReadJsonString(item, "error");
                    outputs.Add(string.IsNullOrWhiteSpace(text) ? $"[{label}]\n{error}" : $"[{label}]\n{text}");
                }
            }
            WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text = outputs.Count == 0 ? ReadJsonString(review, "prompt") : string.Join($"{Environment.NewLine}{Environment.NewLine}", outputs);
            var decision = review.TryGetProperty("decision", out var decisionElement) && decisionElement.ValueKind == JsonValueKind.Object ? decisionElement : default;
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = decision.ValueKind == JsonValueKind.Object
                ? $"评审团完成：{ReadJsonInt(review, "success_count")}/{ReadJsonInt(review, "total_count")} 成功 · {UiDisplayText.Status(ReadJsonString(decision, "status", "--"))} · 需要 {ReadJsonInt(decision, "required_success_count")}"
                : $"多模型协助完成：{ReadJsonInt(review, "success_count")}/{ReadJsonInt(review, "total_count")} 成功";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"多模型协助失败：{ex.Message}";
        }
    }
}
