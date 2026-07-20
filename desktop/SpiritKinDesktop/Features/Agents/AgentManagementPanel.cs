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
    internal async Task LoadAgentManagementAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/agent-management");
            RenderAgentManagement(doc.RootElement.GetProperty("agent_management"));
            await LoadKnowledgeBaseRuntimeStateAsync();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"集群管理加载失败：{ex.Message}";
        }
    }

    internal async Task LoadKnowledgeBaseRuntimeStateAsync()
    {
        try
        {
            using var doc = await GetJsonAsync($"{ApiBase()}/desktop/knowledge-base");
            var kb = doc.RootElement.GetProperty("knowledge_base");
            RefreshKnowledgeBaseIndexStatuses(kb);
            RenderKnowledgeSources(kb);
        }
        catch
        {
            // Agent management remains usable without the optional KB runtime endpoint.
        }
    }

    internal void RenderAgentManagement(JsonElement state)
    {
        SetRendering(true);
        try
        {
            _lastAgentManagementState = state.Clone();
            var assist = state.GetProperty("skill_assist");
            WorkbenchShell.ManagementPanels.SkillAssistEnabledBox.IsChecked = ReadJsonBool(assist, "enabled", false);
            WorkbenchShell.ManagementPanels.SkillAssistBeforeRunBox.IsChecked = ReadJsonBool(assist, "require_before_run", false);
            WorkbenchShell.ManagementPanels.SkillAssistOnFailureBox.IsChecked = ReadJsonBool(assist, "require_on_failure", true);
            WorkbenchShell.ManagementPanels.SkillAssistExternalModelBox.IsChecked = ReadJsonBool(assist, "allow_external_model", true);
            WorkbenchShell.ManagementPanels.SkillAssistExternalCliBox.IsChecked = ReadJsonBool(assist, "allow_external_cli", false);
            SetComboText(WorkbenchShell.ManagementPanels.SkillAssistModeBox, ReadJsonString(assist, "mode"));

            var previousAgent = WorkbenchShell.ManagementPanels.AgentsList.SelectedValue as string;
            _agents.Clear();
            if (state.TryGetProperty("agents", out var agents) && agents.ValueKind == JsonValueKind.Array)
            {
                foreach (var agent in agents.EnumerateArray())
                {
                    _agents.Add(new AgentViewModel(
                        ReadJsonString(agent, "agent_id"),
                        ReadJsonString(agent, "label"),
                        ReadJsonString(agent, "domain"),
                        ReadJsonString(agent, "role"),
                        ReadJsonString(agent, "provider"),
                        ReadJsonString(agent, "model"),
                        ReadJsonString(agent, "model_id"),
                        ReadJsonString(agent, "framework", "native"),
                        ReadJsonString(agent, "adapter", "coordinator_router"),
                        ReadJsonBool(agent, "enabled", true),
                        ReadJsonInt(agent, "priority"),
                        ReadJsonStringArray(agent, "capabilities"),
                        ReadJsonStringArray(agent, "allowed_assistant_ids"),
                        ReadJsonString(agent, "knowledge_base_id"),
                        ReadJsonString(agent, "knowledge_base_path"),
                        ReadJsonString(agent, "brain_profile"),
                        ReadJsonString(agent, "notes")));
                }
            }
            WorkbenchShell.ManagementPanels.AgentsList.SelectedValue = !string.IsNullOrWhiteSpace(previousAgent) && _agents.Any(agent => agent.AgentId == previousAgent)
                ? previousAgent
                : _agents.FirstOrDefault()?.AgentId;

            var selectedAssistantId = ReadJsonString(assist, "selected_assistant_id");
            var previousAssistant = WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue as string;
            _externalAssistants.Clear();
            if (state.TryGetProperty("external_assistants", out var assistants) && assistants.ValueKind == JsonValueKind.Array)
            {
                foreach (var assistant in assistants.EnumerateArray())
                {
                    _externalAssistants.Add(new ExternalAssistantViewModel(
                        ReadJsonString(assistant, "assistant_id"),
                        ReadJsonString(assistant, "label"),
                        ReadJsonString(assistant, "kind"),
                        ReadJsonString(assistant, "command"),
                        ReadJsonString(assistant, "working_directory"),
                        ReadJsonString(assistant, "category"),
                        ReadJsonBool(assistant, "enabled", false),
                        ReadJsonBool(assistant, "allow_write", false),
                        ReadJsonBool(assistant, "review_only", true)));
                }
            }
            WorkbenchShell.ManagementPanels.ExternalAssistantsList.SelectedValue = SelectExistingId(previousAssistant, _externalAssistants.Select(item => item.AssistantId))
                ?? SelectExistingId(selectedAssistantId, _externalAssistants.Select(item => item.AssistantId))
                ?? _externalAssistants.FirstOrDefault()?.AssistantId;
            RenderSelectedExternalAssistantEditor();

            var previousAdapter = WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue as string;
            _agentAdapters.Clear();
            if (state.TryGetProperty("agent_adapters", out var adapters) && adapters.ValueKind == JsonValueKind.Array)
            {
                foreach (var adapter in adapters.EnumerateArray())
                {
                    _agentAdapters.Add(new AgentAdapterViewModel(
                        ReadJsonString(adapter, "adapter_id"),
                        ReadJsonString(adapter, "label"),
                        ReadJsonString(adapter, "kind", "native"),
                        ReadJsonString(adapter, "framework", "spiritkin_native"),
                        ReadJsonString(adapter, "command"),
                        ReadJsonString(adapter, "module"),
                        ReadJsonString(adapter, "endpoint"),
                        ReadJsonString(adapter, "working_directory"),
                        ReadJsonBool(adapter, "enabled", true),
                        ReadJsonBool(adapter, "allow_write", false),
                        ReadJsonBool(adapter, "review_only", false),
                        ReadJsonStringArray(adapter, "capabilities"),
                        ReadJsonStringArray(adapter, "owner_agent_ids"),
                        ReadJsonString(adapter, "health_status", "unknown"),
                        ReadJsonString(adapter, "health_detail"),
                        ReadJsonString(adapter, "notes")));
                }
            }
            WorkbenchShell.ManagementPanels.AgentAdaptersList.SelectedValue = SelectExistingId(previousAdapter, _agentAdapters.Select(item => item.AdapterId))
                ?? _agentAdapters.FirstOrDefault()?.AdapterId;
            RenderSelectedAgentAdapterEditor();
            RenderSelectedAgentEditor();

            var previousKnowledgeBase = WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue as string;
            _knowledgeBases.Clear();
            if (state.TryGetProperty("knowledge_bases", out var knowledgeBases) && knowledgeBases.ValueKind == JsonValueKind.Array)
            {
                foreach (var knowledge in knowledgeBases.EnumerateArray())
                {
                    _knowledgeBases.Add(new KnowledgeBaseViewModel(
                        ReadJsonString(knowledge, "knowledge_base_id"),
                        ReadJsonString(knowledge, "label"),
                        ReadJsonString(knowledge, "owner_agent_id"),
                        ReadJsonString(knowledge, "domain"),
                        ReadJsonString(knowledge, "path"),
                        ReadJsonString(knowledge, "shared_scope"),
                        ReadJsonBool(knowledge, "enabled", true),
                        ReadJsonString(knowledge, "notes"),
                        ReadJsonInt(knowledge, "file_count"),
                        knowledge.TryGetProperty("last_index", out var lastIndex) && lastIndex.ValueKind == JsonValueKind.Object ? ReadJsonString(lastIndex, "updated_at") : ""));
                }
            }
            WorkbenchShell.ManagementPanels.KnowledgeBasesList.SelectedValue = SelectExistingId(previousKnowledgeBase, _knowledgeBases.Select(item => item.KnowledgeBaseId))
                ?? _knowledgeBases.FirstOrDefault()?.KnowledgeBaseId;
            WorkbenchShell.ManagementPanels.KnowledgeSourceTargetBox.ItemsSource = _knowledgeBases;
            RenderSelectedKnowledgeBaseEditor();
            RenderKnowledgeSources(state);

            var activeRouteId = ReadJsonString(state, "active_route_profile_id");
            var previousProfile = WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue as string;
            _routeProfiles.Clear();
            if (state.TryGetProperty("route_profiles", out var profiles) && profiles.ValueKind == JsonValueKind.Array)
            {
                foreach (var profile in profiles.EnumerateArray())
                {
                    _routeProfiles.Add(new RouteProfileViewModel(
                        ReadJsonString(profile, "profile_id"),
                        ReadJsonString(profile, "label"),
                        ReadJsonString(profile, "strategy"),
                        ReadJsonBool(profile, "enabled", true),
                        profile.TryGetProperty("members", out var members) ? FormatJson(members) : "[]",
                        ReadJsonString(profile, "notes")));
                }
            }
            WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue = SelectExistingId(previousProfile, _routeProfiles.Select(item => item.ProfileId))
                ?? SelectExistingId(activeRouteId, _routeProfiles.Select(item => item.ProfileId))
                ?? _routeProfiles.FirstOrDefault()?.ProfileId;
            RenderSelectedRouteProfileEditor();

            var previousRemoteTarget = WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue as string;
            _remoteTargets.Clear();
            if (state.TryGetProperty("remote_targets", out var targets) && targets.ValueKind == JsonValueKind.Array)
            {
                foreach (var target in targets.EnumerateArray())
                {
                    _remoteTargets.Add(new RemoteTargetViewModel(
                        ReadJsonString(target, "target_id"),
                        ReadJsonString(target, "label"),
                        ReadJsonString(target, "base_url"),
                        ReadJsonBool(target, "enabled", false),
                        ReadJsonBool(target, "token_set", false),
                        ReadJsonStringArray(target, "capabilities")));
                }
            }
            WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue = SelectExistingId(previousRemoteTarget, _remoteTargets.Select(item => item.TargetId))
                ?? _remoteTargets.FirstOrDefault()?.TargetId;
            RenderSelectedRemoteTargetEditor();

            _agentRecommendations.Clear();
            if (state.TryGetProperty("recommended_improvements", out var recommendations) && recommendations.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in recommendations.EnumerateArray())
                {
                    _agentRecommendations.Add(new EventViewModel(
                        $"{UiDisplayText.Priority(ReadJsonString(item, "priority"))} · {ReadJsonString(item, "title")}",
                        ReadJsonString(item, "detail")));
                }
            }
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"Skill 协作：{UiDisplayText.SkillAssistMode(ReadJsonString(assist, "mode"))} · Agent {_agents.Count} · Adapter {_agentAdapters.Count} · 外部助手 {_externalAssistants.Count} · 路由组合 {_routeProfiles.Count} · 远端目标 {_remoteTargets.Count}";
            if (state.TryGetProperty("distribution_summary", out var distributionSummary) && distributionSummary.ValueKind == JsonValueKind.Object)
            {
                AddAgentDistributionGaps(distributionSummary);
                WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = BuildAgentDistributionStatusSummary(distributionSummary, assist);
            }
        }
        finally
        {
            SetRendering(false);
        }
    }

    internal void AddAgentDistributionGaps(JsonElement distributionSummary)
    {
        if (!distributionSummary.TryGetProperty("gaps", out var gaps) || gaps.ValueKind != JsonValueKind.Array)
        {
            return;
        }
        foreach (var gap in gaps.EnumerateArray())
        {
            if (gap.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            _agentRecommendations.Insert(0, new EventViewModel(
                $"分布缺口 · {UiDisplayText.Priority(ReadJsonString(gap, "priority", "medium"))} · {ReadJsonString(gap, "title")}",
                ReadJsonString(gap, "detail")));
        }
    }

    internal static string BuildAgentDistributionStatusSummary(JsonElement distributionSummary, JsonElement skillAssist)
    {
        TryReadJsonObject(distributionSummary, "counts", out var counts);
        TryReadJsonObject(distributionSummary, "active_route", out var activeRoute);
        TryReadJsonObject(activeRoute, "primary_text", out var primaryText);
        TryReadJsonObject(distributionSummary, "remote_distribution", out var remoteDistribution);
        var providers = ReadProviderSummaries(distributionSummary, 3);
        var activeRouteLine = $"{UiDisplayText.ShortTechnical(ReadSafeJsonString(activeRoute, "profile_id", "--"))} · {UiDisplayText.RouteStrategy(ReadSafeJsonString(activeRoute, "strategy", "--"))}";
        var primaryLine = $"{UiDisplayText.Domain(ReadSafeJsonString(primaryText, "member_id", "--"))} · {UiDisplayText.Provider(ReadSafeJsonString(primaryText, "provider", "--"))} / {UiDisplayText.ShortTechnical(ReadSafeJsonString(primaryText, "model", "--"))}";
        var gaps = ReadSummaryStepTitles(distributionSummary, "gaps", 2);
        return string.Join(Environment.NewLine, new[]
        {
            $"Agent 分布：{UiDisplayText.Status(ReadJsonString(distributionSummary, "status", "--"))} · Skill 协作 {UiDisplayText.SkillAssistMode(ReadJsonString(skillAssist, "mode", "--"))}",
            $"Agent：启用 {ReadSafeJsonInt(counts, "agents_enabled")} / 总计 {ReadSafeJsonInt(counts, "agents_total")} · 外部助手 {ReadSafeJsonInt(counts, "external_assistants_enabled")} · 远端 {ReadSafeJsonInt(remoteDistribution, "targets_enabled")}/{ReadSafeJsonInt(remoteDistribution, "targets_total")}",
            $"当前路由: {activeRouteLine} · 主文本 {primaryLine}",
            $"接入方式: {(providers.Length == 0 ? "--" : string.Join("；", providers))}",
            $"缺口: {(gaps.Length == 0 ? "无" : string.Join("；", gaps))}",
        });
    }

    internal static string[] ReadProviderSummaries(JsonElement distributionSummary, int limit)
    {
        if (!distributionSummary.TryGetProperty("providers", out var providers) || providers.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return providers
            .EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.Object)
            .Take(limit)
            .Select(item => $"{UiDisplayText.Provider(ReadJsonString(item, "provider", "unconfigured"))} {ReadJsonInt(item, "enabled_agent_count")}")
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

}
