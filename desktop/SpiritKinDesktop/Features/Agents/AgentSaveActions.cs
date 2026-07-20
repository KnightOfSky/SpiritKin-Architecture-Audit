using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace SpiritKinDesktop;

internal sealed partial class AgentsController
{
    internal async Task SaveAgentManagementAsync()
    {
        if (!ApplySelectedAgentFromEditor(showMessage: false))
        {
            return;
        }
        if (!ApplySelectedExternalAssistantFromEditor(showMessage: false))
        {
            return;
        }
        if (!ApplySelectedAgentAdapterFromEditor(showMessage: false))
        {
            return;
        }
        if (!ApplySelectedKnowledgeBaseFromEditor(showMessage: false))
        {
            return;
        }
        if (!ApplyRouteProfileOrShowError(showMessage: false))
        {
            return;
        }
        if (!ApplySelectedRemoteTargetFromEditor(showMessage: false))
        {
            return;
        }
        var profileId = (WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.RouteProfileIdBox.Text.Trim();
        var payload = new
        {
            action = "save",
            state = new
            {
                agents = BuildAgentsPayload(),
                active_route_profile_id = profileId,
                skill_assist = new
                {
                    enabled = WorkbenchShell.ManagementPanels.SkillAssistEnabledBox.IsChecked == true,
                    mode = ComboText(WorkbenchShell.ManagementPanels.SkillAssistModeBox),
                    require_before_run = WorkbenchShell.ManagementPanels.SkillAssistBeforeRunBox.IsChecked == true,
                    require_on_failure = WorkbenchShell.ManagementPanels.SkillAssistOnFailureBox.IsChecked == true,
                    allow_external_model = WorkbenchShell.ManagementPanels.SkillAssistExternalModelBox.IsChecked == true,
                    allow_external_cli = WorkbenchShell.ManagementPanels.SkillAssistExternalCliBox.IsChecked == true,
                    selected_assistant_id = (WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Text.Trim(),
                },
                external_assistants = BuildExternalAssistantsPayload(),
                agent_adapters = BuildAgentAdaptersPayload(),
                knowledge_bases = BuildKnowledgeBasesPayload(),
                route_profiles = BuildRouteProfilesPayload(),
                remote_targets = BuildRemoteTargetsPayload(),
            }
        };
        using var response = await PostJsonAsync($"{ApiBase()}/desktop/agent-management", payload);
        RenderAgentManagement(response.RootElement.GetProperty("agent_management"));
    }

    internal async Task<bool> SaveKnowledgeBaseConfigurationAsync(string successMessage)
    {
        try
        {
            var payload = new
            {
                action = "save",
                state = new
                {
                    knowledge_bases = BuildKnowledgeBasesPayload(),
                }
            };
            using var response = await PostJsonAsync($"{ApiBase()}/desktop/agent-management", payload);
            RenderAgentManagement(response.RootElement.GetProperty("agent_management"));
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = successMessage;
            return true;
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"知识库配置保存失败：{ex.Message}";
            return false;
        }
    }

}
