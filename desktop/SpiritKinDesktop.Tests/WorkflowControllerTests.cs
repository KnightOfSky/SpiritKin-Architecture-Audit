using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class WorkflowControllerTests
{
    [Fact]
    public void BuildWorkflowSummaryIncludesOverviewCounts()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "overview": {
                "default_workflow_name": "commerce.product.publish.v1",
                "definition_count": 4,
                "run_count": 9,
                "active_run_count": 2
              }
            }
            """);

        var summary = WorkflowController.BuildWorkflowSummary(doc.RootElement);

        Assert.Contains("Commerce Product Publish", summary);
        Assert.Contains("定义 4", summary);
        Assert.Contains("运行 9", summary);
        Assert.Contains("active 2", summary);
    }

    [Fact]
    public void TryFindWorkflowCycleDetectsDependencyLoop()
    {
        var nodes = new[]
        {
            Node("capture", "publish"),
            Node("normalize", "capture"),
            Node("publish", "normalize"),
        };

        var found = WorkflowController.TryFindWorkflowCycle(nodes, out var cycle);

        Assert.True(found);
        Assert.Contains("capture", cycle);
        Assert.Contains("publish", cycle);
    }

    [Fact]
    public void TryFindWorkflowCycleIgnoresAcyclicGraph()
    {
        var nodes = new[]
        {
            Node("capture", ""),
            Node("normalize", "capture"),
            Node("publish", "normalize"),
        };

        var found = WorkflowController.TryFindWorkflowCycle(nodes, out var cycle);

        Assert.False(found);
        Assert.Equal("", cycle);
    }

    [Fact]
    public void WorkflowTemplateArgumentsJsonSupportsJsonSchemaParameters()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "parameters": {
                "type": "object",
                "properties": {
                  "query": {"type": "string"},
                  "count": {"type": "integer"},
                  "fresh": {"type": "boolean"}
                },
                "required": ["query"],
                "additionalProperties": true
              }
            }
            """);

        var argumentsJson = WorkflowController.WorkflowTemplateArgumentsJson(doc.RootElement);
        using var arguments = JsonDocument.Parse(argumentsJson);

        Assert.Equal("{{query}}", arguments.RootElement.GetProperty("query").GetString());
        Assert.Equal(0, arguments.RootElement.GetProperty("count").GetInt32());
        Assert.False(arguments.RootElement.GetProperty("fresh").GetBoolean());
        Assert.False(arguments.RootElement.TryGetProperty("required", out _));
        Assert.False(arguments.RootElement.TryGetProperty("additionalProperties", out _));
    }

    private static WorkflowEditNodeViewModel Node(string id, string dependsOn) =>
        new(id, id, "agent_task", "main_text", dependsOn, "", "", "", "{}", 0, 0);
}
