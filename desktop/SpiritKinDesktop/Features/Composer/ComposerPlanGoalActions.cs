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
    internal void TogglePlanMode()
    {
        var enabled = !GetSettingBool(PlanModeSetting);
        SetSetting(PlanModeSetting, enabled);
        RenderComposerSelectorText(ActiveSession());
        RenderComposerAttachmentStatus();
        WorkspaceSidebar.ConnectionStatusText.Text = enabled ? "Plan mode 已开启：发送后只生成方案，不执行动作。" : "Plan mode 已关闭。";
        _ = SaveStateAsync();
    }

    internal void ClearPlanMode()
    {
        SetSetting(PlanModeSetting, false);
        SetSetting(PlanSummarySetting, "");
        RenderComposerSelectorText(ActiveSession());
        RenderComposerAttachmentStatus();
        WorkspaceSidebar.ConnectionStatusText.Text = "已清空 Plan mode。";
        _ = SaveStateAsync();
    }

    internal void TogglePursueGoal()
    {
        var enabled = !GetSettingBool(PursueGoalSetting);
        SetSetting(PursueGoalSetting, enabled);
        if (!enabled)
        {
            SetSetting(PursueGoalTextSetting, "");
            WorkspaceSidebar.ConnectionStatusText.Text = "Pursue goal 已关闭。";
        }
        else
        {
            var goal = ResolvePursueGoalText();
            if (!string.IsNullOrWhiteSpace(goal))
            {
                SetSetting(PursueGoalTextSetting, goal);
            }
            WorkspaceSidebar.ConnectionStatusText.Text = "Pursue goal 已开启：下一次发送会作为持续目标推进。";
        }
        RenderComposerSelectorText(ActiveSession());
        RenderComposerAttachmentStatus();
        _ = SaveStateAsync();
    }

    internal void ClearPursueGoal()
    {
        SetSetting(PursueGoalSetting, false);
        SetSetting(PursueGoalTextSetting, "");
        SetSetting(PursueGoalStatusSetting, "");
        SetSetting(PursueGoalProgressSetting, "");
        SetSetting(PursueGoalNextActionSetting, "");
        SetSetting(PursueGoalTurnCountSetting, "");
        RenderComposerSelectorText(ActiveSession());
        RenderComposerAttachmentStatus();
        WorkspaceSidebar.ConnectionStatusText.Text = "已清空持续目标。";
        _ = SaveStateAsync();
    }

    internal string ResolvePursueGoalText(string currentText = "")
    {
        var saved = GetSettingString(PursueGoalTextSetting);
        if (!string.IsNullOrWhiteSpace(saved))
        {
            return saved.Trim();
        }
        var candidate = string.IsNullOrWhiteSpace(currentText) ? ChatWorkspace.PromptBox.Text.Trim() : currentText.Trim();
        if (!string.IsNullOrWhiteSpace(candidate))
        {
            return candidate;
        }
        var active = ActiveSession();
        if (!string.IsNullOrWhiteSpace(active.Title) && !active.Title.StartsWith("会话 ", StringComparison.OrdinalIgnoreCase))
        {
            return active.Title.Trim();
        }
        return "";
    }

}


