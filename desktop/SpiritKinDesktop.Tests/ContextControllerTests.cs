using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class ContextControllerTests
{
    [Theory]
    [InlineData("request_started", "调用")]
    [InlineData("request_completed", "调用")]
    [InlineData("request_failed", "调用")]
    [InlineData("context_loaded", "思考")]
    [InlineData("prompt_ready", "思考")]
    [InlineData("reply_posting", "思考")]
    public void CollaborationLifecycleUsesCallOnlyForActualModelRequest(string lifecycle, string expected)
    {
        Assert.Equal(expected, ContextController.CollaborationLifecycleStepTitle(lifecycle));
    }

    [Fact]
    public void CollaborationToolPreviewUsesActualCommandParameter()
    {
        using var document = JsonDocument.Parse("""{"params":{"app_name":"cmd"}}""");

        Assert.Equal("cmd", ContextController.CollaborationToolCommandPreview(document.RootElement));
    }

    [Fact]
    public void CollaborationCodexCommandEventBuildsRealPowerShellInvocationWithOutput()
    {
        using var startedDocument = JsonDocument.Parse("""
        {
          "detail": {
            "worker_event": {
              "metadata": {
                "tool_call_id": "cmd-live-1",
                "target": "external_cli",
                "operation": "command_execution",
                "command": "rg --files -g '!node_modules'",
                "lifecycle": "tool_running",
                "output": "$ rg --files -g '!node_modules'"
              }
            }
          }
        }
        """);
        var startedTool = ContextController.CollaborationToolEventInfo(startedDocument.RootElement);
        var message = new DesktopMessage { Id = "cmd-card", Kind = "work", Subtitle = "running" };

        Assert.True(startedTool.IsTool);
        Assert.Equal("rg --files -g '!node_modules'", startedTool.CommandPreview);
        Assert.True(ContextController.UpsertCollaborationToolStep(
            message,
            startedTool,
            "tool_running",
            "$ rg --files -g '!node_modules'",
            new TraceMeta { AgentId = "codex", EventId = "cmd-start", Seq = 1 }));

        using var completedDocument = JsonDocument.Parse("""
        {
          "detail": {
            "worker_event": {
              "metadata": {
                "tool_call_id": "cmd-live-1",
                "target": "external_cli",
                "operation": "command_execution",
                "command": "rg --files -g '!node_modules'",
                "command_output": "desktop/SpiritKinDesktop/App.xaml",
                "lifecycle": "tool_completed",
                "output": "desktop/SpiritKinDesktop/App.xaml"
              }
            }
          }
        }
        """);
        var completedTool = ContextController.CollaborationToolEventInfo(completedDocument.RootElement);
        Assert.True(ContextController.UpsertCollaborationToolStep(
            message,
            completedTool,
            "tool_completed",
            "desktop/SpiritKinDesktop/App.xaml",
            new TraceMeta { AgentId = "codex", EventId = "cmd-complete", Seq = 2 }));

        var model = WorkChainViewModel.FromMessage(message);
        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        var invocation = Assert.Single(group.Invocations);
        Assert.Equal("Ran command", group.HeaderText);
        Assert.Equal("PowerShell", invocation.ShellLabel);
        Assert.Equal("rg --files -g '!node_modules'", invocation.CommandText);
        Assert.Equal("desktop/SpiritKinDesktop/App.xaml", invocation.CommandOutput);
        Assert.Equal("Success", invocation.ResultLabel);
    }

    [Fact]
    public void CollaborationProjectionPreservesExternalCallAndCommandFields()
    {
        var source = new DesktopWorkStep
        {
            Kind = "command",
            Title = "执行命令",
            Detail = "rg --files",
            Status = "completed",
            AgentId = "codex",
            CallAgent = "Codex",
            CallModel = "gpt-5-codex",
            CallProvider = "openai",
            CommandText = "rg --files",
            CommandOutput = "desktop/App.xaml",
            ShellLabel = "CMD",
        };

        var projected = ContextController.ProjectCollaborationWorkStep(source);

        Assert.Equal("Codex", projected.CallAgent);
        Assert.Equal("gpt-5-codex", projected.CallModel);
        Assert.Equal("openai", projected.CallProvider);
        Assert.Equal("rg --files", projected.CommandText);
        Assert.Equal("desktop/App.xaml", projected.CommandOutput);
        Assert.Equal("CMD", projected.ShellLabel);
    }

    [Theory]
    [InlineData("cmd /c dir", "CMD")]
    [InlineData("powershell -NoProfile -Command Get-ChildItem", "PowerShell")]
    [InlineData("Get-ChildItem -Force", "PowerShell")]
    public void CollaborationShellLabelMatchesTheActualWindowsCommandHost(string command, string expected)
    {
        Assert.Equal(expected, ContextController.CollaborationToolShellLabel("external_cli", "command_execution", command));
    }

    [Theory]
    [InlineData("Project A", "project-a")]
    [InlineData("session_123", "session_123")]
    [InlineData("中文 任务", "中文-任务")]
    [InlineData("", "default")]
    public void NormalizeCollaborationThreadKeyKeepsStableSlug(string raw, string expected)
    {
        Assert.Equal(expected, ContextController.NormalizeCollaborationThreadKey(raw));
    }

    [Theory]
    [InlineData("ui refactor", "thread-ui-refactor")]
    [InlineData("topic-existing", "thread-topic-existing")]
    [InlineData("project-shop", "thread-project-shop")]
    [InlineData("", "")]
    public void NormalizeCollaborationThreadIdAddsTopicPrefixWhenNeeded(string raw, string expected)
    {
        Assert.Equal(expected, ContextController.NormalizeCollaborationThreadId(raw));
    }

    [Theory]
    [InlineData("Claude Code", "claude_code")]
    [InlineData("Codex CLI", "codex")]
    [InlineData("编程Agent", "programming")]
    [InlineData("视觉Agent", "vision_model")]
    [InlineData("custom-agent", "custom_agent")]
    public void NormalizeCollaborationWorkerAgentMapsKnownAliases(string raw, string expected)
    {
        Assert.Equal(expected, ContextController.NormalizeCollaborationWorkerAgent(raw));
    }

    [Fact]
    public void BuildCollaborationTurnRefillPayloadUsesHumanActorAndThread()
    {
        var payload = ContextController.BuildCollaborationTurnRefillPayload("thread-alpha");
        var json = System.Text.Json.JsonSerializer.Serialize(payload);

        Assert.Contains("\"action\":\"refill_turns\"", json);
        Assert.Contains("\"thread_id\":\"thread-alpha\"", json);
        Assert.Contains("\"actor\":\"human_desktop\"", json);
    }

    [Fact]
    public void BuildCollaborationTurnCapPayloadAppliesCurrentThreadImmediately()
    {
        var payload = ContextController.BuildCollaborationTurnCapPayload("thread-alpha", 0);
        var json = System.Text.Json.JsonSerializer.Serialize(payload);

        Assert.Contains("\"action\":\"set_thread_turn_cap\"", json);
        Assert.Contains("\"thread_id\":\"thread-alpha\"", json);
        Assert.Contains("\"cap\":0", json);
        Assert.Contains("\"include_collaboration\":false", json);
    }

    [Fact]
    public void BuildCollaborationTurnPausePayloadUsesSoftStopAction()
    {
        var payload = ContextController.BuildCollaborationTurnPausePayload("thread-alpha");
        var json = System.Text.Json.JsonSerializer.Serialize(payload);

        Assert.Contains("\"action\":\"pause_turns\"", json);
        Assert.Contains("\"thread_id\":\"thread-alpha\"", json);
        Assert.Contains("\"actor\":\"human_desktop\"", json);
        Assert.Contains("\"include_collaboration\":false", json);
    }

    [Fact]
    public void CollaborationStreamingDraftIdentityUsesThreadAgentAndParentMessage()
    {
        var key = ContextController.CollaborationStreamingDraftKey("thread Alpha", "main_text", "message-123");
        var id = ContextController.CollaborationStreamingDraftId("thread Alpha", "main_text", "message-123");

        Assert.Equal("thread-alpha|main_text|message-123", key);
        Assert.Equal("collab-reply-reply-main_text-message-123", id);
    }

    private static DesktopMessage WorkCard(params DesktopWorkStep[] steps) => new()
    {
        Kind = "work",
        Steps = steps.ToList(),
    };

    private static DesktopWorkStep Step(
        string kind,
        string title,
        string detail,
        string agentId = "main_text",
        bool terminal = false,
        string eventId = "",
        bool streamLane = false) => new()
    {
        Kind = kind,
        Title = title,
        Detail = detail,
        AgentId = agentId,
        IsTerminal = terminal,
        EventId = eventId,
        IsStreamLane = streamLane,
        CreatedAt = 1,
    };

    [Fact]
    public void ReasoningAfterLifecycleStartsANewVisibleSegment()
    {
        var thinking = Step("thinking", "思考", "先分析问题", streamLane: true);
        var lifecycle = Step("thinking", "调用", "正在调用模型 API");
        var message = WorkCard(thinking, lifecycle);

        var merged = ContextController.TryMergeCollaborationStreamStep(
            message, "reasoning", "thinking", "思考", "，再给结论",
            new TraceMeta { AgentId = "main_text", EventId = "ev-2" });

        Assert.False(merged);
        Assert.Equal(2, message.Steps!.Count);
        Assert.Equal("先分析问题", thinking.Detail);
        Assert.Equal("正在调用模型 API", lifecycle.Detail);
    }

    [Fact]
    public void MergeCollaborationStreamStepAppendsDiscreteCodexReasoningSummaries()
    {
        var thinking = Step("thinking", "思考", "先检查事件协议", agentId: "codex", streamLane: true);
        var message = WorkCard(thinking);

        var merged = ContextController.TryMergeCollaborationStreamStep(
            message, "reasoning", "thinking", "思考", "\n再核对工具调用与结果 ID",
            new TraceMeta { AgentId = "codex", EventId = "ev-summary-2" });

        Assert.True(merged);
        Assert.Equal("先检查事件协议\n再核对工具调用与结果 ID", thinking.Detail);
    }

    [Fact]
    public void LifecycleMilestonesRemainSeparateEvents()
    {
        var lifecycle = Step("thinking", "调用", "消息已入队");
        var message = WorkCard(lifecycle);

        var merged = ContextController.TryMergeCollaborationStreamStep(
            message, "lifecycle", "thinking", "调用", "正在调用模型 API",
            new TraceMeta { AgentId = "main_text", EventId = "ev-2" });

        Assert.False(merged);
        Assert.Single(message.Steps!);
        Assert.Equal("消息已入队", lifecycle.Detail);
    }

    [Fact]
    public void MergeCollaborationStreamStepSkipsTerminalLaneAndDuplicateEvent()
    {
        var terminal = Step("thinking", "思考", "已完结", terminal: true, streamLane: true);
        Assert.False(ContextController.TryMergeCollaborationStreamStep(
            WorkCard(terminal), "reasoning", "thinking", "思考", "新批次",
            new TraceMeta { AgentId = "main_text", EventId = "ev-2" }));

        var lane = Step("thinking", "思考", "同一事件", eventId: "ev-1", streamLane: true);
        Assert.False(ContextController.TryMergeCollaborationStreamStep(
            WorkCard(lane), "reasoning", "thinking", "思考", "重复投递",
            new TraceMeta { AgentId = "main_text", EventId = "ev-1" }));
        Assert.Equal("同一事件", lane.Detail);
    }

    [Fact]
    public void MergeCollaborationStreamStepKeepsAgentLanesIndependent()
    {
        var deepseekLane = Step("thinking", "思考", "DeepSeek 的思路", agentId: "model_deepseek", streamLane: true);
        var mainLane = Step("thinking", "思考", "本地模型的思路", streamLane: true);
        var message = WorkCard(deepseekLane, mainLane);

        var merged = ContextController.TryMergeCollaborationStreamStep(
            message, "reasoning", "thinking", "思考", "继续",
            new TraceMeta { AgentId = "main_text", EventId = "ev-2" });

        Assert.True(merged);
        Assert.Equal("本地模型的思路继续", mainLane.Detail);
        Assert.Equal("DeepSeek 的思路", deepseekLane.Detail);
    }

    [Fact]
    public void ReasoningAfterCommandDoesNotReplaceEarlierThought()
    {
        var firstThought = Step("thinking", "思考", "先检查项目结构", streamLane: true);
        var command = Step("command", "工具调用", "Get-ChildItem");
        command.CommandText = "Get-ChildItem -Force";
        command.ShellLabel = "PowerShell";
        var message = WorkCard(firstThought, command);

        var merged = ContextController.TryMergeCollaborationStreamStep(
            message,
            "reasoning",
            "thinking",
            "思考",
            "命令完成后继续分析",
            new TraceMeta { AgentId = "main_text", EventId = "ev-after-command" },
            "先检查项目结构命令完成后继续分析");

        Assert.False(merged);
        Assert.Equal("先检查项目结构", firstThought.Detail);
        Assert.Equal("Get-ChildItem -Force", command.CommandText);
    }

    // 修I（批次十）：卡完结后迟到流式批被丢，"回复"泳道常停在中途；
    // 权威全文到达时应覆盖被截断的泳道（更长即覆盖），截断自愈。
    [Fact]
    public void UpsertReplyStepOverwritesTruncatedLaneWithAuthoritativeContent()
    {
        var truncated = Step("result", "回复", "回复开头被截断在中途", agentId: "main_text");
        truncated.Status = "stream";
        var message = WorkCard(truncated);

        var changed = ContextController.UpsertCollaborationReplyStep(
            message, "回复开头被截断在中途，加上权威定稿补齐的完整结论", "main_text");

        Assert.True(changed);
        Assert.Single(message.Steps!);
        Assert.Equal("回复开头被截断在中途，加上权威定稿补齐的完整结论", truncated.Detail);
        Assert.Equal("completed", truncated.Status);
    }

    [Fact]
    public void UpsertReplyStepKeepsLongerLaneAndAddsMissingLane()
    {
        // 泳道比权威全文长（4000 截断的气泡 vs 完整泳道）：保留泳道，不回退。
        var full = Step("result", "回复", "泳道里的完整长文比截断后的气泡内容更长更全", agentId: "main_text");
        Assert.False(ContextController.UpsertCollaborationReplyStep(WorkCard(full), "截断的气泡内容", "main_text"));
        Assert.Equal("泳道里的完整长文比截断后的气泡内容更长更全", full.Detail);

        // 卡内无"回复"泳道：维持补一步成稿的既有语义。
        var message = WorkCard(Step("thinking", "思考", "推理"));
        Assert.True(ContextController.UpsertCollaborationReplyStep(message, "权威回复", "main_text"));
        Assert.Equal(2, message.Steps!.Count);
        Assert.Equal("回复", message.Steps[1].Title);
        Assert.Equal("权威回复", message.Steps[1].Detail);
    }

    [Theory]
    [InlineData("DeepSeek，打开百度", "DeepSeek", true)]
    [InlineData("Spirit，你来处理", "Spirit", true)]
    [InlineData("@qwen/qwen3.6-35b-a3b 检查项目", "qwen/qwen3.6-35b-a3b", true)]
    [InlineData("@qwen3-vl:4b 检查图片", "qwen3-vl:4b", true)]
    [InlineData("write code for this", "Code", false)]
    [InlineData("请让所有模型查看", "所有", false)]
    public void ExplicitParticipantNameDetectionAvoidsAmbiguousAliases(string text, string alias, bool expected)
    {
        Assert.Equal(expected, ContextController.ContainsExplicitCollaborationParticipantName(text, alias));
    }

    [Theory]
    [InlineData("@qwen/qwen3.6-35b-a3b 检查项目", "qwen/qwen3.6-35b-a3b")]
    [InlineData("请 @qwen3-vl:4b 检查图片", "qwen3-vl:4b")]
    [InlineData("@qwen3-vl:4b: 检查图片", "qwen3-vl:4b")]
    [InlineData("@model_deepseek: 处理", "model_deepseek")]
    [InlineData("@model_deepseek 处理", "model_deepseek")]
    public void CollaborationMentionParserKeepsFullModelIdentifiers(string text, string expected)
    {
        Assert.Equal(expected, Assert.Single(ContextController.ExtractCollaborationMentionNames(text)));
    }

    [Fact]
    public void DispatchCardStageTitlesComeFromStructuredBackendEvents()
    {
        using var doc = JsonDocument.Parse("""
        {
          "detail": {
            "card_kind": "model_dispatch",
            "dispatch_stage": "route_bus"
          }
        }
        """);

        Assert.Equal("模型已接入", ContextController.CollaborationModelDispatchStepTitle(doc.RootElement));
    }

    [Fact]
    public void DispatchTargetsFanOutToEachModelReplyGroup()
    {
        using var doc = JsonDocument.Parse("""
        {
          "detail": {
            "call_targets": [
              {"agent_id":"main_text","label":"Spirit","provider":"lmstudio","model":"qwen3"},
              {"agent_id":"model_deepseek","label":"DeepSeek","provider":"deepseek","model":"deepseek-v4"}
            ]
          }
        }
        """);

        var targets = ContextController.CollaborationModelDispatchTargets(doc.RootElement);

        Assert.Equal(new[] { "main_text", "model_deepseek" }, targets.Select(item => item.AgentId));
        Assert.Equal("qwen3", targets[0].Model);
        Assert.Equal("deepseek", targets[1].Provider);
    }

    [Fact]
    public void CodexStyleReasoningActivityKeepsEmittedThinkingProcess()
    {
        var detail = ContextController.CodexStyleReasoningActivity(
            "Here is a thinking process with prompt constraints",
            "prompt echo");

        Assert.Equal("Here is a thinking process with prompt constraints", detail);
    }

    [Theory]
    [InlineData("收到，正在打开。\n{\"spiritkin_tool_call\":{\"target\":\"local_pc\",\"operation\":\"browser_open_url\",\"params\":{\"url\":\"https://www.baidu.com\"}}}", "收到，正在打开。")]
    [InlineData("收到。\n{'spiritkin_tool_call': {'target': 'local_pc', 'operation': 'screen_understand'}}", "收到。")]
    public void CollaborationToolPayloadsAreHiddenFromConversationText(string content, string expected)
    {
        Assert.Equal(expected, ContextController.StripCollaborationToolPayloads(content));
    }

    [Theory]
    [InlineData("executor_local_pc", true)]
    [InlineData("Executor_Local_Pc", true)]
    [InlineData("main_text", false)]
    [InlineData("", false)]
    public void ExecutorEventsAreInternalCollaborationMessages(string agentId, bool expected)
    {
        Assert.Equal(expected, ContextController.IsCollaborationExecutorAgentId(agentId));
    }

    [Fact]
    public void LegacyExecutorArtifactsCollapseIntoOriginalWorkCard()
    {
        var origin = new DesktopMessage
        {
            Id = "session-collab-work-thread-main_text-root-message",
            Kind = "work",
            WorkAgent = "Spirit",
            Subtitle = "worked",
            CreatedAt = 10,
            Steps = [new DesktopWorkStep { EventId = "origin", Title = "思考" }],
        };
        var continuation = new DesktopMessage
        {
            Id = "session-collab-work-thread-main_text-result-message",
            Kind = "work",
            WorkAgent = "Spirit",
            Subtitle = "worked",
            CreatedAt = 10.002,
            Steps = [new DesktopWorkStep { EventId = "continuation", Title = "工具结果" }],
        };
        var session = new DesktopSession
        {
            Messages =
            [
                origin,
                new DesktopMessage { Id = "collab-reply-reply-main_text-root-message", Role = "assistant", CreatedAt = 10.0005 },
                new DesktopMessage { Id = "collab-reply-reply-main_text-result-message", Role = "assistant", CreatedAt = 10.003 },
                continuation,
                new DesktopMessage
                {
                    Id = "collab-reply-result-message",
                    Role = "assistant",
                    Subtitle = "Executor Local Pc",
                    Text = "Tool result: local_pc.launch_app",
                    CreatedAt = 11,
                },
            ],
        };

        Assert.True(ContextController.NormalizeLegacyCollaborationExecutorArtifacts(session));
        Assert.DoesNotContain(session.Messages, message => message.Subtitle.StartsWith("Executor "));
        Assert.DoesNotContain(session.Messages, message => ReferenceEquals(message, continuation));
        Assert.DoesNotContain(session.Messages, message => message.Id == "collab-reply-reply-main_text-root-message");
        Assert.Contains(session.Messages, message => message.Id.EndsWith("result-message") && message.Role == "assistant");
        Assert.Equal(2, origin.Steps.Count);
    }
}
