using System;
using System.Linq;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class AgentsController
{
    internal void AgentsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (IsRendering)
        {
            return;
        }
        RenderSelectedAgentEditor();
    }

    internal void AgentModelSelectBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (IsRendering)
        {
            return;
        }
        var selectedId = WorkbenchShell.ManagementPanels.AgentModelSelectBox.SelectedValue as string;
        var model = _assistModels.FirstOrDefault(item => string.Equals(item.ModelId, selectedId, StringComparison.OrdinalIgnoreCase));
        if (model is null)
        {
            return;
        }
        WorkbenchShell.ManagementPanels.AgentModelIdBox.Text = model.ModelId;
        WorkbenchShell.ManagementPanels.AgentProviderBox.Text = model.Provider;
        WorkbenchShell.ManagementPanels.AgentModelBox.Text = model.Model;
        NormalizeAssistModelComboSelection(WorkbenchShell.ManagementPanels.AgentModelSelectBox);
        ApplySelectedAgentFromEditor(showMessage: false);
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已选择模型：{model.DisplayName}。保存集群配置后生效。";
    }

    internal void ExternalAssistantsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedExternalAssistantEditor();
        }
    }

    internal void AgentAdaptersList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedAgentAdapterEditor();
        }
    }

    internal void KnowledgeBasesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedKnowledgeBaseEditor();
        }
    }

    internal void KnowledgeSourcesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedKnowledgeSourceEditor();
        }
    }

    internal void RouteProfilesList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedRouteProfileEditor();
        }
    }

    internal void RemoteTargetsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!IsRendering)
        {
            RenderSelectedRemoteTargetEditor();
        }
    }
}
