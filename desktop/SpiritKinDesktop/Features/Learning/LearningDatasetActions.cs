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
    internal async Task SaveLearningRecordAsync()
    {
        var problem = WorkbenchShell.ManagementPanels.LearningProblemBox.Text.Trim();
        var correction = WorkbenchShell.ManagementPanels.LearningCorrectionBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(problem) || string.IsNullOrWhiteSpace(correction))
        {
            WorkbenchShell.ManagementPanels.LearningSummaryText.Text = "问题和纠正内容不能为空。";
            return;
        }
        var payload = new
        {
            action = "record",
            record = new
            {
                source = ComboText(WorkbenchShell.ManagementPanels.LearningModeBox) == "cloud_model_review" ? "external_model" : "human",
                skill_name = WorkbenchShell.ManagementPanels.LearningSkillBox.Text.Trim(),
                problem,
                correction,
                project = ActiveSession().Title,
                tags = new[] { "desktop_feedback", ComboText(WorkbenchShell.ManagementPanels.LearningModeBox) },
            }
        };
        using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", payload);
        var dataset = doc.RootElement.GetProperty("dataset");
        WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"已保存学习样本，并导出训练集：{ReadJsonString(dataset, "path")} ({ReadJsonInt(dataset, "count")} 条)";
        WorkbenchShell.ManagementPanels.LearningProblemBox.Clear();
        WorkbenchShell.ManagementPanels.LearningCorrectionBox.Clear();
        await LoadLearningAsync();
    }

    internal async Task ExportLearningDatasetAsync()
    {
        using var doc = await PostJsonAsync($"{ApiBase()}/desktop/learning", new { action = "export_dataset" });
        var dataset = doc.RootElement.GetProperty("dataset");
        WorkbenchShell.ManagementPanels.LearningSummaryText.Text = $"训练集已导出：{ReadJsonString(dataset, "path")} ({ReadJsonInt(dataset, "count")} 条)";
    }
}

