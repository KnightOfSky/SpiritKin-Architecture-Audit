using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class ChatWorkspaceView : UserControl
{
    public ChatWorkspaceView()
    {
        InitializeComponent();
        // @提及气泡跟随输入焦点：失焦即收起，避免悬浮框残留遮挡界面。
        void SyncPromptPlaceholder() =>
            PromptPlaceholderTextElement.Visibility = string.IsNullOrEmpty(PromptBoxElement.Text)
                && !PromptBoxElement.IsKeyboardFocusWithin
                    ? System.Windows.Visibility.Visible
                    : System.Windows.Visibility.Collapsed;

        PromptBoxElement.GotKeyboardFocus += (_, _) => SyncPromptPlaceholder();
        PromptBoxElement.LostKeyboardFocus += (_, _) =>
        {
            AgentMentionPopupElement.IsOpen = false;
            SyncPromptPlaceholder();
        };
        EmptyPromptBoxElement.LostKeyboardFocus += (_, _) => EmptyAgentMentionPopupElement.IsOpen = false;
        PromptBoxElement.TextChanged += (_, _) => SyncPromptPlaceholder();
    }

    public System.Windows.Controls.Primitives.Popup AgentMentionPopup => AgentMentionPopupElement;

    public System.Windows.Controls.Primitives.Popup EmptyAgentMentionPopup => EmptyAgentMentionPopupElement;

    public System.Windows.Controls.TextBlock ActiveMetaText => ActiveMetaTextElement;

    public System.Windows.Controls.WrapPanel CollaborationMembersPanel => CollaborationMembersPanelElement;

    public System.Windows.Controls.TextBlock ActiveTitleText => ActiveTitleTextElement;

    public System.Windows.Controls.Button AgentMentionButton => AgentMentionButtonElement;

    public System.Windows.Controls.TextBlock AgentMentionStatusText => AgentMentionStatusTextElement;

    public System.Windows.Controls.Button AttachButton => AttachButtonElement;

    public System.Windows.Controls.TextBlock AttachmentStatusText => AttachmentStatusTextElement;

    public System.Windows.Controls.Button CancelButton => CancelButtonElement;

    public System.Windows.Controls.Button ChatActionsButton => ChatActionsButtonElement;

    public System.Windows.Controls.CheckBox ChatDuplexToggle => ChatDuplexToggleElement;

    public System.Windows.Controls.TextBlock ChatDuplexBalanceText => ChatDuplexBalanceTextElement;

    public System.Windows.Controls.RowDefinition ChatArtifactsRow => ChatArtifactsRowElement;

    public System.Windows.Controls.Border ChatComposerPanel => ChatComposerPanelElement;

    public System.Windows.Controls.RowDefinition ChatComposerRow => ChatComposerRowElement;

    public System.Windows.Controls.Border ChatHeaderBar => ChatHeaderBarElement;

    public System.Windows.Controls.RowDefinition ChatHeaderRow => ChatHeaderRowElement;

    public System.Windows.Controls.GridSplitter ChatInputSplitter => ChatInputSplitterElement;

    public System.Windows.Controls.RowDefinition ChatSplitterRow => ChatSplitterRowElement;

    public System.Windows.Controls.Grid ChatWorkspacePage => ChatWorkspacePageElement;

    public System.Windows.Controls.Button ClearGoalButton => ClearGoalButtonElement;

    public System.Windows.Controls.Button ClearPlanButton => ClearPlanButtonElement;

    public System.Windows.Controls.ItemsControl ComposerAttachmentsList => ComposerAttachmentsListElement;

    public System.Windows.Controls.StackPanel ComposerStatusPanel => ComposerStatusPanelElement;

    public System.Windows.Controls.Grid ConfirmBar => ConfirmBarElement;

    public System.Windows.Controls.Button ConfirmButton => ConfirmButtonElement;

    public System.Windows.Controls.TextBlock ConfirmText => ConfirmTextElement;

    public System.Windows.Controls.Border ConversationArtifactsPanel => ConversationArtifactsPanelElement;

    public System.Windows.Controls.StackPanel CollaborationComposerPanel => CollaborationComposerPanelElement;

    public System.Windows.Controls.TextBlock CollaborationComposerHintText => CollaborationComposerHintTextElement;

    public System.Windows.Controls.ComboBox CollaborationComposerFromBox => CollaborationComposerFromBoxElement;

    public System.Windows.Controls.Button CollaborationModeButton => CollaborationModeButtonElement;

    public System.Windows.Controls.ComboBox CollaborationComposerRoleBox => CollaborationComposerRoleBoxElement;

    public System.Windows.Controls.ComboBox CollaborationComposerToBox => CollaborationComposerToBoxElement;

    public System.Windows.Controls.Button EmptyAgentMentionButton => EmptyAgentMentionButtonElement;

    public System.Windows.Controls.TextBlock EmptyAgentMentionStatusText => EmptyAgentMentionStatusTextElement;

    public System.Windows.Controls.Button EmptyAttachButton => EmptyAttachButtonElement;

    public System.Windows.Controls.TextBlock EmptyAttachmentStatusText => EmptyAttachmentStatusTextElement;

    public System.Windows.Controls.Button EmptyBranchButton => EmptyBranchButtonElement;

    public System.Windows.Controls.TextBlock EmptyBranchText => EmptyBranchTextElement;

    public System.Windows.Controls.Button EmptyClearGoalButton => EmptyClearGoalButtonElement;

    public System.Windows.Controls.Button EmptyClearPlanButton => EmptyClearPlanButtonElement;

    public System.Windows.Controls.ItemsControl EmptyComposerAttachmentsList => EmptyComposerAttachmentsListElement;

    public System.Windows.Controls.StackPanel EmptyComposerStatusPanel => EmptyComposerStatusPanelElement;

    public System.Windows.Controls.Button EmptyCollaborationModeButton => EmptyCollaborationModeButtonElement;

    public System.Windows.Controls.Button EmptyModelButton => EmptyModelButtonElement;

    public System.Windows.Controls.TextBlock EmptyModelText => EmptyModelTextElement;

    public System.Windows.Controls.Button EmptyReasoningButton => EmptyReasoningButtonElement;

    public System.Windows.Controls.TextBlock EmptyReasoningText => EmptyReasoningTextElement;

    public System.Windows.Controls.Button EmptyPermissionButton => EmptyPermissionButtonElement;

    public System.Windows.Controls.TextBlock EmptyPermissionText => EmptyPermissionTextElement;

    public System.Windows.Controls.Button EmptyProjectButton => EmptyProjectButtonElement;

    public System.Windows.Controls.TextBlock EmptyProjectHintText => EmptyProjectHintTextElement;

    public System.Windows.Controls.TextBox EmptyPromptBox => EmptyPromptBoxElement;

    public System.Windows.Controls.TextBlock EmptyQuickChatTitleText => EmptyQuickChatTitleTextElement;

    public System.Windows.Controls.Button EmptyRuntimeButton => EmptyRuntimeButtonElement;

    public System.Windows.Controls.TextBlock EmptyRuntimeText => EmptyRuntimeTextElement;

    public System.Windows.Controls.Button EmptySendButton => EmptySendButtonElement;

    public System.Windows.Controls.Button EmptyWebSearchModeButton => EmptyWebSearchModeButtonElement;

    public System.Windows.Controls.TextBox InlineChangedFileDiffBox => InlineChangedFileDiffBoxElement;

    public System.Windows.Controls.TextBlock InlineChangesMetaText => InlineChangesMetaTextElement;

    public System.Windows.Controls.TextBlock InlineChangesTitleText => InlineChangesTitleTextElement;

    public System.Windows.Controls.TextBlock InlineCompactStatusText => InlineCompactStatusTextElement;

    public System.Windows.Controls.ListBox InlineGitChangesList => InlineGitChangesListElement;

    public System.Windows.Controls.Button InlineOpenWebPreviewButton => InlineOpenWebPreviewButtonElement;

    public System.Windows.Controls.Button InlineReviewChangesButton => InlineReviewChangesButtonElement;

    public System.Windows.Controls.TextBlock InlineThinkingStatusText => InlineThinkingStatusTextElement;

    public System.Windows.Controls.Button InlineUndoChangeButton => InlineUndoChangeButtonElement;

    public System.Windows.Controls.TextBlock InlineWebPreviewStatusText => InlineWebPreviewStatusTextElement;

    public System.Windows.Controls.TextBlock InlineWorkedStatusText => InlineWorkedStatusTextElement;

    public System.Windows.Controls.Button ManageQuickCommandsButton => ManageQuickCommandsButtonElement;

    public System.Windows.Controls.ListBox MessagesList => MessagesListElement;

    public System.Windows.Controls.Button ModelButton => ModelButtonElement;

    public System.Windows.Controls.TextBlock ModelText => ModelTextElement;

    public System.Windows.Controls.Border TopicAnchorsPanel => TopicAnchorsPanelElement;

    public System.Windows.Controls.ItemsControl TopicAnchorsList => TopicAnchorsListElement;

    public System.Windows.Controls.TextBlock PendingText => PendingTextElement;

    public System.Windows.Controls.Button PermissionButton => PermissionButtonElement;

    public System.Windows.Controls.TextBlock PermissionText => PermissionTextElement;

    public System.Windows.Controls.TextBox PromptBox => PromptBoxElement;

    public System.Windows.Controls.Button ReasoningButton => ReasoningButtonElement;

    public System.Windows.Controls.TextBlock ReasoningText => ReasoningTextElement;

    public System.Windows.Controls.Grid QuickChatEmptyPanel => QuickChatEmptyPanelElement;

    public System.Windows.Controls.ComboBox QuickCommandBox => QuickCommandBoxElement;

    public System.Windows.Controls.Button SafetyResumeButton => SafetyResumeButtonElement;

    public System.Windows.Controls.TextBlock SafetyStatusText => SafetyStatusTextElement;

    public System.Windows.Controls.Button SafetyStopButton => SafetyStopButtonElement;

    public System.Windows.Controls.Button SaveNoteButton => SaveNoteButtonElement;

    public System.Windows.Controls.Button SendButton => SendButtonElement;

    public System.Windows.Controls.TextBlock SendStatusText => SendStatusTextElement;

    public System.Windows.Controls.TextBlock WsStatusText => WsStatusTextElement;

    public System.Windows.Controls.Button WebSearchModeButton => WebSearchModeButtonElement;
}
