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
    internal void NewRouteProfile()
    {
        var profile = new RouteProfileViewModel(
            UniqueId("route", _routeProfiles.Select(item => item.ProfileId)),
            "新路由组合",
            "primary_with_specialists",
            true,
            BuildDefaultRouteMembersJson(),
            "");
        _routeProfiles.Add(profile);
        WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue = profile.ProfileId;
        RenderSelectedRouteProfileEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已新增路由组合：{profile.ProfileId}。";
    }

    internal bool ApplyRouteProfileOrShowError(bool showMessage)
    {
        if (!TryNormalizeRouteMembersJson(out var membersJson))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "成员配置 JSON 格式错误。";
            return false;
        }
        return ApplySelectedRouteProfileFromEditor(membersJson, showMessage);
    }

    internal bool ApplySelectedRouteProfileFromEditor(string membersJson, bool showMessage)
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _routeProfiles.Count
            ? _routeProfiles[selectedIndex].ProfileId
            : UniqueId("route", _routeProfiles.Select(item => item.ProfileId));
        var updated = new RouteProfileViewModel(
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RouteProfileIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.RouteProfileIdBox.Text.Trim(),
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RouteProfileLabelBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.RouteProfileLabelBox.Text.Trim(),
            string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.RouteStrategyBox)) ? "primary_with_specialists" : ComboText(WorkbenchShell.ManagementPanels.RouteStrategyBox),
            WorkbenchShell.ManagementPanels.RouteProfileEnabledBox.IsChecked == true,
            membersJson,
            WorkbenchShell.ManagementPanels.RouteProfileNotesBox.Text.Trim());
        if (_routeProfiles.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.ProfileId, updated.ProfileId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"组合 ID 已存在：{updated.ProfileId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _routeProfiles.Count)
            {
                _routeProfiles[selectedIndex] = updated;
            }
            else
            {
                _routeProfiles.Add(updated);
            }
            WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue = updated.ProfileId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用路由组合修改：{updated.ProfileId}";
        }
        return true;
    }

    internal void DeleteSelectedRouteProfile()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _routeProfiles.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的路由组合。";
            return;
        }
        if (_routeProfiles.Count <= 1)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "至少保留一个路由组合。";
            return;
        }
        var removed = _routeProfiles[selectedIndex];
        if (!ConfirmDestructiveAction("删除路由组合", $"确定要删除路由组合“{removed.ProfileId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _routeProfiles.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.RouteProfilesList.SelectedValue = _routeProfiles[Math.Min(selectedIndex, _routeProfiles.Count - 1)].ProfileId;
        RenderSelectedRouteProfileEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已删除路由组合：{removed.ProfileId}。保存集群配置后生效。";
    }

    internal void NewRemoteTarget()
    {
        var target = new RemoteTargetViewModel(
            UniqueId("remote", _remoteTargets.Select(item => item.TargetId)),
            "新远端目标",
            $"http://127.0.0.1:{_remoteWorkerPort}",
            false,
            false,
            new[] { "skill_control" });
        _remoteTargets.Add(target);
        WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue = target.TargetId;
        RenderSelectedRemoteTargetEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已新增远端目标：{target.TargetId}。";
    }

    internal bool ApplySelectedRemoteTargetFromEditor(bool showMessage)
    {
        if (_remoteTargets.Count == 0 && !RemoteTargetEditorHasContent())
        {
            return true;
        }
        var selectedIndex = WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedIndex;
        var fallbackId = selectedIndex >= 0 && selectedIndex < _remoteTargets.Count
            ? _remoteTargets[selectedIndex].TargetId
            : UniqueId("remote", _remoteTargets.Select(item => item.TargetId));
        var updated = BuildRemoteTargetFromEditor(fallbackId);
        if (_remoteTargets.Where((_, index) => index != selectedIndex).Any(item => string.Equals(item.TargetId, updated.TargetId, StringComparison.OrdinalIgnoreCase)))
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"远端目标 ID 已存在：{updated.TargetId}";
            return false;
        }
        SetRendering(true);
        try
        {
            if (selectedIndex >= 0 && selectedIndex < _remoteTargets.Count)
            {
                _remoteTargets[selectedIndex] = updated;
            }
            else
            {
                _remoteTargets.Add(updated);
            }
            WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue = updated.TargetId;
            WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text = updated.TargetId;
        }
        finally
        {
            SetRendering(false);
        }
        if (showMessage)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已应用远端目标修改：{updated.TargetId}";
        }
        return true;
    }

    internal RemoteTargetViewModel BuildRemoteTargetFromEditor(string fallbackId)
    {
        var targetId = string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text) ? fallbackId : WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text.Trim();
        return new RemoteTargetViewModel(
            targetId,
            string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetLabelBox.Text) ? targetId : WorkbenchShell.ManagementPanels.RemoteTargetLabelBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Text.Trim(),
            WorkbenchShell.ManagementPanels.RemoteTargetEnabledBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.RemoteTargetTokenSetBox.IsChecked == true,
            WorkbenchShell.ManagementPanels.RemoteTargetCapabilitiesBox.Text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
    }

    internal void DeleteSelectedRemoteTarget()
    {
        var selectedIndex = WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedIndex;
        if (selectedIndex < 0 || selectedIndex >= _remoteTargets.Count)
        {
            WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = "请先选择要删除的远端目标。";
            return;
        }
        var removed = _remoteTargets[selectedIndex];
        if (!ConfirmDestructiveAction("删除远端目标", $"确定要删除远端目标“{removed.TargetId}”吗？保存集群配置后生效。"))
        {
            return;
        }
        _remoteTargets.RemoveAt(selectedIndex);
        WorkbenchShell.ManagementPanels.RemoteTargetsList.SelectedValue = _remoteTargets.Count == 0 ? null : _remoteTargets[Math.Min(selectedIndex, _remoteTargets.Count - 1)].TargetId;
        RenderSelectedRemoteTargetEditor();
        WorkbenchShell.ManagementPanels.AgentManagementSummaryText.Text = $"已删除远端目标：{removed.TargetId}。保存集群配置后生效。";
    }

    internal List<Dictionary<string, object?>> BuildAgentsPayload()
    {
        var list = new List<Dictionary<string, object?>>();
        for (var index = 0; index < _agents.Count; index++)
        {
            var agent = _agents[index];
            if (string.IsNullOrWhiteSpace(agent.KnowledgeBaseId) || string.IsNullOrWhiteSpace(agent.KnowledgeBasePath))
            {
                agent = agent.WithKnowledgeBase(
                    string.IsNullOrWhiteSpace(agent.KnowledgeBaseId) ? $"kb_{agent.AgentId}" : agent.KnowledgeBaseId,
                    string.IsNullOrWhiteSpace(agent.KnowledgeBasePath) ? $"state/knowledge_bases/agents/{agent.AgentId}" : agent.KnowledgeBasePath);
                _agents[index] = agent;
            }
            EnsureKnowledgeBaseForAgent(agent);
            list.Add(new Dictionary<string, object?>
            {
                ["agent_id"] = agent.AgentId,
                ["label"] = agent.Label,
                ["domain"] = agent.Domain,
                ["role"] = agent.Role,
                ["provider"] = agent.Provider,
                ["model"] = agent.Model,
                ["model_id"] = agent.ModelId,
                ["framework"] = agent.Framework,
                ["adapter"] = agent.Adapter,
                ["enabled"] = agent.Enabled,
                ["priority"] = agent.Priority,
                ["capabilities"] = agent.Capabilities,
                ["allowed_assistant_ids"] = agent.AllowedAssistantIds,
                ["knowledge_base_id"] = agent.KnowledgeBaseId,
                ["knowledge_base_path"] = agent.KnowledgeBasePath,
                ["brain_profile"] = agent.BrainProfile,
                ["notes"] = agent.Notes,
            });
        }
        return list;
    }

    internal List<Dictionary<string, object?>> BuildExternalAssistantsPayload()
    {
        var list = new List<Dictionary<string, object?>>();
        foreach (var assistant in _externalAssistants)
        {
            list.Add(new Dictionary<string, object?>
            {
                ["assistant_id"] = assistant.AssistantId,
                ["label"] = assistant.Label,
                ["kind"] = assistant.Kind,
                ["command"] = assistant.Command,
                ["working_directory"] = assistant.WorkingDirectory,
                ["category"] = assistant.Category,
                ["enabled"] = assistant.Enabled,
                ["allow_write"] = assistant.AllowWrite,
                ["review_only"] = assistant.ReviewOnly,
            });
        }
        return list;
    }

    internal List<Dictionary<string, object?>> BuildAgentAdaptersPayload()
    {
        var list = new List<Dictionary<string, object?>>();
        foreach (var adapter in _agentAdapters)
        {
            list.Add(new Dictionary<string, object?>
            {
                ["adapter_id"] = adapter.AdapterId,
                ["label"] = adapter.Label,
                ["kind"] = adapter.Kind,
                ["framework"] = adapter.Framework,
                ["command"] = adapter.Command,
                ["module"] = adapter.Module,
                ["endpoint"] = adapter.Endpoint,
                ["working_directory"] = adapter.WorkingDirectory,
                ["enabled"] = adapter.Enabled,
                ["allow_write"] = adapter.AllowWrite,
                ["review_only"] = adapter.ReviewOnly,
                ["capabilities"] = adapter.Capabilities,
                ["owner_agent_ids"] = adapter.OwnerAgentIds,
                ["notes"] = adapter.Notes,
            });
        }
        return list;
    }

    internal List<Dictionary<string, object?>> BuildKnowledgeBasesPayload()
    {
        foreach (var agent in _agents)
        {
            EnsureKnowledgeBaseForAgent(agent);
        }
        var list = new List<Dictionary<string, object?>>();
        foreach (var knowledge in _knowledgeBases)
        {
            list.Add(new Dictionary<string, object?>
            {
                ["knowledge_base_id"] = knowledge.KnowledgeBaseId,
                ["label"] = knowledge.Label,
                ["owner_agent_id"] = knowledge.OwnerAgentId,
                ["domain"] = knowledge.Domain,
                ["path"] = knowledge.Path,
                ["shared_scope"] = knowledge.SharedScope,
                ["enabled"] = knowledge.Enabled,
                ["notes"] = knowledge.Notes,
            });
        }
        return list;
    }

    internal List<Dictionary<string, object?>> BuildRouteProfilesPayload()
    {
        var list = new List<Dictionary<string, object?>>();
        foreach (var profile in _routeProfiles)
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(profile.MembersJson) ? "[]" : profile.MembersJson);
            list.Add(new Dictionary<string, object?>
            {
                ["profile_id"] = profile.ProfileId,
                ["label"] = profile.Label,
                ["strategy"] = profile.Strategy,
                ["enabled"] = profile.Enabled,
                ["members"] = NormalizeRouteMembersForSavedAgents(doc.RootElement),
                ["notes"] = profile.Notes,
            });
        }
        return list;
    }

    internal List<Dictionary<string, object?>> NormalizeRouteMembersForSavedAgents(JsonElement membersElement)
    {
        var agentsById = _agents.ToDictionary(agent => agent.AgentId, StringComparer.OrdinalIgnoreCase);
        var members = new List<Dictionary<string, object?>>();
        if (membersElement.ValueKind != JsonValueKind.Array)
        {
            return members;
        }
        foreach (var member in membersElement.EnumerateArray())
        {
            if (member.ValueKind != JsonValueKind.Object)
            {
                continue;
            }
            var payload = JsonElementToDictionary(member);
            var memberId = ReadJsonString(member, "member_id");
            var role = ReadJsonString(member, "role");
            if (agentsById.TryGetValue(memberId, out var agent)
                || (!string.IsNullOrWhiteSpace(role) && _agents.FirstOrDefault(item => string.Equals(item.Role, role, StringComparison.OrdinalIgnoreCase)) is { } agentByRole && agentsById.TryGetValue(agentByRole.AgentId, out agent)))
            {
                payload["member_id"] = agent.AgentId;
                payload["role"] = string.IsNullOrWhiteSpace(agent.Role) ? payload.GetValueOrDefault("role") : agent.Role;
                payload["provider"] = agent.Provider;
                payload["model"] = agent.Model;
                payload["enabled"] = agent.Enabled;
                payload["capabilities"] = agent.Capabilities;
            }
            members.Add(payload);
        }
        return members;
    }

    internal List<Dictionary<string, object?>> BuildRemoteTargetsPayload()
    {
        var list = new List<Dictionary<string, object?>>();
        foreach (var target in _remoteTargets)
        {
            list.Add(new Dictionary<string, object?>
            {
                ["target_id"] = target.TargetId,
                ["label"] = target.Label,
                ["base_url"] = target.BaseUrl,
                ["enabled"] = target.Enabled,
                ["token_set"] = target.TokenSet,
                ["capabilities"] = target.Capabilities,
            });
        }
        return list;
    }

    internal bool ExternalAssistantEditorHasContent()
    {
        return !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantIdBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantLabelBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantCommandBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.ExternalAssistantWorkingDirectoryBox.Text);
    }

    internal bool AgentAdapterEditorHasContent()
    {
        return !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterIdBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterLabelBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterCommandBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterModuleBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterEndpointBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterWorkingDirectoryBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterCapabilitiesBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterOwnerAgentsBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.AgentAdapterNotesBox.Text);
    }

    internal bool KnowledgeBaseEditorHasContent()
    {
        return !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBaseIdBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBaseLabelBox.Text)
            || !string.IsNullOrWhiteSpace((WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.SelectedValue as string) ?? WorkbenchShell.ManagementPanels.KnowledgeBaseOwnerAgentBox.Text)
            || !string.IsNullOrWhiteSpace(ComboText(WorkbenchShell.ManagementPanels.KnowledgeBaseDomainBox))
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBasePathBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.KnowledgeBaseNotesBox.Text);
    }

    internal bool RemoteTargetEditorHasContent()
    {
        return !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetIdBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetLabelBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetBaseUrlBox.Text)
            || !string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RemoteTargetCapabilitiesBox.Text);
    }

    internal bool TryNormalizeRouteMembersJson(out string membersJson)
    {
        membersJson = "[]";
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(WorkbenchShell.ManagementPanels.RouteMembersJsonBox.Text) ? "[]" : WorkbenchShell.ManagementPanels.RouteMembersJsonBox.Text);
            if (doc.RootElement.ValueKind != JsonValueKind.Array)
            {
                return false;
            }
            membersJson = FormatJson(doc.RootElement);
            return true;
        }
        catch
        {
            return false;
        }
    }

    internal string BuildDefaultRouteMembersJson()
    {
        var members = _agents
            .Where(agent => agent.Enabled)
            .OrderByDescending(agent => agent.Priority)
            .Take(3)
            .Select(agent => new Dictionary<string, object?>
            {
                ["member_id"] = agent.AgentId,
                ["role"] = string.IsNullOrWhiteSpace(agent.Role) ? "specialist" : agent.Role,
                ["provider"] = agent.Provider,
                ["model"] = agent.Model,
                ["weight"] = agent.Role.Equals("primary", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.7,
                ["enabled"] = true,
                ["capabilities"] = agent.Capabilities,
            })
            .ToList();
        if (members.Count == 0)
        {
            return "[]";
        }
        return JsonSerializer.Serialize(members, new JsonSerializerOptions { WriteIndented = true });
    }

}
