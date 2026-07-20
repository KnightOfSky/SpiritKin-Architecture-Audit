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
    internal void RenderWorkflowGovernance(JsonElement workflows)
    {
        _workflowVersions.Clear();
        if (workflows.TryGetProperty("definition_versions", out var versions) && versions.ValueKind == JsonValueKind.Array)
        {
            foreach (var version in versions.EnumerateArray())
            {
                _workflowVersions.Add(WorkflowVersionViewModel.FromJson(version));
            }
        }
        WorkbenchShell.ManagementPanels.WorkflowVersionsList.SelectedValue = _workflowVersions.FirstOrDefault()?.VersionId;

        var auditLines = new List<string>();
        if (workflows.TryGetProperty("audit_events", out var auditEvents) && auditEvents.ValueKind == JsonValueKind.Array)
        {
            foreach (var audit in auditEvents.EnumerateArray().Take(12))
            {
                auditLines.Add($"{ReadJsonString(audit, "at", "--")} · {ReadJsonString(audit, "actor", "system")} · {ReadJsonString(audit, "action", "--")} · {ReadJsonString(audit, "message", "")}");
            }
        }
        WorkbenchShell.ManagementPanels.WorkflowAuditBox.Text = auditLines.Count == 0 ? "暂无审计事件。" : string.Join(Environment.NewLine, auditLines);

        if (TryReadJsonObject(workflows, "permission_policy", out var permissions))
        {
            var governanceText = "";
            if (TryReadJsonObject(workflows, "governance_policy", out var governance))
            {
                var forbiddenActions = governance.TryGetProperty("forbidden_actions", out var forbiddenActionArray) && forbiddenActionArray.ValueKind == JsonValueKind.Array ? forbiddenActionArray.GetArrayLength() : 0;
                var forbiddenTypes = governance.TryGetProperty("forbidden_node_types", out var forbiddenTypeArray) && forbiddenTypeArray.ValueKind == JsonValueKind.Array ? forbiddenTypeArray.GetArrayLength() : 0;
                var contractCount = governance.TryGetProperty("interface_contracts", out var contracts) && contracts.ValueKind == JsonValueKind.Object ? contracts.EnumerateObject().Count() : 0;
                governanceText = $" · 治理 {ReadJsonString(governance, "mode", "advisory")} · 禁止动作 {forbiddenActions} · 禁止类型 {forbiddenTypes} · 契约 {contractCount}";
            }
            WorkbenchShell.ManagementPanels.WorkflowGovernanceText.Text = $"权限模式 {ReadJsonString(permissions, "mode", "desktop_owner_edit")} · 审计 {(ReadJsonBool(permissions, "audit_required", true) ? "开启" : "关闭")} · 回滚审批 {(ReadJsonBool(permissions, "rollback_requires_approval", false) ? "需要" : "不需要")}{governanceText}";
        }
        else
        {
            WorkbenchShell.ManagementPanels.WorkflowGovernanceText.Text = "保存定义后会记录历史版本和审计事件。";
        }
    }

    internal void RenderWorkflowMetrics(JsonElement workflows)
    {
        if (!TryReadJsonObject(workflows, "overview", out var overview))
        {
            ResetWorkflowMetrics();
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowDefinitionCountText.Text = ReadJsonInt(overview, "definition_count").ToString();
        WorkbenchShell.ManagementPanels.WorkflowBuiltinDefinitionCountText.Text = ReadJsonInt(overview, "builtin_definition_count").ToString();
        WorkbenchShell.ManagementPanels.WorkflowRunCountText.Text = ReadJsonInt(overview, "run_count").ToString();
        WorkbenchShell.ManagementPanels.WorkflowActiveRunCountText.Text = ReadJsonInt(overview, "active_run_count").ToString();
        var review = 0;
        var blocked = 0;
        if (TryReadJsonObject(overview, "status_counts", out var statusCounts))
        {
            review = ReadJsonInt(statusCounts, "waiting_review");
            blocked = ReadJsonInt(statusCounts, "blocked") + ReadJsonInt(statusCounts, "failed");
        }
        WorkbenchShell.ManagementPanels.WorkflowReviewRunCountText.Text = review.ToString();
        WorkbenchShell.ManagementPanels.WorkflowBlockedRunCountText.Text = blocked.ToString();
    }

    internal void ResetWorkflowMetrics()
    {
        WorkbenchShell.ManagementPanels.WorkflowDefinitionCountText.Text = "--";
        WorkbenchShell.ManagementPanels.WorkflowBuiltinDefinitionCountText.Text = "--";
        WorkbenchShell.ManagementPanels.WorkflowRunCountText.Text = "--";
        WorkbenchShell.ManagementPanels.WorkflowActiveRunCountText.Text = "--";
        WorkbenchShell.ManagementPanels.WorkflowReviewRunCountText.Text = "--";
        WorkbenchShell.ManagementPanels.WorkflowBlockedRunCountText.Text = "--";
    }

    internal void UpdateWorkflowInputMode(string workflowName)
    {
        if (workflowName.Contains("video", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.WorkflowInputModeText.Text = "视频生成：填写 prompt、时长、画幅、参考图等输入后启动运行。";
            if (string.IsNullOrWhiteSpace(WorkflowAgentId()) || string.Equals(WorkflowAgentId(), "ecommerce", StringComparison.OrdinalIgnoreCase))
            {
                SetComboText(WorkbenchShell.ManagementPanels.WorkflowAgentSelectBox, "video_animation");
            }
            return;
        }

        WorkbenchShell.ManagementPanels.WorkflowInputModeText.Text = "电商自动化上架：链接入队、OCR / 图片产物、productData、审核和草稿发布。";
        if (string.IsNullOrWhiteSpace(WorkflowAgentId()) || string.Equals(WorkflowAgentId(), "video_animation", StringComparison.OrdinalIgnoreCase))
        {
            SetComboText(WorkbenchShell.ManagementPanels.WorkflowAgentSelectBox, "ecommerce");
        }
    }

}
