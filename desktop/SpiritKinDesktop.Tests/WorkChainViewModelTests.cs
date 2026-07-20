using System.Windows;

namespace SpiritKinDesktop.Tests;

// MergeSteps 身份配对 + IsStreamLane 打字机的行为契约（批次八）。
// 测试线程的 Dispatcher 不 pump frame → AdaptiveTypewriter 的 DispatcherTimer 永不 tick，
// 恰好可以断言"进了打字机队列（Detail 未瞬贴全文）"vs"直贴（Detail == 全文）"。
public sealed class WorkChainViewModelTests
{
    [Fact]
    public void WorkEntryTemplateSelectorKeepsThinkingCallsAndCommandsDistinct()
    {
        var narrativeTemplate = new DataTemplate();
        var callTemplate = new DataTemplate();
        var commandTemplate = new DataTemplate();
        var selector = new WorkEntryTemplateSelector
        {
            NarrativeTemplate = narrativeTemplate,
            CallTemplate = callTemplate,
            CommandTemplate = commandTemplate,
        };
        var thought = Lane("thinking", "思考", "读取上下文", "main_text", 1, status: "completed");
        var call = Lane("call", "调用", "调用编程 Agent", "programming", 2, status: "completed");
        call.CallAgent = "编程 Agent";
        var command = Lane("command", "工作指令", "运行 git status", "codex", 3, status: "completed");
        command.CommandText = "git status";
        command.ShellLabel = "CMD";

        var thoughtEntry = Assert.Single(WorkChainViewModel.FromMessage(WorkMessage("thought-template", "worked", thought)).Entries);
        var callMessage = WorkMessage("call-template", "worked", call);
        callMessage.WorkAgent = "Spirit";
        var callEntry = Assert.Single(WorkChainViewModel.FromMessage(callMessage).Entries);
        var commandEntry = Assert.Single(WorkChainViewModel.FromMessage(WorkMessage("command-template", "worked", command)).Entries);

        Assert.Same(narrativeTemplate, selector.SelectTemplate(thoughtEntry, null!));
        Assert.Same(callTemplate, selector.SelectTemplate(callEntry, null!));
        Assert.Same(commandTemplate, selector.SelectTemplate(commandEntry, null!));
    }

    [Fact]
    public void AssistantReplyUsesFullReadingLaneAndVisibleSpeakerIdentity()
    {
        var message = new DesktopMessage
        {
            Id = "assistant-reply",
            Role = "assistant",
            Text = "完整回复正文",
            Subtitle = "answer",
        };

        var projected = MessageViewModel.FromMessage(message);

        Assert.Equal(HorizontalAlignment.Stretch, projected.Alignment);
        Assert.True(double.IsNaN(projected.BubbleWidth));
        Assert.True(projected.IsAssistantMessage);
        Assert.Equal(Visibility.Visible, projected.HeaderVisibility);
    }

    [Fact]
    public void CollaborationReasoningLaneShowsEmittedThinkingProcess()
    {
        var step = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "Here's a thinking process with private prompt analysis and internal constraints.",
            RunId = "collab-thread-private",
            SpanId = "collab-thread-private:reasoning",
            Status = "running",
            AgentId = "model_deepseek",
            IsStreamLane = true,
            ReasoningVisibility = "private",
        };

        var projected = WorkStepViewModel.FromStep(step);

