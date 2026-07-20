using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class AgentsControllerTests
{
    [Fact]
    public void UniqueIdSkipsExistingIdsIgnoringCase()
    {
        var id = AgentsController.UniqueId("agent", new[] { "agent_01", "AGENT_02" });

        Assert.Equal("agent_03", id);
    }

    [Fact]
    public void SafeRemoteExportIdKeepsOnlyStableIdentifierCharacters()
    {
        var id = AgentsController.SafeRemoteExportId(" commerce/store A:2026 ");

        Assert.Equal("commercestoreA2026", id);
    }

    [Fact]
    public void EnsureOkResponseThrowsUsefulError()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "ok": false,
              "error": "missing_worker",
              "detail": "remote target is unavailable"
            }
            """);

        var error = Assert.Throws<InvalidOperationException>(() => AgentsController.EnsureOkResponse(doc.RootElement, "同步失败"));

        Assert.Contains("missing_worker", error.Message);
        Assert.Contains("remote target is unavailable", error.Message);
    }

    [Fact]
    public void BuildAgentDistributionStatusSummaryIncludesRouteAndSkillAssistState()
    {
        using var distribution = JsonDocument.Parse(
            """
            {
              "counts": {
                "agents_enabled": 3,
                "agents_total": 5,
                "external_assistants_enabled": 2
              },
              "remote_distribution": {
                "targets_enabled": 1,
                "targets_total": 2
              },
              "active_route": {
                "profile_id": "cloud_review",
                "strategy": "review_gate",
                "primary_text": {
                  "member_id": "programming",
                  "provider": "openai",
                  "model": "gpt-5"
                }
              }
            }
            """);
        using var assist = JsonDocument.Parse(
            """
            {
              "mode": "review_gate",
              "assistant_id": "codex",
              "agent_id": "programming"
            }
            """);

        var summary = AgentsController.BuildAgentDistributionStatusSummary(distribution.RootElement, assist.RootElement);

        Assert.Contains("启用 3 / 总计 5", summary);
        Assert.Contains("外部助手 2", summary);
        Assert.Contains("远端 1/2", summary);
        Assert.Contains("cloud_review", summary);
        Assert.Contains("审核", summary);
        Assert.Contains("门禁", summary);
        Assert.Contains("编程", summary);
        Assert.Contains("OpenAI", summary);
        Assert.Contains("gpt-5", summary);
    }
}
