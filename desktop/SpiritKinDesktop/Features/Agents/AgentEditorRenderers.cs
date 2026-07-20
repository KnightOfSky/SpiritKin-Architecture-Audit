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
    internal void RenderSelectedAgentEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.AgentsList.SelectedValue as string;
        var agent = _agents.FirstOrDefault(item => item.AgentId == selectedId) ?? _agents.FirstOrDefault();
        if (agent is null)
        {
            WorkbenchShell.ManagementPanels.AgentIdBox.Clear();
            WorkbenchShell.ManagementPanels.AgentLabelBox.Clear();
            WorkbenchShell.ManagementPanels.AgentDomainBox.Clear();
            WorkbenchShell.ManagementPanels.AgentRoleBox.Clear();
            WorkbenchShell.ManagementPanels.AgentModelSelectBox.SelectedValue = null;
            WorkbenchShell.ManagementPanels.AgentModelIdBox.Clear();
            WorkbenchShell.ManagementPanels.AgentProviderBox.Clear();
            WorkbenchShell.ManagementPanels.AgentModelBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.AgentFrameworkBox, "native");
            WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedValue = null;
            WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.Text = "";
            WorkbenchShell.ManagementPanels.AgentEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.AgentPriorityBox.Text = "50";
            WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Clear();
            WorkbenchShell.ManagementPanels.AgentKnowledgeBaseIdBox.Clear();
            WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Clear();
            WorkbenchShell.ManagementPanels.AgentBrainProfileBox.Clear();
            WorkbenchShell.ManagementPanels.AgentCapabilitiesBox.Clear();
            WorkbenchShell.ManagementPanels.AgentNotesBox.Clear();
            return;
        }
        WorkbenchShell.ManagementPanels.AgentIdBox.Text = agent.AgentId;
        WorkbenchShell.ManagementPanels.AgentLabelBox.Text = agent.Label;
        WorkbenchShell.ManagementPanels.AgentDomainBox.Text = agent.Domain;
        WorkbenchShell.ManagementPanels.AgentRoleBox.Text = agent.Role;
        WorkbenchShell.ManagementPanels.AgentModelSelectBox.SelectedValue = SelectExistingId(agent.ModelId, _assistModels.Select(item => item.ModelId));
        NormalizeAssistModelComboSelection(WorkbenchShell.ManagementPanels.AgentModelSelectBox);
        if (WorkbenchShell.ManagementPanels.AgentModelSelectBox.SelectedItem is null && !string.IsNullOrWhiteSpace(agent.ModelId))
        {
            WorkbenchShell.ManagementPanels.AgentModelSelectBox.Text = agent.ModelId;
        }
        WorkbenchShell.ManagementPanels.AgentModelIdBox.Text = agent.ModelId;
        WorkbenchShell.ManagementPanels.AgentProviderBox.Text = agent.Provider;
        WorkbenchShell.ManagementPanels.AgentModelBox.Text = agent.Model;
        SetComboText(WorkbenchShell.ManagementPanels.AgentFrameworkBox, agent.Framework);
        WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedValue = SelectExistingId(agent.Adapter, _agentAdapters.Select(item => item.AdapterId));
        if (WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.SelectedItem is null)
        {
            WorkbenchShell.ManagementPanels.AgentAdapterSelectBox.Text = agent.Adapter;
        }
        WorkbenchShell.ManagementPanels.AgentEnabledBox.IsChecked = agent.Enabled;
        WorkbenchShell.ManagementPanels.AgentPriorityBox.Text = agent.Priority.ToString();
        WorkbenchShell.ManagementPanels.AgentAllowedAssistantsBox.Text = string.Join(Environment.NewLine, agent.AllowedAssistantIds);
        WorkbenchShell.ManagementPanels.AgentKnowledgeBaseIdBox.Text = agent.KnowledgeBaseId;
        WorkbenchShell.ManagementPanels.AgentKnowledgeBasePathBox.Text = agent.KnowledgeBasePath;
        WorkbenchShell.ManagementPanels.AgentBrainProfileBox.Text = agent.BrainProfile;
        WorkbenchShell.ManagementPanels.AgentCapabilitiesBox.Text = string.Join(Environment.NewLine, agent.Capabilities);
        WorkbenchShell.ManagementPanels.AgentNotesBox.Text = agent.Notes;
    }

    internal void RenderSelectedExternalAssistantEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue as string;
        var assistant = _externalAssistants.FirstOrDefault(item => item.AssistantId == selectedId) ?? _externalAssistants.FirstOrDefault();
        if (assistant is null)
        {
            WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Clear();
            WorkbenchShell.ManagementPanels.ExternalAssistantLabelBox.Clear();
            WorkbenchShell.ManagementPanels.ExternalAssistantCommandBox.Clear();
            WorkbenchShell.ManagementPanels.ExternalAssistantWorkingDirectoryBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.ExternalAssistantKindBox, "cli");
            SetComboText(WorkbenchShell.ManagementPanels.ExternalAssistantCategoryBox, "general");
            WorkbenchShell.ManagementPanels.ExternalAssistantEnabledBox.IsChecked = false;
            WorkbenchShell.ManagementPanels.ExternalAssistantReviewOnlyBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.ExternalAssistantAllowWriteBox.IsChecked = false;
            return;
        }
        WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Text = assistant.AssistantId;
        WorkbenchShell.ManagementPanels.ExternalAssistantLabelBox.Text = assistant.Label;
        WorkbenchShell.ManagementPanels.ExternalAssistantCommandBox.Text = assistant.Command;
        WorkbenchShell.ManagementPanels.ExternalAssistantWorkingDirectoryBox.Text = assistant.WorkingDirectory;
        SetComboText(WorkbenchShell.ManagementPanels.ExternalAssistantKindBox, assistant.Kind);
        SetComboText(WorkbenchShell.ManagementPanels.ExternalAssistantCategoryBox, assistant.Category);
        WorkbenchShell.ManagementPanels.ExternalAssistantEnabledBox.IsChecked = assistant.Enabled;
        WorkbenchShell.ManagementPanels.ExternalAssistantReviewOnlyBox.IsChecked = assistant.ReviewOnly;
        WorkbenchShell.ManagementPanels.ExternalAssistantAllowWriteBox.IsChecked = assistant.AllowWrite;
    }

    internal void RenderSelectedKnowledgeBaseEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
        var knowledge = _knowledgeBases.FirstOrDefault(item => item.KnowledgeBaseId == selectedId) ?? _knowledgeBases.FirstOrDefault();
        if (knowledge is null)
        {
            WorkbenchShell.ManagementPanels.KnowledgeBaseIdBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeBaseLabelBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.SelectedValue = null;
            WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.Text = "";
            SetComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseDomainBox, "general");
            WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseScopeBox, "agent");
            WorkbenchShell.ManagementPanels.KnowledgeBaseEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.KnowledgeBaseNotesBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeBaseIndexStatusText.Text = "索引状态：未选择知识库";
            return;
        }
        WorkbenchShell.ManagementPanels.KnowledgeBaseIdBox.Text = knowledge.KnowledgeBaseId;
        WorkbenchShell.ManagementPanels.KnowledgeBaseLabelBox.Text = knowledge.Label;
        WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.SelectedValue = SelectExistingId(knowledge.OwnerAgentId, _agents.Select(item => item.AgentId));
        WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.Text = knowledge.OwnerAgentId;
        SetComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseDomainBox, knowledge.Domain);
        WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text = knowledge.Path;
        SetComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseScopeBox, knowledge.SharedScope);
        WorkbenchShell.ManagementPanels.KnowledgeBaseEnabledBox.IsChecked = knowledge.Enabled;
        WorkbenchShell.ManagementPanels.KnowledgeBaseNotesBox.Text = knowledge.Notes;
        WorkbenchShell.ManagementPanels.KnowledgeBaseIndexStatusText.Text = BuildKnowledgeBaseIndexReport(knowledge, writeManifest: false);
    }

    internal void RenderKnowledgeSources(JsonElement state)
    {
        var previousSource = WorkbenchShell.ManagementPanels.KnowledgeSourcesList.SelectedValue as string;
        _knowledgeSources.Clear();
        if (state.TryGetProperty("external_sources", out var sources) && sources.ValueKind == JsonValueKind.Array)
        {
            foreach (var source in sources.EnumerateArray())
            {
                _knowledgeSources.Add(KnowledgeSourceViewModel.FromJson(source));
            }
        }
        WorkbenchShell.ManagementPanels.KnowledgeSourcesList.SelectedValue = SelectExistingId(previousSource, _knowledgeSources.Select(item => item.SourceId))
            ?? _knowledgeSources.FirstOrDefault()?.SourceId;
        RenderSelectedKnowledgeSourceEditor();
    }

    internal void RenderSelectedKnowledgeSourceEditor()
    {
        var selected = _knowledgeSources.FirstOrDefault(item => item.SourceId == (WorkbenchShell.ManagementPanels.KnowledgeSourcesList.SelectedValue as string)) ?? _knowledgeSources.FirstOrDefault();
        if (selected is null)
        {
            WorkbenchShell.ManagementPanels.KnowledgeSourceIdBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeSourceLabelBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.KnowledgeSourceKindBox, "folder");
            WorkbenchShell.ManagementPanels.KnowledgeSourceTargetBox.SelectedValue = _knowledgeBases.FirstOrDefault()?.KnowledgeBaseId;
            WorkbenchShell.ManagementPanels.KnowledgeSourcePathBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeSourceEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.KnowledgeSourceRecursiveBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.KnowledgeSourceIgnoreBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeSourceTagsBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeSourceNotesBox.Clear();
            WorkbenchShell.ManagementPanels.KnowledgeSourceStatusText.Text = "外部源状态：未选择";
            return;
        }
        WorkbenchShell.ManagementPanels.KnowledgeSourceIdBox.Text = selected.SourceId;
        WorkbenchShell.ManagementPanels.KnowledgeSourceLabelBox.Text = selected.Label;
        SetComboText(WorkbenchShell.ManagementPanels.KnowledgeSourceKindBox, selected.Kind);
        WorkbenchShell.ManagementPanels.KnowledgeSourceTargetBox.SelectedValue = SelectExistingId(selected.KnowledgeBaseId, _knowledgeBases.Select(item => item.KnowledgeBaseId));
        WorkbenchShell.ManagementPanels.KnowledgeSourcePathBox.Text = selected.Path;
        WorkbenchShell.ManagementPanels.KnowledgeSourceEnabledBox.IsChecked = selected.Enabled;
        WorkbenchShell.ManagementPanels.KnowledgeSourceRecursiveBox.IsChecked = selected.Recursive;
        WorkbenchShell.ManagementPanels.KnowledgeSourceIgnoreBox.Text = string.Join(Environment.NewLine, selected.IgnorePatterns);
        WorkbenchShell.ManagementPanels.KnowledgeSourceTagsBox.Text = string.Join(Environment.NewLine, selected.TagFilter);
        WorkbenchShell.ManagementPanels.KnowledgeSourceNotesBox.Text = selected.Notes;
        WorkbenchShell.ManagementPanels.KnowledgeSourceStatusText.Text = selected.StatusLine;
    }

    internal void RenderSelectedRouteProfileEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue as string;
        var profile = _routeProfiles.FirstOrDefault(item => item.ProfileId == selectedId) ?? _routeProfiles.FirstOrDefault();
        if (profile is null)
        {
            WorkbenchShell.ManagementPanels.RouteProfileIdBox.Clear();
            WorkbenchShell.ManagementPanels.RouteProfileLabelBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.RouteStrategyBox, "primary_with_specialists");
            WorkbenchShell.ManagementPanels.RouteProfileEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.RouteMembersJsonBox.Text = "[]";
            WorkbenchShell.ManagementPanels.RouteProfileNotesBox.Clear();
            return;
        }
        WorkbenchShell.ManagementPanels.RouteProfileIdBox.Text = profile.ProfileId;
        WorkbenchShell.ManagementPanels.RouteProfileLabelBox.Text = profile.Label;
        SetComboText(WorkbenchShell.ManagementPanels.RouteStrategyBox, profile.Strategy);
        WorkbenchShell.ManagementPanels.RouteProfileEnabledBox.IsChecked = profile.Enabled;
        WorkbenchShell.ManagementPanels.RouteMembersJsonBox.Text = profile.MembersJson;
        WorkbenchShell.ManagementPanels.RouteProfileNotesBox.Text = profile.Notes;
    }

    internal void RenderSelectedRemoteTargetEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue as string;
        var target = _remoteTargets.FirstOrDefault(item => item.TargetId == selectedId) ?? _remoteTargets.FirstOrDefault();
        if (target is null)
        {
            WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Clear();
            WorkbenchShell.ManagementPanels.RemoteTargetLabelBox.Clear();
            WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Clear();
            WorkbenchShell.ManagementPanels.RemoteTargetEnabledBox.IsChecked = false;
            WorkbenchShell.ManagementPanels.RemoteTargetTokenSetBox.IsChecked = false;
            WorkbenchShell.ManagementPanels.RemoteTargetCapabilitiesBox.Clear();
            return;
        }
        WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text = target.TargetId;
        WorkbenchShell.ManagementPanels.RemoteTargetLabelBox.Text = target.Label;
        WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Text = target.BaseUrl;
        WorkbenchShell.ManagementPanels.RemoteTargetEnabledBox.IsChecked = target.Enabled;
        WorkbenchShell.ManagementPanels.RemoteTargetTokenSetBox.IsChecked = target.TokenSet;
        WorkbenchShell.ManagementPanels.RemoteTargetCapabilitiesBox.Text = string.Join(Environment.NewLine, target.Capabilities);
    }

    internal void RenderSelectedAgentAdapterEditor()
    {
        var selectedId = WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue as string;
        var adapter = _agentAdapters.FirstOrDefault(item => item.AdapterId == selectedId) ?? _agentAdapters.FirstOrDefault();
        if (adapter is null)
        {
            WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterLabelBox.Clear();
            SetComboText(WorkbenchShell.ManagementPanels.AgentAdapterKindBox, "native");
            SetComboText(WorkbenchShell.ManagementPanels.AgentAdapterFrameworkBox, "spiritkin_native");
            WorkbenchShell.ManagementPanels.AgentAdapterCommandBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterModuleBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterEndpointBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterWorkingDirectoryBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterEnabledBox.IsChecked = true;
            WorkbenchShell.ManagementPanels.AgentAdapterReviewOnlyBox.IsChecked = false;
            WorkbenchShell.ManagementPanels.AgentAdapterAllowWriteBox.IsChecked = false;
            WorkbenchShell.ManagementPanels.AgentAdapterCapabilitiesBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterOwnerAgentsBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterNotesBox.Clear();
            WorkbenchShell.ManagementPanels.AgentAdapterStatusText.Text = "Adapter 状态：未选择";
            return;
        }
        WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Text = adapter.AdapterId;
        WorkbenchShell.ManagementPanels.AgentAdapterLabelBox.Text = adapter.Label;
        SetComboText(WorkbenchShell.ManagementPanels.AgentAdapterKindBox, adapter.Kind);
        SetComboText(WorkbenchShell.ManagementPanels.AgentAdapterFrameworkBox, adapter.Framework);
        WorkbenchShell.ManagementPanels.AgentAdapterCommandBox.Text = adapter.Command;
        WorkbenchShell.ManagementPanels.AgentAdapterModuleBox.Text = adapter.Module;
        WorkbenchShell.ManagementPanels.AgentAdapterEndpointBox.Text = adapter.Endpoint;
        WorkbenchShell.ManagementPanels.AgentAdapterWorkingDirectoryBox.Text = adapter.WorkingDirectory;
        WorkbenchShell.ManagementPanels.AgentAdapterEnabledBox.IsChecked = adapter.Enabled;
        WorkbenchShell.ManagementPanels.AgentAdapterReviewOnlyBox.IsChecked = adapter.ReviewOnly;
        WorkbenchShell.ManagementPanels.AgentAdapterAllowWriteBox.IsChecked = adapter.AllowWrite;
        WorkbenchShell.ManagementPanels.AgentAdapterCapabilitiesBox.Text = string.Join(Environment.NewLine, adapter.Capabilities);
        WorkbenchShell.ManagementPanels.AgentAdapterOwnerAgentsBox.Text = string.Join(Environment.NewLine, adapter.OwnerAgentIds);
        WorkbenchShell.ManagementPanels.AgentAdapterNotesBox.Text = adapter.Notes;
        WorkbenchShell.ManagementPanels.AgentAdapterStatusText.Text = $"Adapter 状态：{UiDisplayText.Status(adapter.HealthStatus)} · {adapter.HealthDetail}";
    }

}
