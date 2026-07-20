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
    internal bool RenderAgentMentionStatus()
    {
        // @提及预览改为输入框上方的悬浮气泡：只在焦点停留时展示，失焦由视图收起。
        var mainVisible = UpdateAgentMentionStatus(ChatWorkspace.AgentMentionStatusText, ChatWorkspace.PromptBox.Text);
        ChatWorkspace.AgentMentionPopup.IsOpen = mainVisible && ChatWorkspace.PromptBox.IsKeyboardFocusWithin;
        var emptyVisible = UpdateAgentMentionStatus(ChatWorkspace.EmptyAgentMentionStatusText, ChatWorkspace.EmptyPromptBox.Text);
        ChatWorkspace.EmptyAgentMentionPopup.IsOpen = emptyVisible && ChatWorkspace.EmptyPromptBox.IsKeyboardFocusWithin;
        return mainVisible || emptyVisible;
    }

    internal bool UpdateAgentMentionStatus(TextBlock target, string text)
    {
        var agent = ResolveMentionedAgent(text);
        if (agent is null)
        {
            target.Text = "";
            target.Visibility = Visibility.Collapsed;
            return false;
        }
        var action = agent.Enabled
            ? IsAgentMentionStatusQuery(text) ? "状态查询" : "强制路由"
            : "Agent 已关闭";
        var model = string.IsNullOrWhiteSpace(agent.Model) ? "模型未配置" : UiDisplayText.ShortTechnical(agent.Model, 18);
        target.Text = $"@{agent.AgentId} · {agent.Label} · {action} · {model}";
        target.Visibility = Visibility.Visible;
        return true;
    }

    internal AgentViewModel? ResolveMentionedAgent(string text)
    {
        var agents = _agents().ToList();
        if (string.IsNullOrWhiteSpace(text) || agents.Count == 0)
        {
            return null;
        }
        foreach (Match match in Regex.Matches(text, @"(?<![\w./-])@(?<name>[A-Za-z0-9_.\-\u4e00-\u9fff]{1,64})"))
        {
            var normalized = NormalizeAgentMentionKey(match.Groups["name"].Value);
            if (string.IsNullOrWhiteSpace(normalized))
            {
                continue;
            }
            var agent = agents.FirstOrDefault(item => AgentMentionMatches(item, normalized));
            if (agent is not null)
            {
                return agent;
            }
        }
        return null;
    }

    internal static bool AgentMentionMatches(AgentViewModel agent, string normalized)
    {
        if (NormalizeAgentMentionKey(agent.AgentId) == normalized
            || NormalizeAgentMentionKey(agent.Label) == normalized
            || NormalizeAgentMentionKey(agent.Label.Replace("Agent", "", StringComparison.OrdinalIgnoreCase)) == normalized)
        {
            return true;
        }
        var aliasTarget = ResolveAgentAliasTarget(normalized);
        return !string.IsNullOrWhiteSpace(aliasTarget)
            && string.Equals(agent.AgentId, aliasTarget, StringComparison.OrdinalIgnoreCase);
    }

    internal static string NormalizeAgentMentionKey(string value)
    {
        return Regex.Replace((value ?? "").Trim().ToLowerInvariant(), @"[\s_\-.]+", "");
    }

    internal static string ResolveAgentAliasTarget(string normalized)
    {
        return normalized switch
        {
            "主" or "主agent" or "总调度" or "总agent" or "main" or "mainagent" or "maintext" or "spirit" => "main_text",
            "编程" or "代码" or "code" or "coding" or "programming" => "programming",
            "视觉" or "vision" or "visionmodel" => "vision_model",
            "视频" or "动画" or "video" or "animation" or "videoanimation" => "video_animation",
            "游戏" or "game" or "gamedev" or "gamedevelopment" => "game_development",
            "电商" or "commerce" or "ecommerce" => "ecommerce",
            "技能" or "skill" or "skills" or "skillrunner" => "skill_runner",
            "评审" or "审查" or "review" or "reviewer" or "externalreviewer" => "external_reviewer",
            _ => "",
        };
    }

    internal static bool IsAgentMentionStatusQuery(string text)
    {
        var normalized = Regex.Replace(text ?? "", @"\s+", "").ToLowerInvariant();
        return normalized.Contains("当前工作情况", StringComparison.Ordinal)
            || normalized.Contains("工作情况", StringComparison.Ordinal)
            || normalized.Contains("状态", StringComparison.Ordinal)
            || normalized.Contains("进度", StringComparison.Ordinal)
            || normalized.Contains("队列", StringComparison.Ordinal)
            || normalized.Contains("status", StringComparison.Ordinal)
            || normalized.Contains("progress", StringComparison.Ordinal);
    }

    internal static string TrimStatusText(string value, int maxLength)
    {
        var normalized = string.Join(" ", value.Split(default(string[]), StringSplitOptions.RemoveEmptyEntries));
        return normalized.Length <= maxLength ? normalized : normalized[..Math.Max(0, maxLength - 3)] + "...";
    }

}

