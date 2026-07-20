using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class ComposerController
{
    internal void ToggleCollaborationComposerMode()
    {
        SetCollaborationComposerMode(!CollaborationChatActive);
    }

    internal void ToggleWebSearchComposerMode()
    {
        SetSetting(WebSearchModeSetting, !GetSettingBool(WebSearchModeSetting));
        RenderComposerModeButtonStates();
        _ = SaveStateAsync();
    }

    internal void SetCollaborationComposerMode(bool enabled, bool persist = true)
    {
        if (enabled)
        {
            EnableCollaborationComposerMode(projectMessages: false);
            return;
        }

        if (!CollaborationChatActive)
        {
            RenderComposerModeButtonStates();
            return;
        }

        SetCollaborationChatActive(false);
        SetSetting(CollaborationModeSetting, false);
        _clearCollaborationChatSignature();
        RenderComposerModeButtonStates();
        FocusVisibleComposer();
        if (persist)
        {
            _ = SaveStateAsync();
        }
    }

    internal void EnableCollaborationComposerMode(bool projectMessages)
    {
        var quickChatComposerVisible = ChatWorkspace.QuickChatEmptyPanel.Visibility == Visibility.Visible;
        SetCollaborationChatActive(true);
        SetSetting(CollaborationModeSetting, true);
        if (!quickChatComposerVisible)
        {
            BindCurrentSessionCollaborationThread();
        }
        _clearCollaborationChatSignature();
        RenderComposerModeButtonStates();
        if (projectMessages)
        {
            RenderCollaborationComposerModeOnly();
        }
        FocusVisibleComposer();
        _ = SaveStateAsync();
    }

    internal void EnableCollaborationRoutingForAddressedMessage()
    {
        if (!CollaborationChatActive)
        {
            EnableCollaborationComposerMode(projectMessages: false);
        }
        else
        {
            BindCurrentSessionCollaborationThread();
            RenderComposerModeButtonStates();
            ChatWorkspace.PromptBox.Focus();
        }
    }

    internal void RenderCollaborationComposerModeOnly()
    {
        if (CollaborationChatActive)
        {
            var projected = ProjectCollaborationMessagesIntoActiveSessionFromCache();
            if (projected)
            {
                RenderState();
                _ = SaveStateAsync();
                return;
            }
        }
        var active = ActiveSession();
        RenderActiveMessages(active);
        SyncQuickChatLayout(active);
    }

    internal string BindCurrentSessionCollaborationThread()
    {
        var threadId = CurrentSessionCollaborationThreadId();
        SetActiveCollaborationThreadId(threadId);
        WorkbenchShell.ManagementPanels.CollaborationTaskIdBox.Text = threadId;
        ClearCollaborationSignatures();
        return threadId;
    }

    internal void RenderComposerModeButtonStates()
    {
        ChatWorkspace.AgentMentionButton.Visibility = Visibility.Visible;
        ChatWorkspace.EmptyAgentMentionButton.Visibility = Visibility.Visible;

        ChatWorkspace.ChatDuplexToggle.Visibility = CollaborationChatActive ? Visibility.Visible : Visibility.Collapsed;
        ChatWorkspace.ChatDuplexBalanceText.Visibility = CollaborationChatActive ? Visibility.Visible : Visibility.Collapsed;

        ApplyComposerToggleButtonState(
            ChatWorkspace.CollaborationModeButton,
            CollaborationChatActive,
            "模型协作",
            "模型协作已开启，点击切回普通对话",
            "开启模型协作对话");
        ApplyComposerToggleButtonState(
            ChatWorkspace.EmptyCollaborationModeButton,
            CollaborationChatActive,
            "模型协作",
            "模型协作已开启，点击切回普通对话",
            "开启模型协作对话");

        var webSearchEnabled = GetSettingBool(WebSearchModeSetting);
        ChatWorkspace.WebSearchModeButton.Visibility = Visibility.Visible;
        ChatWorkspace.EmptyWebSearchModeButton.Visibility = Visibility.Visible;
        ApplyComposerToggleButtonState(
            ChatWorkspace.WebSearchModeButton,
            webSearchEnabled,
            "联网检索",
            "联网检索已开启，点击关闭",
            "开启联网检索");
        ApplyComposerToggleButtonState(
            ChatWorkspace.EmptyWebSearchModeButton,
            webSearchEnabled,
            "联网检索",
            "联网检索已开启，点击关闭",
            "开启联网检索");
    }

    internal static void ApplyComposerToggleButtonState(Button button, bool active, string label, string activeToolTip, string inactiveToolTip)
    {
        // Active state is already conveyed by color and border. Keeping the label
        // stable prevents two toggles from pushing the send controls off-screen.
        var visualState = ResolveComposerToggleVisualState(active, label, activeToolTip, inactiveToolTip);
        button.Content = visualState.Content;
        button.ToolTip = visualState.ToolTip;
        button.SetResourceReference(Control.ForegroundProperty, active ? "FantasyPrimaryBrush" : "FantasyFaintTextBrush");
        if (active)
        {
            button.SetResourceReference(Control.BackgroundProperty, "FantasyGoldWashBrush");
            button.SetResourceReference(Control.BorderBrushProperty, "FantasyPrimaryBrush");
            button.BorderThickness = new Thickness(1);
            button.Padding = new Thickness(6, 0, 6, 0);
            return;
        }
        button.Background = Brushes.Transparent;
        button.BorderBrush = Brushes.Transparent;
        button.BorderThickness = new Thickness(0);
        button.Padding = new Thickness(2, 0, 2, 0);
    }

    internal static (string Content, string ToolTip) ResolveComposerToggleVisualState(
        bool active,
        string label,
        string activeToolTip,
        string inactiveToolTip)
    {
        return (label, active ? activeToolTip : inactiveToolTip);
    }

    private void FocusVisibleComposer()
    {
        var prompt = ChatWorkspace.QuickChatEmptyPanel.Visibility == Visibility.Visible
            ? ChatWorkspace.EmptyPromptBox
            : ChatWorkspace.PromptBox;
        prompt.Focus();
        prompt.CaretIndex = prompt.Text.Length;
    }
}
