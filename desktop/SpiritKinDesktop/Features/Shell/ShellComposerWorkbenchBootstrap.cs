using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private void RegisterComposerWorkbenchHandlers()
    {
        AddHandler(GotKeyboardFocusEvent, new KeyboardFocusChangedEventHandler(_shellInteractionController.TextEditTarget_GotKeyboardFocus), true);
        AddHandler(ContextMenuOpeningEvent, new ContextMenuEventHandler(_shellInteractionController.TextEditTarget_ContextMenuOpening), true);
        _shellInteractionController.InstallTextEditContextMenus(this);
        ChatWorkspace.PromptBox.TextChanged += _composerController.ComposerPrompt_TextChanged;
        ChatWorkspace.PromptBox.PreviewKeyDown += async (_, e) =>
        {
            if (ShellInteractionController.IsSubmitKey(e.Key) && (Keyboard.Modifiers & ModifierKeys.Control) == 0 && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
            {
                e.Handled = true;
                await _runtimeController.SendCommandAsync();
            }
            else if (ShellInteractionController.IsSubmitKey(e.Key) && (Keyboard.Modifiers & ModifierKeys.Control) != 0)
            {
                e.Handled = true;
                await _runtimeController.SendCommandAsync(steerConversation: true);
            }
        };
        ChatWorkspace.ClearPlanButton.Click += (_, _) => _composerController.ClearPlanMode();
        ChatWorkspace.EmptyClearPlanButton.Click += (_, _) => _composerController.ClearPlanMode();
        ChatWorkspace.ClearGoalButton.Click += (_, _) => _composerController.ClearPursueGoal();
        ChatWorkspace.EmptyClearGoalButton.Click += (_, _) => _composerController.ClearPursueGoal();
        ChatWorkspace.EmptyPromptBox.PreviewKeyDown += async (_, e) =>
        {
            if (ShellInteractionController.IsSubmitKey(e.Key) && (Keyboard.Modifiers & ModifierKeys.Control) == 0 && (Keyboard.Modifiers & ModifierKeys.Shift) == 0)
            {
                e.Handled = true;
                await _runtimeController.SendCommandAsync(ChatWorkspace.EmptyPromptBox.Text);
            }
            else if (ShellInteractionController.IsSubmitKey(e.Key) && (Keyboard.Modifiers & ModifierKeys.Control) != 0)
            {
                e.Handled = true;
                await _runtimeController.SendCommandAsync(ChatWorkspace.EmptyPromptBox.Text, steerConversation: true);
            }
        };
        ChatWorkspace.AttachButton.Click += (_, _) => _composerController.OpenComposerPlusMenu(ChatWorkspace.AttachButton);
        ChatWorkspace.EmptyAttachButton.Click += (_, _) => _composerController.OpenComposerPlusMenu(ChatWorkspace.EmptyAttachButton);
        ChatWorkspace.CollaborationModeButton.Click += (_, _) => _composerController.ToggleCollaborationComposerMode();
        ChatWorkspace.EmptyCollaborationModeButton.Click += (_, _) => _composerController.ToggleCollaborationComposerMode();
        ChatWorkspace.WebSearchModeButton.Click += (_, _) => _composerController.ToggleWebSearchComposerMode();
        ChatWorkspace.EmptyWebSearchModeButton.Click += (_, _) => _composerController.ToggleWebSearchComposerMode();
        ChatWorkspace.AgentMentionButton.Click += (_, _) =>
        {
            if (_composerController.CollaborationChatActive)
            {
                _composerController.OpenCollaborationMentionMenu(ChatWorkspace.AgentMentionButton, ChatWorkspace.PromptBox);
                return;
            }
            _composerController.OpenAgentMentionMenu(ChatWorkspace.AgentMentionButton, ChatWorkspace.PromptBox);
        };
        ChatWorkspace.EmptyAgentMentionButton.Click += (_, _) =>
        {
            if (_composerController.CollaborationChatActive)
            {
                _composerController.OpenCollaborationMentionMenu(ChatWorkspace.EmptyAgentMentionButton, ChatWorkspace.EmptyPromptBox);
                return;
            }
            _composerController.OpenAgentMentionMenu(ChatWorkspace.EmptyAgentMentionButton, ChatWorkspace.EmptyPromptBox);
        };
        ChatWorkspace.PermissionButton.Click += (_, _) => _composerController.OpenPermissionMenu(ChatWorkspace.PermissionButton);
        ChatWorkspace.EmptyPermissionButton.Click += (_, _) => _composerController.OpenPermissionMenu();
        ChatWorkspace.EmptyModelButton.Click += (_, _) => _composerController.OpenModelMenu();
        ChatWorkspace.ModelButton.Click += (_, _) => _composerController.OpenModelMenu(ChatWorkspace.ModelButton);
        ChatWorkspace.EmptyReasoningButton.Click += (_, _) => _composerController.OpenReasoningMenu(ChatWorkspace.EmptyReasoningButton);
        ChatWorkspace.ReasoningButton.Click += (_, _) => _composerController.OpenReasoningMenu(ChatWorkspace.ReasoningButton);
        ChatWorkspace.EmptyProjectButton.Click += (_, _) => _composerController.OpenProjectMenu();
        ChatWorkspace.EmptyRuntimeButton.Click += (_, _) => _composerController.OpenRuntimeMenu();
        ChatWorkspace.EmptyBranchButton.Click += (_, _) => _composerController.OpenBranchMenu();
        ChatWorkspace.InlineOpenWebPreviewButton.Click += (_, _) => OpenInlineWebPreviewMenu();
        WorkbenchShell.CollapseWorkbenchPanelButton.Click += async (_, _) => await _workbenchController.ToggleWorkbenchPanelAsync();
        WorkbenchShell.RestoreWorkbenchPanelButton.Click += async (_, _) => await _workbenchController.ToggleWorkbenchPanelAsync(forceOpen: true);
        WorkbenchShell.RefreshGitChangesButton.Click += async (_, _) => await _workbenchController.RefreshGitChangesAsync(selectFirst: true);
        WorkbenchShell.LocalEnvironmentButton.Click += (_, _) => _composerController.OpenRuntimeMenu(WorkbenchShell.LocalEnvironmentButton);
        WorkbenchShell.BranchEnvironmentButton.Click += (_, _) => _composerController.OpenBranchMenu(WorkbenchShell.BranchEnvironmentButton);
        WorkbenchShell.CommitPushButton.Click += (_, _) => _workbenchController.OpenCommitPushMenu();
        WorkbenchShell.OpenTerminalButton.Click += async (_, _) => await _workbenchController.ShowTerminalAsync();
        TerminalPanel.CloseTerminalButton.Click += (_, _) => _workbenchController.HideTerminal();
        TerminalPanel.TerminalOutputBox.PreviewKeyDown += _workbenchController.TerminalOutputBox_PreviewKeyDown;
        TerminalPanel.TerminalOutputBox.PreviewMouseDown += _workbenchController.TerminalOutputBox_PreviewMouseDown;
        TerminalPanel.TerminalOutputBox.TextChanged += _workbenchController.TerminalOutputBox_TextChanged;
        WorkbenchShell.GitChangesList.SelectionChanged += async (_, _) => await _workbenchController.RefreshSelectedChangeDiffAsync();
        ChatWorkspace.InlineGitChangesList.SelectionChanged += async (_, _) => await _workbenchController.RefreshSelectedChangeDiffAsync(preferInline: true);
        WorkbenchShell.ReviewChangesButton.Click += async (_, _) =>
        {
            await _workbenchController.RefreshGitChangesAsync(selectFirst: true);
            await _workbenchController.RefreshSelectedChangeDiffAsync(forceFirst: true);
        };
        ChatWorkspace.InlineReviewChangesButton.Click += async (_, _) =>
        {
            await _workbenchController.RefreshGitChangesAsync(selectFirst: true, preferInline: true);
            await _workbenchController.RefreshSelectedChangeDiffAsync(forceFirst: true, preferInline: true);
        };
        WorkbenchShell.UndoSelectedChangeButton.Click += async (_, _) => await _workbenchController.UndoSelectedGitChangeAsync();
        ChatWorkspace.InlineUndoChangeButton.Click += async (_, _) => await _workbenchController.UndoSelectedGitChangeAsync(preferInline: true);
    }
}
