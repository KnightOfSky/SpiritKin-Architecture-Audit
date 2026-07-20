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
    internal void OpenComposerPlusMenu(Control placementTarget)
    {
        var menu = new ContextMenu { PlacementTarget = placementTarget, Placement = PlacementMode.Bottom };
        AddContextMenuItem(menu, "Add photos & files", async (_, _) => await AddComposerFilesAsync());
        var planEnabled = GetSettingBool(PlanModeSetting);
        AddContextMenuItem(menu, $"{(planEnabled ? "✓ " : "")}Plan mode", (_, _) => TogglePlanMode());
        var pursueEnabled = GetSettingBool(PursueGoalSetting);
        AddContextMenuItem(menu, $"{(pursueEnabled ? "✓ " : "")}Pursue goal", (_, _) => TogglePursueGoal());
        if (planEnabled)
        {
            menu.Items.Add(CreateStyledSeparator());
            AddContextMenuItem(menu, "Clear plan", (_, _) => ClearPlanMode());
        }
        if (pursueEnabled)
        {
            if (!planEnabled)
            {
                menu.Items.Add(CreateStyledSeparator());
            }
            AddContextMenuItem(menu, "Clear goal", (_, _) => ClearPursueGoal());
        }
        if (_pendingAttachments.Count > 0)
        {
            menu.Items.Add(CreateStyledSeparator());
            AddDisabledMenuHeader(menu, $"Attached: {_pendingAttachments.Count} file{(_pendingAttachments.Count == 1 ? "" : "s")}");
            AddContextMenuItem(menu, "Clear attachments", (_, _) => ClearComposerAttachments());
        }
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void OpenAgentMentionMenu(Control placementTarget, TextBox target)
    {
        var menu = new ContextMenu { PlacementTarget = placementTarget, Placement = PlacementMode.Bottom };
        var enabledAgents = _agents()
            .Where(agent => agent.Enabled)
            .OrderByDescending(agent => string.Equals(agent.AgentId, "programming", StringComparison.OrdinalIgnoreCase))
            .ThenBy(agent => agent.Priority)
            .ThenBy(agent => agent.Label, StringComparer.CurrentCultureIgnoreCase)
            .ToList();
        if (enabledAgents.Count == 0)
        {
            AddDisabledMenuHeader(menu, "没有启用的 Agent");
        }
        else
        {
            AddDisabledMenuHeader(menu, "@ Agent 路由");
            foreach (var agent in enabledAgents.Take(12))
            {
                var header = $"@{agent.AgentId}  {agent.Label} · {UiDisplayText.Domain(agent.Domain)}";
                AddContextMenuItem(menu, header, (_, _) => InsertAgentMention(target, agent, statusQuery: false));
            }
            menu.Items.Add(CreateStyledSeparator());
            AddDisabledMenuHeader(menu, "查询工作情况");
            foreach (var agent in enabledAgents.Take(8))
            {
                AddContextMenuItem(menu, $"@{agent.AgentId} 当前工作情况", (_, _) => InsertAgentMention(target, agent, statusQuery: true));
            }
        }
        menu.Items.Add(CreateStyledSeparator());
        AddContextMenuItem(menu, "打开 Agent 管理", (_, _) => OpenManagementPage("agents", "agents"));
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal void OpenCollaborationMentionMenu(Control placementTarget, TextBox target)
    {
        var menu = new ContextMenu { PlacementTarget = placementTarget, Placement = PlacementMode.Bottom };
        var participants = _collaborationParticipantOptions()
            .Where(item => item.CanChat && !string.Equals(item.Kind, "worker", StringComparison.OrdinalIgnoreCase))
            .OrderBy(item => CollaborationParticipantSortKey(item.Kind))
            .ThenBy(item => item.Label, StringComparer.CurrentCultureIgnoreCase)
            .Take(18)
            .ToList();
        AddDisabledMenuHeader(menu, "@ 协作参与者");
        if (participants.Count == 0)
        {
            AddContextMenuItem(menu, "@Codex  Codex Agent", (_, _) => InsertCollaborationMention(target, "@Codex "));
            AddContextMenuItem(menu, "@ClaudeCode  Claude Code Agent", (_, _) => InsertCollaborationMention(target, "@ClaudeCode "));
            AddContextMenuItem(menu, "@GPT  已配置云端模型", (_, _) => InsertCollaborationMention(target, "@GPT "));
        }
        else
        {
            foreach (var participant in participants)
            {
                var mention = string.IsNullOrWhiteSpace(participant.Mention) ? $"@{participant.ParticipantId}" : participant.Mention;
                var status = string.IsNullOrWhiteSpace(participant.Status) ? "unknown" : participant.Status;
                AddContextMenuItem(menu, $"{mention}  {participant.Label} · {CollaborationParticipantKindLabel(participant.Kind)} · {status}", (_, _) => InsertCollaborationMention(target, $"{mention} "));
            }
        }
        menu.Items.Add(CreateStyledSeparator());
        ApplyMenuStyle(menu);
        menu.IsOpen = true;
    }

    internal static int CollaborationParticipantSortKey(string kind)
    {
        return (kind ?? "").Trim().ToLowerInvariant() switch
        {
            "external_cli" => 0,
            "model_api" => 1,
            "local_agent" => 2,
            _ => 9,
        };
    }

    internal static string CollaborationParticipantKindLabel(string kind)
    {
        return (kind ?? "").Trim().ToLowerInvariant() switch
        {
            "external_cli" => "外部 Agent",
            "model_api" => "模型",
            "local_agent" => "本地 Agent",
            "worker" => "执行器",
            _ => "参与者",
        };
    }

    internal void InsertCollaborationMention(TextBox target, string mention)
    {
        target.Focus();
        var index = Math.Clamp(target.CaretIndex, 0, target.Text.Length);
        if (index > 0 && target.Text[index - 1] == '@' && mention.StartsWith("@", StringComparison.Ordinal))
        {
            target.SelectionStart = index - 1;
            target.SelectionLength = 1;
            target.SelectedText = mention;
            target.CaretIndex = index - 1 + mention.Length;
        }
        else
        {
            var prefix = index > 0 && !char.IsWhiteSpace(target.Text[index - 1]) ? " " : "";
            var suffix = index < target.Text.Length && !char.IsWhiteSpace(target.Text[index]) ? " " : "";
            target.SelectedText = prefix + mention + suffix;
            target.CaretIndex = index + prefix.Length + mention.Length;
        }
        _lastCollaborationMentionTriggerIndex = -1;
        RenderComposerAttachmentStatus();
    }

    internal void InsertAgentMention(TextBox target, AgentViewModel agent, bool statusQuery)
    {
        var mention = statusQuery ? $"@{agent.AgentId} 当前工作情况" : $"@{agent.AgentId} ";
        target.Focus();
        if (statusQuery)
        {
            var selectedText = target.SelectedText;
            if (string.IsNullOrWhiteSpace(target.Text) || target.SelectionLength == target.Text.Length || string.IsNullOrWhiteSpace(selectedText))
            {
                target.Text = mention;
                target.CaretIndex = target.Text.Length;
            }
            else
            {
                target.SelectedText = mention;
                target.CaretIndex = target.SelectionStart + mention.Length;
            }
        }
        else
        {
            var index = Math.Clamp(target.CaretIndex, 0, target.Text.Length);
            var prefix = index > 0 && !char.IsWhiteSpace(target.Text[index - 1]) ? " " : "";
            var suffix = index < target.Text.Length && !char.IsWhiteSpace(target.Text[index]) ? " " : "";
            target.SelectedText = prefix + mention + suffix;
            target.CaretIndex = index + prefix.Length + mention.Length;
        }
        RenderComposerAttachmentStatus();
    }

}
