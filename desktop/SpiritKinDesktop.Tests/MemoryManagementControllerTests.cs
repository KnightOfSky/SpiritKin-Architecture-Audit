using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class MemoryManagementControllerTests
{
    [Fact]
    public void BuildSummaryShowsConflictAndAuditCounts()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "stats": { "total": 12, "pending_conflict_count": 2, "conflict_count": 5 },
              "audit": { "by_severity": { "error": 1, "warning": 4 } }
            }
            """);

        var summary = MemoryManagementController.BuildSummary(doc.RootElement);

        Assert.Contains("长期记忆 12", summary);
        Assert.Contains("待复核 2", summary);
        Assert.Contains("冲突历史 5", summary);
        Assert.Contains("审计错误/警告 1/4", summary);
    }

    [Theory]
    [InlineData("prefer_new", true)]
    [InlineData("prefer_existing", true)]
    [InlineData("context_difference", false)]
    [InlineData("clarification_needed", false)]
    [InlineData("dismiss", false)]
    public void DestructiveMemoryResolutionsRequireReason(string resolution, bool expected)
    {
        Assert.Equal(expected, MemoryManagementController.ResolutionRequiresReason(resolution));
    }

    [Fact]
    public void ConflictViewModelPreservesEvidenceAndStatus()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "conflict_id": "memconf-000001",
              "status": "pending_review",
              "confidence": 0.72,
              "reason": "shared-topic lexical contradiction: 喜欢",
              "created_at": 1784256000,
              "source_entry_id": "ltm-000002",
              "target_entry_id": "ltm-000001",
              "source_memory": { "content": "用户不喜欢咖啡", "metadata": { "source": "user_feedback", "attribution": "user_explicit", "evidence_quotes": ["我现在不喝咖啡"] } },
              "target_memory": { "content": "用户喜欢咖啡", "metadata": { "source": "conversation", "attribution": "assistant_inferred", "evidence_quotes": ["我喜欢咖啡"] } }
            }
            """);

        var viewModel = MemoryConflictViewModel.FromJson(doc.RootElement);

        Assert.True(viewModel.IsOpen);
        Assert.Equal("待复核", viewModel.StatusLabel);
        Assert.Contains("我现在不喝咖啡", viewModel.SourceEvidence);
        Assert.Contains("我喜欢咖啡", viewModel.TargetEvidence);
        Assert.Contains("user_feedback", viewModel.SourceProvenance);
        Assert.Contains("assistant_inferred", viewModel.TargetProvenance);
        Assert.Contains("memconf-000001", viewModel.Meta);
    }
}
