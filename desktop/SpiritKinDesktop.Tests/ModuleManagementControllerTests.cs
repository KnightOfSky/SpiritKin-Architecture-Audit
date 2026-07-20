using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class ModuleManagementControllerTests
{
    [Theory]
    [InlineData("workflows", "", "workflows")]
    [InlineData("knowledge_base", "", "agents:knowledge")]
    [InlineData("search_management", "", "search")]
    [InlineData("mcp_management", "", "mcp")]
    [InlineData("mobile_management", "", "mobile")]
    [InlineData("models", "", "models")]
    [InlineData("memory", "/desktop/memory", "memory")]
    [InlineData("module_governance", "", "overview")]
    [InlineData("unknown", "/desktop/learning", "learning")]
    [InlineData("unknown", "/desktop/search-management", "search")]
    public void ModulePageForKeepsExistingRoutingMap(string moduleId, string endpoint, string expected)
    {
        Assert.Equal(expected, ModuleManagementController.ModulePageFor(moduleId, endpoint));
    }

    [Theory]
    [InlineData("service_ports", "operations", "/desktop/service-ports", "services")]
    [InlineData("project_runtime", "project_runtime", "/desktop/project-runtime", "tasks:projects")]
    [InlineData("resource_registry", "resource-registry", "/desktop/resource-registry", "modules")]
    [InlineData("state_maintenance", "state_maintenance", "/desktop/state-maintenance", "mobile")]
    [InlineData("action_log", "action_log", "/desktop/action-log", "logs")]
    [InlineData("workflows", "workflows", "/desktop/workflows", "workflows")]
    [InlineData("memory", "memory", "/desktop/memory", "memory")]
    public void NormalizeDesktopDestinationMapsBackendAliasesToVisiblePages(
        string moduleId,
        string desktopPage,
        string endpoint,
        string expected)
    {
        Assert.Equal(expected, ModuleManagementController.NormalizeDesktopDestination(moduleId, desktopPage, endpoint));
    }

    [Theory]
    [InlineData("ready", "就绪")]
    [InlineData("needs_attention", "注意")]
    [InlineData("blocked", "阻塞")]
    [InlineData("custom", "custom")]
    public void ModuleStatusLabelKeepsExistingLabels(string status, string expected)
    {
        Assert.Equal(expected, ModuleManagementController.ModuleStatusLabel(status));
    }

    [Fact]
    public void BuildSummaryIncludesOverviewAndPortfolioCounts()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "overview": {
                "status": "needs_attention",
                "module_count": 9,
                "ready_count": 5,
                "attention_count": 3,
                "blocked_count": 1,
                "action_count": 7
              },
              "portfolio": {
                "health_score": 82,
                "readiness_percent": 61,
                "high_action_count": 2,
                "risk_counts": {"high": 1, "medium": 4, "low": 6}
              }
            }
            """);

        var summary = ModuleManagementController.BuildSummary(doc.RootElement);

        Assert.Contains("状态 注意", summary);
        Assert.Contains("模块 9", summary);
        Assert.Contains("就绪 5", summary);
        Assert.Contains("风险 高/中/低 1/4/6", summary);
    }
}
