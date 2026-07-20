using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal async Task SaveWorkflowDefinitionAsync()
    {
        if (!TryBuildWorkflowDefinitionPayload(out var definition, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = error;
            return;
        }
        await WorkflowActionAsync("upsert_definition", new Dictionary<string, object?>
        {
            ["workflow_name"] = Convert.ToString(definition["name"]) ?? ActiveWorkflowName(),
            ["definition"] = definition,
            ["project_root"] = _rootDir,
            ["actor"] = "spiritkin_wpf_desktop",
            ["reason"] = "saved from WPF workflow designer",
        });
    }

    internal async Task ComposeWorkflowDefinitionAsync(bool startAfterSave)
    {
        if (!TryBuildWorkflowComposePayload(out var payload, out var workflowName, out var error))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = error;
            return;
        }

        payload["actor"] = "spiritkin_wpf_desktop";
        payload["reason"] = "composed from WPF workflow composer";
        var saved = await WorkflowActionAsync("compose_definition", payload);
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = workflowName;
        WorkbenchShell.ManagementPanels.WorkflowDefinitionCatalogList.SelectedValue = workflowName;
        if (!startAfterSave || !saved)
        {
            return;
        }

        if (!TryParseJsonObject(WorkbenchShell.ManagementPanels.WorkflowComposeStartInputsBox.Text, out var inputs, out error))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"组合已保存，启动输入 JSON 无效：{error}";
            return;
        }
        if (!inputs.ContainsKey("project_root"))
        {
            inputs["project_root"] = _rootDir;
        }
        await WorkflowActionAsync("start_run", new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
            ["project_root"] = _rootDir,
            ["inputs"] = inputs,
        });
    }

    internal bool TryBuildWorkflowComposePayload(out Dictionary<string, object?> payload, out string workflowName, out string error)
    {
        payload = new Dictionary<string, object?>();
        workflowName = WorkbenchShell.ManagementPanels.WorkflowComposeNameBox.Text.Trim();
        error = "";
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            error = "组合工作流 ID 不能为空。";
            return false;
        }
        var components = BuildWorkflowComposeComponents();
        if (components.Count == 0)
        {
            error = "请至少填写一个组件工作流 ID。";
            return false;
        }
        payload["workflow_name"] = workflowName;
        payload["display_name"] = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.WorkflowComposeDisplayNameBox.Text) ? workflowName : WorkbenchShell.ManagementPanels.WorkflowComposeDisplayNameBox.Text.Trim();
        payload["mode"] = ComboText(WorkbenchShell.ManagementPanels.WorkflowComposeModeBox).Trim();
        payload["components"] = components;
        payload["project_root"] = _rootDir;
        return true;
    }

    internal List<Dictionary<string, object?>> BuildWorkflowComposeComponents()
    {
        var components = new List<Dictionary<string, object?>>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var rawLine in SplitLines(WorkbenchShell.ManagementPanels.WorkflowComposeComponentsBox.Text))
        {
            var parts = rawLine.Split('|', 2, StringSplitOptions.TrimEntries);
            var workflowName = parts.FirstOrDefault()?.Trim() ?? "";
            if (string.IsNullOrWhiteSpace(workflowName) || !seen.Add(workflowName))
            {
                continue;
            }
            var item = new Dictionary<string, object?> { ["workflow_name"] = workflowName };
            if (parts.Length > 1 && !string.IsNullOrWhiteSpace(parts[1]))
            {
                item["label"] = parts[1].Trim();
            }
            components.Add(item);
        }
        return components;
    }

    internal void AddSelectedWorkflowToComposeList()
    {
        var workflowName = ActiveWorkflowName();
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "当前没有可添加的工作流定义。";
            return;
        }
        var lines = SplitLines(WorkbenchShell.ManagementPanels.WorkflowComposeComponentsBox.Text).ToList();
        if (!lines.Any(line => string.Equals(line.Split('|', 2, StringSplitOptions.TrimEntries)[0], workflowName, StringComparison.OrdinalIgnoreCase)))
        {
            lines.Add(workflowName);
        }
        WorkbenchShell.ManagementPanels.WorkflowComposeComponentsBox.Text = string.Join(Environment.NewLine, lines);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已加入组合组件：{workflowName}";
    }

    internal async Task RestoreBuiltinWorkflowDefinitionAsync()
    {
        var workflowName = ActiveWorkflowName();
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择要恢复的内置工作流。";
            return;
        }
        if (!workflowName.Equals("ecommerce.auto_listing.v1", StringComparison.OrdinalIgnoreCase)
            && !workflowName.Equals("content.video_generation.v1", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "当前工作流不是内置模板；只有电商自动上架和视频生成可恢复内置蓝图。";
            return;
        }
        await WorkflowActionAsync("save_builtin_definition", new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
            ["project_root"] = _rootDir,
            ["actor"] = "spiritkin_wpf_desktop",
        });
    }

    internal async Task RollbackWorkflowDefinitionAsync()
    {
        if (WorkbenchShell.ManagementPanels.WorkflowVersionsList.SelectedItem is not WorkflowVersionViewModel version)
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择一个历史版本。";
            return;
        }
        if (!ConfirmDestructiveAction("回滚工作流定义", $"确定将“{ActiveWorkflowName()}”回滚到版本 {version.VersionId} 吗？当前定义会先写入历史记录。"))
        {
            return;
        }
        await WorkflowActionAsync("rollback_definition", new Dictionary<string, object?>
        {
            ["workflow_name"] = ActiveWorkflowName(),
            ["version_id"] = version.VersionId,
            ["project_root"] = _rootDir,
            ["actor"] = "spiritkin_wpf_desktop",
        });
    }

    internal async Task DeleteWorkflowDefinitionAsync()
    {
        var workflowName = ActiveWorkflowName();
        if (string.IsNullOrWhiteSpace(workflowName))
        {
            WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "请先选择要删除的工作流定义。";
            return;
        }
        if (!ConfirmDestructiveAction("删除工作流定义", $"确定要删除已保存工作流定义“{workflowName}”吗？内置模板不会被删除。"))
        {
            return;
        }
        await WorkflowActionAsync("delete_definition", new Dictionary<string, object?>
        {
            ["workflow_name"] = workflowName,
            ["project_root"] = _rootDir,
        });
    }

    internal void NewWorkflowDefinition()
    {
        var name = $"custom.workflow.{DateTime.Now:yyyyMMddHHmmss}.v1";
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = name;
        WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text = "自定义工作流";
        WorkbenchShell.ManagementPanels.WorkflowVersionBox.Text = "0.1.0";
        WorkbenchShell.ManagementPanels.WorkflowCategoryBox.Text = "自定义";
        WorkbenchShell.ManagementPanels.WorkflowDescriptionBox.Text = "自定义节点式工作流。";
        _workflowEditNodes.Clear();
        _workflowEditNodes.Add(new WorkflowEditNodeViewModel(
            "start",
            "开始",
            "agent_task",
            WorkflowAgentId(),
            "",
            "",
            "",
            "",
            "{}",
            24,
            LaneDefaultY("agent_task")));
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = "start";
        RefreshWorkflowDefinitionPreviewFromEditor("start");
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = "已创建新的工作流草稿；编辑节点后点击保存定义。";
    }

    internal void DuplicateWorkflowDefinition()
    {
        var originalName = ActiveWorkflowName();
        var name = $"{originalName}.copy.{DateTime.Now:HHmmss}";
        WorkbenchShell.ManagementPanels.WorkflowNameBox.Text = name;
        WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text = $"{WorkbenchShell.ManagementPanels.WorkflowDisplayNameBox.Text.Trim()} Copy".Trim();
        var copied = _workflowEditNodes
            .Select((node, index) => node.WithPosition(24 + (index % 4) * WorkflowNodeHorizontalGap, LaneDefaultY(node.NodeType) + (index / 4) * 18))
            .ToArray();
        _workflowEditNodes.Clear();
        foreach (var node in copied)
        {
            _workflowEditNodes.Add(node);
        }
        RefreshWorkflowDefinitionPreviewFromEditor(WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue as string);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已复制为新工作流：{name}。";
    }
}