        Assert.Equal(step.Detail, projected.Detail);
    }

    [Fact]
    public void HistoricalMainChatReasoningSpanShowsEmittedThinkingProcess()
    {
        var process = "先读取当前会话，再检查用户目标，最后组织回复。";
        var step = Lane("thinking", "思考", process, "main_text", 10, streamLane: false, status: "completed");
        step.RunId = "desktop-run";
        step.SpanId = "desktop-run:model:main_text:1:reasoning";

        var model = WorkChainViewModel.FromMessage(WorkMessage("main-reasoning", "worked", step));

        Assert.Equal(process, Assert.Single(model.Steps).Detail);
        Assert.True(model.IsExpanded);
    }

    [Fact]
    public void CollaborationCodexReasoningSummaryRemainsVisible()
    {
        var step = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "检查事件协议并确认工具结果是否与调用 ID 配对。",
            RunId = "collab-thread-summary",
            SpanId = "collab-thread-summary:reasoning",
            Status = "running",
            AgentId = "codex",
            IsStreamLane = true,
            ReasoningVisibility = "summary",
        };

        var projected = WorkStepViewModel.FromStep(step);

        Assert.Equal("检查事件协议并确认工具结果是否与调用 ID 配对。", projected.Detail);
    }

    [Fact]
    public void CollaborationLongCodexReasoningSummaryRemainsVisible()
    {
        var summary = string.Concat(Enumerable.Repeat("检查事件协议、追踪事件投影并验证界面状态。", 24));
        Assert.True(summary.Length > 240);
        var step = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = summary,
            RunId = "collab-thread-long-summary",
            SpanId = "collab-thread-long-summary:reasoning",
            Status = "running",
            AgentId = "codex",
            IsStreamLane = true,
            ReasoningVisibility = "summary",
        };

        var projected = WorkStepViewModel.FromStep(step);

        Assert.Equal(summary, projected.Detail);
    }

    [Fact]
    public void CollaborationDeepSeekReasoningProcessRemainsVisible()
    {
        var process = "先确认用户目标，再检查当前会话状态，最后组织可执行的回复。";
        var step = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = process,
            RunId = "collab-thread-deepseek-process",
            SpanId = "collab-thread-deepseek-process:reasoning",
            Status = "running",
            AgentId = "model_deepseek",
            IsStreamLane = true,
            ReasoningVisibility = "process",
        };

        var projected = WorkStepViewModel.FromStep(step);

        Assert.Equal(process, projected.Detail);
    }

    private static DesktopMessage TimelineMessage(string id, string role, string kind = "", double createdAt = 0)
    {
        return new DesktopMessage
        {
            Id = id,
            Role = role,
            Kind = kind,
            CreatedAt = createdAt,
        };
    }

    [Fact]
    public void AtelierTimelineKeepsEveryModelWorkCardWithItsReply()
    {
        var timeline = new List<DesktopMessage>
        {
            TimelineMessage("user", "user", createdAt: 1),
            TimelineMessage("deep-work", "assistant", "work", 2),
            TimelineMessage("deep-reply", "assistant", createdAt: 3),
            TimelineMessage("spirit-work", "assistant", "work", 4),
            TimelineMessage("spirit-reply", "assistant", createdAt: 5),
        };

        var projected = RuntimeController.ProjectAtelierTimeline(timeline);

        Assert.Equal(new[] { "user", "deep-work", "deep-reply", "spirit-work", "spirit-reply" }, projected.Select(message => message.Id));
    }

    [Fact]
    public void StreamSegmentsUsingTheSameSpanStaySeparatedAcrossToolBoundaries()
    {
        var beforeTool = Lane("thinking", "思考", "先检查项目上下文。", "codex", 10, streamLane: true, status: "completed");
        beforeTool.SpanId = "collab-turn:reasoning";
        var command = Lane("command", "执行命令", "git status --short", "codex", 20, status: "completed");
        command.SpanId = "collab-turn:tool:1";
        command.CommandText = "git status --short";
        command.ShellLabel = "PowerShell";
        var afterTool = Lane("thinking", "思考", "根据命令结果继续分析。", "codex", 30, streamLane: true, status: "completed");
        afterTool.SpanId = beforeTool.SpanId;

        var model = WorkChainViewModel.FromMessage(WorkMessage("append-reasoning", "worked", beforeTool, command, afterTool));

        Assert.Equal(3, model.Steps.Count);
        Assert.Equal("先检查项目上下文。", model.Steps[0].Detail);
        Assert.Equal("根据命令结果继续分析。", model.Steps[2].Detail);
    }

    [Fact]
    public void AtelierTimelinePreservesSeparateUserTurns()
    {
        var timeline = new List<DesktopMessage>
        {
            TimelineMessage("user-1", "user", createdAt: 1),
            TimelineMessage("reply-1", "assistant", createdAt: 2),
            TimelineMessage("user-2", "user", createdAt: 3),
            TimelineMessage("work-2", "assistant", "work", 4),
            TimelineMessage("reply-2", "assistant", createdAt: 5),
        };

        var projected = RuntimeController.ProjectAtelierTimeline(timeline);

        Assert.Equal(new[] { "user-1", "reply-1", "user-2", "work-2", "reply-2" }, projected.Select(message => message.Id));
    }

    [Fact]
    public void AtelierTimelineLeavesConfirmationCopyInPermissionGate()
    {
        var narrative = TimelineMessage("spirit-reply", "assistant", createdAt: 2);
        narrative.Text = "原有的编辑室主叙事";
        var confirmation = TimelineMessage("confirmation", "assistant", createdAt: 4);
        confirmation.Text = "这个操作会控制 local_pc 执行 clipboard_write。为安全起见，请先回复“确认执行”或“取消执行”。";
        var timeline = new List<DesktopMessage>
        {
            TimelineMessage("user", "user", createdAt: 1),
            narrative,
            TimelineMessage("work", "system", "work", 3),
            confirmation,
        };

        var projected = RuntimeController.ProjectAtelierTimeline(timeline);

        Assert.Equal(new[] { "user", "work", "spirit-reply" }, projected.Select(message => message.Id));
    }

    [Fact]
    public void AtelierTimelineDoesNotReplaceNarrativeWithCancellationReceipt()
    {
        var narrative = TimelineMessage("spirit-reply", "assistant", createdAt: 2);
        narrative.Text = "原有的编辑室主叙事";
        var cancellation = TimelineMessage("cancel-receipt", "assistant", createdAt: 4);
        cancellation.Text = "已取消 local_pc.clipboard_write。";

        var projected = RuntimeController.ProjectAtelierTimeline(new List<DesktopMessage>
        {
            TimelineMessage("user", "user", createdAt: 1),
            narrative,
            cancellation,
            TimelineMessage("work", "system", "work", 5),
        });

        Assert.Equal(new[] { "user", "work", "spirit-reply" }, projected.Select(message => message.Id));
    }

    private static DesktopMessage WorkMessage(string id, string subtitle, params DesktopWorkStep[] steps)
    {
        return new DesktopMessage
        {
            Id = id,
            Role = "assistant",
            Kind = "work",
            Subtitle = subtitle,
            CreatedAt = 100,
            Steps = steps.ToList(),
        };
    }

    private static DesktopWorkStep Lane(string kind, string title, string detail, string agentId, double createdAt, bool streamLane = false, string status = "")
    {
        return new DesktopWorkStep
        {
            Kind = kind,
            Title = title,
            Detail = detail,
            AgentId = agentId,
            CreatedAt = createdAt,
            Status = status,
            IsStreamLane = streamLane,
            // 协作泳道步骤的真实形态：RunId 带 collab- 前缀（IsVisibleWorkTraceStep 据此放行）。
            RunId = "collab-test-run",
        };
    }

    [Fact]
    public void StreamUpdatesPreserveUserChosenExpansionState()
    {
        var message = WorkMessage("compact-card", "running", Lane("thinking", "思考", "推理中", "main_text", 10));
        message.WorkExpanded = false;
        var model = WorkChainViewModel.FromMessage(message);
        Assert.True(model.IsExpanded);

        model.IsExpanded = true;
        message.WorkExpanded = false;
        model.UpdateFromMessage(message);

        Assert.True(model.IsExpanded);
    }

    [Fact]
    public void MergeStepsReusesVmAfterHeadRemoval()
    {
        // 40 步删头（RemoveRange）后所有步骤位置左移——按位置配对全灭，按身份配对应全部复用。
        var steps = Enumerable.Range(0, 5)
            .Select(index => Lane("thinking", $"步骤{index}", $"内容{index}", "main_text", 10 + index))
            .ToArray();
        var message = WorkMessage("card-1", "running", steps);
        var model = WorkChainViewModel.FromMessage(message);
        var before = model.Steps.ToList();

        message.Steps!.RemoveAt(0);
        model.UpdateFromMessage(message);

        Assert.Equal(4, model.Steps.Count);
        for (var index = 0; index < 4; index++)
        {
            Assert.Same(before[index + 1], model.Steps[index]);
        }
    }

    [Fact]
    public void MergeStepsAdoptsStatusTransitionWithoutRebuildingVm()
    {
        var message = WorkMessage("card-2", "running", Lane("thinking", "思考", "推理中", "main_text", 10, streamLane: true, status: "running"));
        var model = WorkChainViewModel.FromMessage(message);
        var vm = model.Steps[0];
        Assert.Equal("RUNNING", vm.StatusLabel);
        var stepsBefore = model.Steps;

        message.Steps![0].Status = "completed";
        model.UpdateFromMessage(message);

        // VM 同实例（打字机存活）、状态原地跃迁、列表实例已换（Entries 徽章快照重建）。
        Assert.Same(vm, model.Steps[0]);
        Assert.Equal("DONE", vm.StatusLabel);
        Assert.NotSame(stepsBefore, model.Steps);
    }

    [Fact]
    public void MergeStepsKeepsAgentsSeparate()
    {
        // 两个 agent 各有一条"思考"泳道：更新其一不得串到另一个的 VM 上。
        var message = WorkMessage(
            "card-3",
            "running",
            Lane("thinking", "思考", "本地推理", "main_text", 10, streamLane: true),
            Lane("thinking", "思考", "DS推理", "model_deepseek", 11, streamLane: true));
        var model = WorkChainViewModel.FromMessage(message);
        var local = model.Steps[0];
        var remote = model.Steps[1];

        message.Steps![1].Detail = "DS推理继续延长";
        model.UpdateFromMessage(message);

        Assert.Same(local, model.Steps[0]);
        Assert.Same(remote, model.Steps[1]);
        Assert.Equal("本地推理", model.Steps[0].Detail);
    }

    [Fact]
    public void MergeStepsPairsDuplicateTitlesInOrder()
    {
        var message = WorkMessage(
            "card-4",
            "running",
            Lane("command", "执行命令", "ls -la", "codex", 10),
            Lane("command", "执行命令", "git status", "codex", 20));
        var model = WorkChainViewModel.FromMessage(message);
        var first = model.Steps[0];
        var second = model.Steps[1];

        model.UpdateFromMessage(message);

        Assert.Same(first, model.Steps[0]);
        Assert.Same(second, model.Steps[1]);
    }

    [Fact]
    public void StreamLaneReplyStepAnimatesInsteadOfSnapping()
    {
        // "回复"泳道（kind=result → IsBlock）过去 animate 恒 false 整块直贴；IsStreamLane 后应进打字机。
        var message = WorkMessage("card-5", "running", Lane("result", "回复", "你好", "main_text", 10, streamLane: true));
        var model = WorkChainViewModel.FromMessage(message);
        var vm = model.Steps[0];
        Assert.Equal("你好", vm.Detail);

        message.Steps![0].Detail = "你好，这是一段更长的流式回复正文";
        model.UpdateFromMessage(message);

        // timer 不 tick → 未瞬贴全文即证明走了打字机队列。
        Assert.Same(vm, model.Steps[0]);
        Assert.NotEqual("你好，这是一段更长的流式回复正文", vm.Detail);
    }

    [Fact]
    public void StreamLaneKeepsAnimatingAfterCardFinalized()
    {
        // 卡完结（running=false）后收尾批次到达：流式泳道仍走打字机，不因 finalize 瞬贴。
        var message = WorkMessage("card-6", "running", Lane("thinking", "思考", "推理开头", "main_text", 10, streamLane: true));
        var model = WorkChainViewModel.FromMessage(message);
        var vm = model.Steps[0];

        message.Subtitle = "worked";
        message.Steps![0].Detail = "推理开头，加上收尾批次里迟到的完整结论";
        model.UpdateFromMessage(message);

        Assert.Same(vm, model.Steps[0]);
        Assert.NotEqual("推理开头，加上收尾批次里迟到的完整结论", vm.Detail);
    }

    [Fact]
    public void StreamLaneExpediteAfterFinalizeStillDoesNotSnap()
    {
        // 修G：卡完结时 MergeSteps 对流式泳道调 ExpediteTail（deadline 封顶 3.5s 提速收尾），
        // 但 Expedite 只收紧 deadline 不瞬贴——timer 不 tick 时后续批次依旧不得直贴全文。
        var message = WorkMessage("card-6b", "running", Lane("result", "回复", "回复开头", "main_text", 10, streamLane: true));
        var model = WorkChainViewModel.FromMessage(message);
        var vm = model.Steps[0];

        // 完结帧：触发 ExpediteTail。
        message.Subtitle = "worked";
        message.Steps![0].Detail = "回复开头，加上定稿正文第一段";
        model.UpdateFromMessage(message);
        Assert.Same(vm, model.Steps[0]);
        Assert.NotEqual("回复开头，加上定稿正文第一段", vm.Detail);

        // 完结后又一收尾批：Expedite 已生效，仍必须走打字机而不是瞬贴。
        message.Steps![0].Detail = "回复开头，加上定稿正文第一段，以及收尾批里补齐的第二段";
        model.UpdateFromMessage(message);
        Assert.Same(vm, model.Steps[0]);
        Assert.NotEqual("回复开头，加上定稿正文第一段，以及收尾批里补齐的第二段", vm.Detail);
    }

    [Fact]
    public void NonStreamBlockStepSnapsDirectly()
    {
        // 非流式块类步骤（command，IsStreamLane=false）：维持原直贴语义，不进打字机。
        var message = WorkMessage("card-7", "running", Lane("command", "执行命令", "ls", "codex", 10));
        var model = WorkChainViewModel.FromMessage(message);
        var vm = model.Steps[0];

        message.Steps![0].Detail = "ls -la --color";
        model.UpdateFromMessage(message);

        Assert.Same(vm, model.Steps[0]);
        Assert.Equal("ls -la --color", vm.Detail);
    }

    [Fact]
    public void MergeStepsReturnsSameListWhenNothingChanged()
    {
        // 引用去抖契约：内容与顺序都没变时返回旧列表实例，Steps setter 不触发重渲染。
        var message = WorkMessage("card-8", "running", Lane("thinking", "思考", "稳定内容", "main_text", 10, streamLane: true));
        var model = WorkChainViewModel.FromMessage(message);
        var stepsBefore = model.Steps;

        model.UpdateFromMessage(message);

        Assert.Same(stepsBefore, model.Steps);
    }

    [Fact]
    public void StandaloneReplyResultRemainsNarrativeInsteadOfFakeToolGroup()
    {
        var message = WorkMessage(
            "reply-result",
            "worked",
            Lane("result", "回复", "完整回复正文", "main_text", 10, streamLane: true, status: "completed"));

        var model = WorkChainViewModel.FromMessage(message);

        var narrative = Assert.IsType<WorkNarrativeEntryViewModel>(Assert.Single(model.Entries));
        Assert.Equal("回复已生成，正文见下方消息。", narrative.Step.Detail);
    }

    [Fact]
    public void CommandAndOutputFormExpandedRealCommandGroupWhileRunning()
    {
        var command = Lane("command", "执行命令", "git status --short", "codex", 10, status: "running");
        command.CommandText = "git status --short";
        command.ShellLabel = "Shell";
        var message = WorkMessage(
            "command-group",
            "running",
            command,
            Lane("result", "命令输出", "M desktop/App.xaml", "codex", 11, status: "running"));

        var model = WorkChainViewModel.FromMessage(message);

        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        Assert.Equal("命令调用", group.GroupLabel);
        Assert.Equal("Ran command", group.HeaderText);
        Assert.True(group.IsRunning);
        Assert.True(group.IsExpanded);
        Assert.Equal(2, group.Steps.Count);
        var invocation = Assert.Single(group.Invocations);
        Assert.Equal("git status --short", invocation.CommandText);
        Assert.Equal("M desktop/App.xaml", invocation.CommandOutput);
        Assert.Equal("Running", invocation.ResultLabel);
    }

    [Fact]
    public void ConsecutivePowerShellAndCmdCallsShareOneCollapsibleCommandGroup()
    {
        var powershell = Lane("command", "执行命令", "Get-Process", "codex", 10, status: "completed");
        powershell.CommandText = "powershell -NoProfile -Command Get-Process";
        powershell.CommandOutput = "pwsh output";
        powershell.ShellLabel = "PowerShell";
        var cmd = Lane("command", "执行命令", "dir", "codex", 20, status: "completed");
        cmd.CommandText = "cmd /c dir";
        cmd.CommandOutput = "cmd output";
        cmd.ShellLabel = "CMD";

        var model = WorkChainViewModel.FromMessage(WorkMessage("multi-command-group", "worked", powershell, cmd));

        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        Assert.Equal("Ran 2 commands", group.HeaderText);
        Assert.Equal(2, group.Invocations.Count);
        Assert.Equal("PowerShell", group.Invocations[0].ShellLabel);
        Assert.Equal("powershell -NoProfile -Command Get-Process", group.Invocations[0].CommandText);
        Assert.Equal("CMD", group.Invocations[1].ShellLabel);
        Assert.Equal("cmd /c dir", group.Invocations[1].CommandText);

        group.IsExpanded = false;
        Assert.Equal(Visibility.Collapsed, group.StepsListVisibility);
        group.IsExpanded = true;
        Assert.Equal(Visibility.Visible, group.StepsListVisibility);
    }

    [Fact]
    public void FinalizedWorkPromotesUnclosedRunningStepsToCompleted()
    {
        var message = WorkMessage(
            "completed-frontier",
            "worked",
            Lane("thinking", "思考", "读取运行上下文", "agent_cluster", 10, status: "started"));
        message.Steps![0].SpanId = "desktop-test-run:agent:agent_cluster";

        var model = WorkChainViewModel.FromMessage(message);

        var step = Assert.Single(model.Steps);
        Assert.Equal("completed", step.StateBucket);
        Assert.True(step.IsCompleted);
    }

    [Fact]
    public void LaterCompletedStepClosesEarlierStaleRunningFrontier()
    {
        var stale = Lane("thinking", "思考", "agent 读取上下文", "agent_cluster", 10, status: "started");
        stale.SpanId = "desktop-test-run:agent:agent_cluster";
        var route = Lane("thinking", "思考", "已选择 general 路由", "agent_cluster", 20, status: "completed");
        route.SpanId = "desktop-test-run:route:general";
        var message = WorkMessage("stale-frontier", "running", stale, route);

        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal(new[] { "completed", "completed" }, model.Entries.Select(entry => entry.State));
        Assert.Empty(model.Entries.Where(entry => entry.IsRunning));
    }

    [Fact]
    public void MainAgentStructuredModelSpanRendersAsInvocationCard()
    {
        var agentPreparation = Lane("thinking", "工作指令", "开始调用文本模型生成回复。", "agent_cluster", 10, status: "completed");
        agentPreparation.SpanId = "desktop-test-run:agent:general";
        agentPreparation.RunId = "desktop-test-run";
        var modelCall = Lane("call", "调用", "正在调用语言模型。", "main_text", 20, status: "completed");
        modelCall.CallAgent = "Spirit";
        modelCall.CallProvider = "openai_compatible";
        modelCall.CallModel = "qwen3";
        modelCall.SpanId = "desktop-test-run:model:main_text:1";
        modelCall.RunId = "desktop-test-run";

        var model = WorkChainViewModel.FromMessage(WorkMessage("model-truth", "worked", agentPreparation, modelCall));

        Assert.Equal("开始调用文本模型生成回复。", model.Steps[0].Detail);
        Assert.Equal("正在调用语言模型。", model.Steps[1].Detail);
        Assert.Equal("Agent / 模型调用", model.ChainLabel);
        Assert.Contains(model.Entries, entry => entry is WorkCallGroupViewModel);
    }

    [Fact]
    public void MainChatKeepsConcreteContextAndRouteProcessText()
    {
        var context = Lane("thinking", "思考", "读取当前会话上下文和桌面状态。", "runtime", 10, status: "completed");
        context.RunId = "desktop-test-run";
        context.SpanId = "desktop-test-run:scheduler:context";
        var route = Lane("thinking", "思考", "模型选择普通回答路由；原因：未命中专业 Agent。", "agent_cluster", 20, status: "completed");
        route.RunId = "desktop-test-run";
        route.SpanId = "desktop-test-run:route:general";
        route.Key = "route:general|general|gpu_heavy";

        var model = WorkChainViewModel.FromMessage(WorkMessage("main-process", "worked", context, route));

        Assert.Equal(context.Detail, model.Steps[0].Detail);
        Assert.Equal(route.Detail, model.Steps[1].Detail);
    }

    [Fact]
    public void PrimaryRuntimeDispatchWithModelTargetIncludesInvocationCard()
    {
        var dispatch = Lane("thinking", "工作指令", "提交到 agent 编排器。", "runtime", 10, status: "completed");
        dispatch.RunId = "desktop-test-run";
        dispatch.SpanId = "desktop-test-run:scheduler:dispatch";
        var modelCall = Lane("call", "调用", "回复生成完成。", "agent_cluster", 20, status: "completed");
        modelCall.CallAgent = "Spirit";
        modelCall.CallModel = "qwen3";
        modelCall.RunId = "desktop-test-run";
        modelCall.SpanId = "desktop-test-run:model:main_text:1";

        var model = WorkChainViewModel.FromMessage(WorkMessage("primary-model-dispatch", "worked", dispatch, modelCall));

        Assert.Equal("Agent / 模型调用", model.ChainLabel);
        Assert.Contains(model.Entries, entry => entry is WorkCallGroupViewModel);
    }

    [Fact]
    public void SpiritExplicitExternalDelegationUsesInvocationCard()
    {
        var delegation = Lane("call", "调用", "正在把任务交给 Deepseek。", "main_text", 10, status: "completed");
        delegation.CallAgent = "模型 Deepseek";
        delegation.CallModel = "deepseek-v4";
        var message = WorkMessage("spirit-delegation", "worked", delegation);
        message.WorkAgent = "Spirit";

        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("Agent / 模型调用", model.ChainLabel);
        Assert.IsType<WorkCallGroupViewModel>(Assert.Single(model.Entries));
    }

    [Fact]
    public void CalledModelOwnWorkRemainsThinkingChain()
    {
        var delegated = Lane("call", "调用", "正在调用模型 API。", "model_deepseek", 10, status: "completed");
        delegated.CallAgent = "编程 Agent";
        delegated.CallModel = "deepseek-v4";
        var message = WorkMessage("called-model-work", "worked", delegated);
        message.WorkAgent = "模型 Deepseek";

        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("思考链", model.ChainLabel);
        Assert.IsType<WorkNarrativeEntryViewModel>(Assert.Single(model.Entries));
    }

    [Theory]
    [InlineData("main_text")]
    [InlineData("主 Agent")]
    [InlineData("Spirit")]
    public void MainAgentTargetsCreateInvocationCards(string target)
    {
        var modelCall = Lane("call", "调用", "主 Agent 正在整理回复。", "main_text", 20, status: "completed");
        modelCall.CallAgent = target;
        modelCall.CallProvider = "openai_compatible";
        modelCall.CallModel = "qwen3";

        var model = WorkChainViewModel.FromMessage(WorkMessage("main-call-target", "worked", modelCall));

        Assert.Equal("Agent / 模型调用", model.ChainLabel);
        Assert.IsType<WorkCallGroupViewModel>(Assert.Single(model.Entries));
    }

    [Fact]
    public void ExternalAgentCallRendersTargetAndModelWithThinkingSteps()
    {
        var accepted = Lane("call", "接收任务", "已接收协作请求。", "programming", 10, status: "completed");
        accepted.CallAgent = "编程 Agent";
        accepted.CallProvider = "openai";
        accepted.CallModel = "gpt-5-codex";
        var routed = Lane("call", "交给模型", "Agent Route Bus 已接收。", "programming", 20, status: "completed");
        routed.CallAgent = accepted.CallAgent;
        routed.CallProvider = accepted.CallProvider;
        routed.CallModel = accepted.CallModel;

        var message = WorkMessage("external-call", "worked", accepted, routed);
        message.WorkAgent = "Spirit";
        var model = WorkChainViewModel.FromMessage(message);

        var call = Assert.IsType<WorkCallGroupViewModel>(Assert.Single(model.Entries));
        Assert.Equal("Agent / 模型调用", model.ChainLabel);
        Assert.Equal("调用 编程 Agent", call.HeaderText);
        Assert.Equal("openai · gpt-5-codex", call.TargetText);
        Assert.Equal(2, call.Steps.Count);
        Assert.True(call.IsExpanded);
    }

    [Fact]
    public void UnstructuredModelCallCopyDoesNotCreateModelInvocationLane()
    {
        var legacy = Lane("thinking", "工作指令", "开始调用文本模型生成回复。", "", 10, status: "completed");
        legacy.RunId = "";

        var model = WorkChainViewModel.FromMessage(WorkMessage("legacy-model-copy", "worked", legacy));

        Assert.Empty(model.Steps);
    }

    [Fact]
    public void CallKindWithoutStructuredExternalTargetRemainsThinkingNarrative()
    {
        var malformed = Lane("call", "调用", "准备处理请求。", "main_text", 10, status: "completed");

        var model = WorkChainViewModel.FromMessage(WorkMessage("call-without-target", "worked", malformed));

        Assert.Equal("思考链", model.ChainLabel);
        Assert.IsType<WorkNarrativeEntryViewModel>(Assert.Single(model.Entries));
    }

    [Fact]
    public void ExecutionCommandSuppressesLegacyToolNameAndKeepsCmdPreview()
    {
        var execution = Lane("command", "工作指令", "cmd · 完成。", "agent_cluster", 10, status: "completed");
        execution.SpanId = "desktop-test-run:execution:local_pc:launch_app";
        execution.CommandText = "cmd";
        execution.ShellLabel = "CMD";
        var legacyTool = Lane("command", "工作指令", "调用工具：app.launch", "", 11);
        legacyTool.Key = "tool:app.launch";
        legacyTool.RunId = "";
        var model = WorkChainViewModel.FromMessage(WorkMessage("cmd-group", "worked", execution, legacyTool));

        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        Assert.Single(group.Steps);
        Assert.Equal("Ran command", group.HeaderText);
        Assert.Equal("cmd", Assert.Single(group.Invocations).CommandText);
        Assert.Equal("CMD", Assert.Single(group.Invocations).ShellLabel);
    }

    [Fact]
    public void PersistedCmdSummaryWithoutStructuredInvocationRemainsNarrative()
    {
        var execution = Lane("command", "工作指令", "cmd · 完成。", "agent_cluster", 10, status: "completed");
        execution.SpanId = "desktop-test-run:execution:local_pc:launch_app";

        var model = WorkChainViewModel.FromMessage(WorkMessage("legacy-cmd-card", "worked", execution));

        Assert.IsType<WorkNarrativeEntryViewModel>(Assert.Single(model.Entries));
        Assert.DoesNotContain(model.Entries, entry => entry is WorkCommandGroupViewModel);
    }

    [Fact]
    public void StructuredToolCallUsesToolCardWithoutShellPrompt()
    {
        var tool = Lane("command", "工作指令", "调用工具：app.launch", "agent_cluster", 10, status: "completed");
        tool.SpanId = "desktop-test-run:tool:app.launch";
        tool.CommandText = "app.launch";
        tool.CommandOutput = "工具执行完成";
        tool.ShellLabel = "Tool";

        var model = WorkChainViewModel.FromMessage(WorkMessage("tool-card", "worked", tool));

        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        Assert.Equal("Called tool", group.HeaderText);
        Assert.Equal("工具调用", group.GroupLabel);
        var invocation = Assert.Single(group.Invocations);
        Assert.True(invocation.IsTool);
        Assert.Equal("\u203a ", invocation.PromptText);
    }

    [Fact]
    public void GenericExecutionSummaryCannotOverwriteConcreteCmdProjection()
    {
        var started = Lane("command", "工作指令", "运行 cmd。", "agent_cluster", 10, status: "started");
        started.RunId = "desktop-test-run";
        started.SpanId = "desktop-test-run:execution:local_pc:launch_app";
        started.CommandText = "cmd";
        started.ShellLabel = "CMD";
        var completed = Lane("command", "工作指令", "cmd · 完成。", "agent_cluster", 20, status: "completed");
        completed.RunId = "desktop-test-run";
        completed.SpanId = started.SpanId;
        completed.CommandText = "cmd";
        completed.CommandOutput = "已由 local_pc 执行打开命令提示符。";
        completed.ShellLabel = "CMD";
        var genericSummary = Lane("result", "工作指令", "执行桌面指令 local_pc.launch_app，结果：完成。", "executor_local_pc", 30, status: "completed");
        genericSummary.RunId = "desktop-test-run";
        genericSummary.SpanId = started.SpanId;

        var model = WorkChainViewModel.FromMessage(WorkMessage("cmd-summary", "worked", started, completed, genericSummary));

        var group = Assert.IsType<WorkCommandGroupViewModel>(Assert.Single(model.Entries));
        Assert.Equal("Ran command", group.HeaderText);
        Assert.DoesNotContain("local_pc.launch_app", group.HeaderText);
        Assert.Equal("completed", group.State);
        var invocation = Assert.Single(group.Invocations);
        Assert.Equal("cmd", invocation.CommandText);
        Assert.Equal("已由 local_pc 执行打开命令提示符。", invocation.CommandOutput);
        Assert.Equal("Success", invocation.ResultLabel);
    }

    [Fact]
    public void ExecutionTimelineUsesCompletedCurrentPendingFrontier()
    {
        var message = WorkMessage(
            "state-frontier",
            "running",
            Lane("thinking", "读取上下文", "完成", "main_text", 10, status: "completed"),
            Lane("thinking", "调用模型", "流式生成中", "main_text", 20, status: "running"),
            Lane("thinking", "整理回复", "等待", "main_text", 30, status: "queued"));

        var model = WorkChainViewModel.FromMessage(message);
        var entries = model.Entries.Cast<WorkNarrativeEntryViewModel>().ToList();

        Assert.Equal(new[] { "completed", "running", "pending" }, entries.Select(entry => entry.State));
        Assert.Equal(Visibility.Collapsed, entries[0].IncomingConnectorVisibility);
        Assert.Equal(Visibility.Visible, entries[1].IncomingConnectorVisibility);
        Assert.Equal(Visibility.Visible, entries[2].IncomingConnectorVisibility);
        Assert.Equal(Visibility.Visible, entries[0].ConnectorPulseVisibility);
        Assert.Equal(Visibility.Collapsed, entries[1].ConnectorPulseVisibility);
        Assert.Equal(Visibility.Collapsed, entries[2].ConnectorVisibility);
        Assert.True(entries[0].IsCompleted);
        Assert.True(entries[1].IsRunning);
        Assert.True(entries[2].IsPending);
    }

    [Fact]
    public void RunningChainHasOnlyOneCurrentFrontierAcrossCallThinkingAndReply()
    {
        var message = WorkMessage(
            "single-frontier",
            "running",
            Lane("thinking", "调用", "正在调用模型 API", "model_deepseek", 10, status: "stream"),
            Lane("thinking", "思考", "正在分析请求", "model_deepseek", 20, streamLane: true, status: "stream"),
            Lane("result", "回复", "正在生成正文", "model_deepseek", 30, streamLane: true, status: "stream"));

        var model = WorkChainViewModel.FromMessage(message);
        var entries = model.Entries.Cast<WorkNarrativeEntryViewModel>().ToList();

        Assert.Equal(new[] { "completed", "completed", "running" }, entries.Select(entry => entry.State));
        Assert.Single(entries.Where(entry => entry.IsRunning));
        Assert.Equal(Visibility.Visible, entries[1].ConnectorPulseVisibility);
    }

    [Fact]
    public void StructuredRouteSuppressesOnlyItsLegacyPendingDuplicate()
    {
        var structured = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "模型选择 普通回答 路由。",
            Key = "route:general|general|gpu_heavy",
            CreatedAt = 10,
            Seq = 9,
            RunId = "desktop-test-run",
            EventId = "desktop-test-run:000009",
            SpanId = "desktop-test-run:route:general",
            Status = "completed",
        };
        var legacy = new DesktopWorkStep
        {
            Kind = "thinking",
            Title = "思考",
            Detail = "模型选择了普通回答路径。",
            Key = structured.Key,
            CreatedAt = 11,
        };

        var upgraded = WorkChainViewModel.FromMessage(WorkMessage("route-upgraded", "worked", structured, legacy));
        var onlyRoute = Assert.Single(upgraded.Entries);
        Assert.Equal("completed", onlyRoute.State);

        var legacyOnly = WorkChainViewModel.FromMessage(WorkMessage("route-legacy", "running", legacy));
        Assert.Single(legacyOnly.Entries);
        Assert.Equal("pending", legacyOnly.Entries[0].State);
    }

    // ── 阶段步进条（THINKING→READING→EDITING→DONE）投影契约 ──
    private static string PhaseState(WorkChainViewModel model, string label) =>
        model.Phases.Single(phase => phase.Label == label).State;

    [Fact]
    public void PhasesHiddenWhenNoSteps()
    {
        // 无过程步骤 → 步进条整条隐藏，不占位。
        var message = WorkMessage("phase-empty", "running");
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal(Visibility.Collapsed, model.PhasesVisibility);
        Assert.Empty(model.Phases);
    }

    [Fact]
    public void PhasesThinkingActiveWhileRunningThinkOnly()
    {
        // 仅有思考泳道且运行中：只显示已到达的 THINKING。
        var message = WorkMessage("phase-think", "running", Lane("thinking", "思考", "推理中", "main_text", 10, streamLane: true, status: "running"));
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal(Visibility.Visible, model.PhasesVisibility);
        Assert.Single(model.Phases);
        Assert.Equal("active", PhaseState(model, "THINKING"));
        Assert.DoesNotContain(model.Phases, phase => phase.Label is "READING" or "EDITING" or "DONE");
    }

    [Fact]
    public void PhasesReadingActiveOnCommandStep()
    {
        // 命令/输出步骤把进展推进到 READING：THINKING done、READING active。
        var message = WorkMessage(
            "phase-read",
            "running",
            Lane("thinking", "思考", "先想", "main_text", 10, streamLane: true),
            Lane("command", "执行命令", "ls -la", "codex", 20, status: "running"));
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("done", PhaseState(model, "THINKING"));
        Assert.Equal("active", PhaseState(model, "READING"));
        Assert.DoesNotContain(model.Phases, phase => phase.Label is "EDITING" or "DONE");
    }

    [Fact]
    public void PhasesEditingActiveOnDiffStep()
    {
        // diff 步骤把进展推进到 EDITING：其前两段 done、EDITING active。
        var message = WorkMessage(
            "phase-edit",
            "running",
            Lane("thinking", "思考", "先想", "main_text", 10, streamLane: true),
            Lane("diff", "编辑文件", "+1 -0", "codex", 20, status: "running"));
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("done", PhaseState(model, "THINKING"));
        Assert.Equal("done", PhaseState(model, "READING"));
        Assert.Equal("active", PhaseState(model, "EDITING"));
        Assert.DoesNotContain(model.Phases, phase => phase.Label == "DONE");
    }

    [Fact]
    public void PhasesAllDoneWhenRunSucceeds()
    {
        // 整条 run 成功终态：四段全 done（DONE 亮起）。
        var message = WorkMessage(
            "phase-done",
            "worked",
            Lane("thinking", "思考", "想完", "main_text", 10, streamLane: true, status: "completed"),
            new DesktopWorkStep { Kind = "thinking", Title = "完成", Detail = "Done", AgentId = "main_text", CreatedAt = 30, Status = "completed", IsTerminal = true, RunId = "collab-test-run" });
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("done", PhaseState(model, "THINKING"));
        Assert.Equal("done", PhaseState(model, "DONE"));
    }

    [Fact]
    public void PhasesDoneNotForcedWhenRunFails()
    {
        // 失败终态：不得把 DONE 段强点亮成 done（保留失败前的最深进展语义）。
        var message = WorkMessage(
            "phase-fail",
            "worked",
            Lane("thinking", "思考", "想", "main_text", 10, streamLane: true),
            new DesktopWorkStep { Kind = "thinking", Title = "失败", Detail = "Request failed", AgentId = "main_text", CreatedAt = 30, Status = "failed", IsTerminal = true, RunId = "collab-test-run" });
        var model = WorkChainViewModel.FromMessage(message);

        Assert.DoesNotContain(model.Phases, phase => phase.Label == "DONE");
    }

    [Fact]
    public void CancelledWorkOverridesLateCompletedTerminalState()
    {
        var message = WorkMessage(
            "cancelled-run",
            "cancelled",
            new DesktopWorkStep
            {
                Kind = "thinking",
                Title = "思考",
                Detail = "迟到的完成事件",
                AgentId = "main_text",
                CreatedAt = 30,
                Status = "completed",
                IsTerminal = true,
                RunId = "collab-test-run",
            });

        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal("cancelled", model.Status);
        Assert.Equal("已取消", model.StatusLabel);
        Assert.StartsWith("已停止", model.Meta);
    }

    [Fact]
    public void PhasesConnectorHiddenOnFirstSegment()
    {
        // 首段 THINKING 前不画连接线，其余段都画。
        var message = WorkMessage("phase-connector", "running", Lane("thinking", "思考", "推理", "main_text", 10, streamLane: true, status: "running"));
        var model = WorkChainViewModel.FromMessage(message);

        Assert.Equal(Visibility.Collapsed, model.Phases[0].ConnectorVisibility);
        Assert.All(model.Phases.Skip(1), phase => Assert.Equal(Visibility.Visible, phase.ConnectorVisibility));
    }
}
