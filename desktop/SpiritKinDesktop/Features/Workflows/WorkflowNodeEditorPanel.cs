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
    internal WorkflowNodeTemplateViewModel SelectedWorkflowNodeTemplate() =>
        WorkbenchShell.ManagementPanels.WorkflowNodeTemplateBox.SelectedItem as WorkflowNodeTemplateViewModel
        ?? _workflowNodeTemplates.FirstOrDefault()
        ?? new WorkflowNodeTemplateViewModel("agent_task", "Agent 任务", "agent_task", "agent_task", "Agent 处理", "", "", "", "", "{}", "");

    internal void ApplyWorkflowNodeTemplateToEditor()
    {
        var template = SelectedWorkflowNodeTemplate();
        var currentNodeId = WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(currentNodeId))
        {
            currentNodeId = UniqueWorkflowNodeId(template.BaseNodeId);
            WorkbenchShell.ManagementPanels.WorkflowNodeIdBox.Text = currentNodeId;
        }
        WorkbenchShell.ManagementPanels.WorkflowNodeLabelBox.Text = template.Label;
        SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeTypeBox, template.NodeType);
        SetComboText(WorkbenchShell.ManagementPanels.WorkflowNodeAgentBox, string.IsNullOrWhiteSpace(template.AssignedAgent) ? WorkflowAgentId() : template.AssignedAgent);
        WorkbenchShell.ManagementPanels.WorkflowNodeToolNameBox.Text = template.ToolName;
        WorkbenchShell.ManagementPanels.WorkflowNodeSkillNameBox.Text = template.SkillName;
        WorkbenchShell.ManagementPanels.WorkflowNodeReviewGateBox.Text = template.ReviewGate;
        WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text = template.ArgumentsJson;
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = $"已套用模板：{template.DisplayName}。检查依赖后点击“应用节点”。";
    }

    internal void InsertWorkflowNodeFromTemplate()
    {
        var template = SelectedWorkflowNodeTemplate();
        var dependsOn = ActiveWorkflowEditNodeId();
        var nodeId = UniqueWorkflowNodeId(template.BaseNodeId);
        PushWorkflowUndoSnapshot("insert template node");
        var nodeType = template.NodeType;
        var node = new WorkflowEditNodeViewModel(
            nodeId,
            template.Label,
            nodeType,
            string.IsNullOrWhiteSpace(template.AssignedAgent) ? WorkflowAgentId() : template.AssignedAgent,
            dependsOn,
            template.ToolName,
            template.SkillName,
            template.ReviewGate,
            template.ArgumentsJson,
            24 + (_workflowEditNodes.Count % 4) * WorkflowNodeHorizontalGap,
            LaneDefaultY(nodeType));
        _workflowEditNodes.Add(node);
        WorkbenchShell.ManagementPanels.WorkflowEditNodesList.SelectedValue = node.NodeId;
        SelectSingleWorkflowGraphNode(node.NodeId);
        RenderWorkflowNodeEditor(node);
        RefreshWorkflowDefinitionPreviewFromEditor(node.NodeId);
        WorkbenchShell.ManagementPanels.WorkflowActionText.Text = $"已插入模板节点：{template.DisplayName} -> {node.NodeId}";
    }

    internal void InsertCustomWorkflowNode()
    {
        var node = AddWorkflowEditNodeFromCanvas("custom.open_node", ActiveWorkflowEditNodeId(), null);
        if (node is null)
        {
            return;
        }
        WorkbenchShell.ManagementPanels.WorkflowDesignerValidationText.Text = "已插入自定义节点；把节点类型改成 custom.xxx / external.xxx / integration.xxx / automation.xxx，并在参数区添加任意参数。空参数值会生成 {{参数名}} 启动参数。";
    }

}
