namespace SpiritKinDesktop;

internal sealed record CollaborationParticipantOption(string ParticipantId, string Label, string Kind, string Status, string Mention, bool CanChat, bool CanExecute, bool RequiresReview, string[] Aliases);

internal sealed record GitCommandResult(bool Success, string Output);

internal sealed record PendingConfirmationInfo(string Target, string Operation, string RiskLevel);

internal sealed record ComposerSendOptions(bool SteerConversation = false);

internal readonly struct TraceMeta
{
    public long Seq { get; init; }
    public string RunId { get; init; }
    public string EventId { get; init; }
    public string SpanId { get; init; }
    public string ParentId { get; init; }
    public string Status { get; init; }
    public bool IsTerminal { get; init; }
    public string AgentId { get; init; }
    public string MessageId { get; init; }
    public string StepKind { get; init; }
    public string CallAgent { get; init; }
    public string CallModel { get; init; }
    public string CallProvider { get; init; }
    public string CommandText { get; init; }
    public string CommandOutput { get; init; }
    public string ShellLabel { get; init; }
}
