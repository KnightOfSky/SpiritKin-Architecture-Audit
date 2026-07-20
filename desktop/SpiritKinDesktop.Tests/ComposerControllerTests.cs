using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class ComposerControllerTests
{
    [Theory]
    [InlineData(true, false, true)]
    [InlineData(true, true, true)]
    [InlineData(false, true, false)]
    public void QuickChatVisibilityDependsOnDraftStateNotComposerMode(
        bool quickChatMode,
        bool collaborationChatActive,
        bool expected)
    {
        Assert.Equal(expected, RuntimeController.ShouldShowQuickChat(quickChatMode, collaborationChatActive));
    }

    [Theory]
    [InlineData(true, "模型协作已开启，点击切回普通对话")]
    [InlineData(false, "开启模型协作对话")]
    public void ComposerToggleKeepsStableLabelAndMovesStateTextToTooltip(bool active, string expectedToolTip)
    {
        var state = ComposerController.ResolveComposerToggleVisualState(
            active,
            "模型协作",
            "模型协作已开启，点击切回普通对话",
            "开启模型协作对话");

        Assert.Equal("模型协作", state.Content);
        Assert.Equal(expectedToolTip, state.ToolTip);
        Assert.DoesNotContain("已开启", state.Content);
    }

    [Theory]
    [InlineData("编程", "programming")]
    [InlineData("maintext", "main_text")]
    [InlineData("claudecode", "")]
    public void NormalizeAgentMentionKeySupportsChineseAndTechnicalAliases(string rawMention, string expectedAgentId)
    {
        var normalized = ComposerController.NormalizeAgentMentionKey(rawMention);
        var alias = ComposerController.ResolveAgentAliasTarget(normalized);

        Assert.DoesNotContain("@", normalized);
        Assert.Equal(expectedAgentId, alias);
    }

    [Fact]
    public void DescribeExecutionWorkStepKeepsStableKeyAndReadableFailure()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "target": "local_pc",
              "operation": "launch_app",
              "success": false,
              "error": "permission denied"
            }
            """);

        var step = ComposerController.DescribeExecutionWorkStep(doc.RootElement);

        Assert.Equal("工作指令", step.Title);
        Assert.Contains("permission denied", step.Detail);
        Assert.Equal("exec:local_pc.launch_app:fail", step.Key);
    }

    [Fact]
    public void DescribeExecutionWorkStepUsesActualCmdAsCommandPreview()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "target": "local_pc",
              "operation": "launch_app",
              "success": true,
              "data": { "app_name": "cmd" }
            }
            """);

        var step = ComposerController.DescribeExecutionWorkStep(doc.RootElement);

        Assert.Equal("工作指令", step.Title);
        Assert.Equal("cmd · 完成。", step.Detail);
        Assert.Equal("exec:local_pc.launch_app:ok", step.Key);
    }

    [Fact]
    public void MainAgentModelWorkUpdateStartsWithStructuredCallLane()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "kind": "thought",
              "text": "正在生成回复。",
              "detail": {
                "model_call": {
                  "agent_id": "main_text",
                  "target_label": "Spirit",
                  "provider": "lmstudio",
                  "model": "qwen3",
                  "external": false
                }
              }
            }
            """);

        var step = ComposerController.DescribeAssistantWorkUpdatedStep(doc.RootElement);
        var meta = ComposerController.ReadTraceMeta(doc.RootElement);

        Assert.Equal("调用", step.Title);
        Assert.Equal("正在生成回复。", step.Detail);
        Assert.Equal("call", meta.StepKind);
        Assert.Equal("Spirit", meta.CallAgent);
        Assert.Equal("qwen3", meta.CallModel);
    }

    [Fact]
    public void ExternalAgentModelWorkUpdateUsesStructuredCallLane()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "kind": "thought",
              "text": "正在调用编程 Agent。",
              "detail": {
                "model_call": {
                  "agent_id": "programming",
                  "target_agent_id": "programming",
                  "target_label": "编程 Agent",
                  "provider": "openai",
                  "model": "gpt-5-codex",
                  "external": true
                }
              }
            }
            """);

        var step = ComposerController.DescribeAssistantWorkUpdatedStep(doc.RootElement);
        var meta = ComposerController.ReadTraceMeta(doc.RootElement);

        Assert.Equal("调用", step.Title);
        Assert.Equal("call", meta.StepKind);
        Assert.Equal("编程 Agent", meta.CallAgent);
        Assert.Equal("gpt-5-codex", meta.CallModel);
        Assert.Equal("openai", meta.CallProvider);
    }

    [Fact]
    public void StructuredToolWorkUpdateCarriesToolInvocationInsteadOfShellGuess()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "kind": "command",
              "text": "工具 app.launch 执行完成。",
              "detail": {
                "tool": { "name": "app.launch" },
                "result": { "message": "opened" }
              }
            }
            """);

        var meta = ComposerController.ReadTraceMeta(doc.RootElement, "assistant.work_updated");

        Assert.Equal("command", meta.StepKind);
        Assert.Equal("app.launch", meta.CommandText);
        Assert.Equal("opened", meta.CommandOutput);
        Assert.Equal("Tool", meta.ShellLabel);
    }

    [Fact]
    public void StructuredWindowsCommandDefaultsToCmdAndKeepsActualArgv()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "kind": "command",
              "text": "git status completed",
              "detail": {
                "execution": {
                  "target": "git",
                  "operation": "git.status",
                  "command": "git -C D:\\SpiritKinAI status --short",
                  "output": "M desktop/App.xaml"
                }
              }
            }
            """);

        var meta = ComposerController.ReadTraceMeta(doc.RootElement, "assistant.work_updated");

        Assert.Equal("command", meta.StepKind);
        Assert.Equal("git -C D:\\SpiritKinAI status --short", meta.CommandText);
        Assert.Equal("M desktop/App.xaml", meta.CommandOutput);
        Assert.Equal("CMD", meta.ShellLabel);
    }

    [Fact]
    public void ReadTraceMetaReadsSchemaFieldsAndTerminalStatus()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "seq": 12,
              "event_id": "evt_1",
              "run_id": "run_1",
              "span_id": "span_1",
              "parent_id": "root",
              "status": "completed",
              "detail": {
                "agent_id": "programming"
              }
            }
            """);

        var meta = ComposerController.ReadTraceMeta(doc.RootElement, "assistant.execution.completed");

        Assert.Equal(12, meta.Seq);
        Assert.Equal("evt_1", meta.EventId);
        Assert.Equal("programming", meta.AgentId);
        Assert.True(meta.IsTerminal);
    }

    [Fact]
    public void MainReasoningSnapshotsGrowOneLaneUntilAToolBoundary()
    {
        var message = new DesktopMessage
        {
            Steps =
            [
                new DesktopWorkStep
                {
                    Kind = "thinking",
                    Title = "思考",
                    Detail = "先读取上下文。",
                    SpanId = "desktop-run:model:main_text:1:reasoning",
                    AgentId = "main_text",
                    Status = "stream",
                    IsStreamLane = true,
                },
            ],
        };
        var accumulated = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "先读取上下文，再核对用户目标。",
            SpanId = "desktop-run:model:main_text:1:reasoning",
            AgentId = "main_text",
            Status = "completed",
            IsStreamLane = true,
            Seq = 2,
        };

        Assert.True(ComposerController.TryMergeReasoningStreamStep(message, accumulated));
        Assert.Single(message.Steps);
        Assert.Equal(accumulated.Detail, message.Steps[0].Detail);
        Assert.Equal("completed", message.Steps[0].Status);

        message.Steps.Add(new DesktopWorkStep
        {
            Kind = "command",
            Title = "执行命令",
            CommandText = "git status --short",
            SpanId = "desktop-run:tool:1",
        });
        var resumed = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "根据命令结果继续分析。",
            SpanId = accumulated.SpanId,
            AgentId = "main_text",
            Status = "stream",
            IsStreamLane = true,
        };

        Assert.False(ComposerController.TryMergeReasoningStreamStep(message, resumed));
        Assert.Equal(2, message.Steps.Count);
    }
}
