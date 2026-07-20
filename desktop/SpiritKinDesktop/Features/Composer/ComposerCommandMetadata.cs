using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal void EnsureActiveSessionProject()
    {
        var active = ActiveSession();
        var projectId = GetSettingString(ProjectIdSetting);
        if (string.IsNullOrWhiteSpace(projectId)
            || active.ProjectId == projectId
            || string.IsNullOrWhiteSpace(active.ProjectId))
        {
            return;
        }
        if (State.Projects.Any(project => string.Equals(project.Id, projectId, StringComparison.OrdinalIgnoreCase)))
        {
            active.ProjectId = projectId;
            active.UpdatedAt = NowSeconds();
            _expandedProjectIds.Add(projectId);
        }
    }

    internal Dictionary<string, object?> BuildComposerCommandMetadata(bool steerConversation = false)
    {
        var project = ComposerProject();
        var runtime = ActiveProjectRuntimeProfile();
        var pursueGoal = GetSettingBool(PursueGoalSetting);
        var goalText = pursueGoal ? ResolvePursueGoalText() : "";
        var metadata = new Dictionary<string, object?>
        {
            ["frontend"] = "spiritkin_wpf_desktop",
            ["session_id"] = State.ActiveSessionId,
            ["collaboration_thread_id"] = CurrentSessionCollaborationThreadId(),
            ["project_id"] = project?.Id,
            ["project_title"] = project?.Title,
            ["workspace_path"] = runtime.WorkspacePath,
            ["dependency_file"] = runtime.DependencyFilePath,
            ["env_file"] = runtime.EnvFilePath,
            ["start_command"] = runtime.StartCommand,
            ["permission_mode"] = ComposerPermissionMode(),
            ["full_access_granted"] = GetSettingBool(FullAccessGrantedSetting),
            ["plan_mode"] = GetSettingBool(PlanModeSetting),
            ["web_search_enabled"] = GetSettingBool(WebSearchModeSetting),
            ["pursue_goal"] = pursueGoal,
            ["goal_text"] = goalText,
            ["model_id"] = GetSettingString(ModelIdSetting),
            ["model_display"] = GetSettingString(ModelDisplaySetting, "自动（主模型）"),
            ["model_provider"] = GetSettingString(ModelProviderSetting),
            ["model_name"] = GetSettingString(ModelNameSetting),
            ["model_source"] = GetSettingString(ModelSourceSetting, "runtime_route"),
            ["reasoning_effort"] = GetSettingString(ReasoningEffortSetting, "auto"),
            ["runtime_mode"] = GetSettingString(RuntimeModeSetting, "local_edge"),
            ["runtime_display"] = GetSettingString(RuntimeDisplaySetting, "Work locally"),
            ["runtime_target"] = "microsoft_edge_local",
            ["branch"] = GetSettingString(BranchSetting, CurrentGitBranch(refresh: false)),
            ["steer_conversation"] = steerConversation,
            ["input_mode"] = steerConversation ? "steer" : "send",
        };
        if (_pendingAttachments.Count > 0)
        {
            metadata["attachments"] = BuildPendingAttachmentPayload();
        }
        if (_pendingAttachmentDocuments.Count > 0)
        {
            metadata["documents"] = BuildPendingDocumentPayload();
        }
        return metadata;
    }

}
