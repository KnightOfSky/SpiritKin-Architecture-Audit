using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class McpManagementControllerTests
{
    [Fact]
    public void BuildPolicyTextUsesDefaultGateWhenPolicyMissing()
    {
        using var doc = JsonDocument.Parse("{}");

        var text = McpManagementController.BuildPolicyText(doc.RootElement);

        Assert.Equal("策略：MCP 工具必须先审核并启用后才会导出。", text);
    }

    [Fact]
    public void BuildPolicyTextKeepsExistingPolicyLabels()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "policy": {
                "external_launch_enabled": true,
                "requires_review_before_tool_export": false,
                "requires_agent_allowlist": true,
                "tool_execution_mode": "proxy_pending_execution"
              }
            }
            """);

        var text = McpManagementController.BuildPolicyText(doc.RootElement);

        Assert.Equal("策略：允许启动 · 未要求审核 · 需要 Agent allowlist · proxy_pending_execution", text);
    }
}
