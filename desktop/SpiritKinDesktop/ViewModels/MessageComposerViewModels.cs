using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public abstract class ChatTimelineItemViewModel : INotifyPropertyChanged
{
    protected ChatTimelineItemViewModel(string id)
    {
        Id = id;
    }

    public string Id { get; }
    public abstract Thickness Margin { get; }
    public event PropertyChangedEventHandler? PropertyChanged;

    protected void RaisePropertyChanged(string propertyName)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    protected void SetValue<T>(ref T field, T value, string propertyName)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }
        field = value;
        RaisePropertyChanged(propertyName);
    }
}

public sealed class TopicAnchorViewModel
{
    private static readonly Brush UserDotBrush = new SolidColorBrush(Color.FromRgb(0x02, 0x50, 0xCC));
    // 模型定稿绿点对齐 FantasySuccessBrush（#16A34A），与工作卡完成态同色。
    private static readonly Brush ModelDotBrush = new SolidColorBrush(Color.FromRgb(0x16, 0xA3, 0x4A));

    static TopicAnchorViewModel()
    {
        UserDotBrush.Freeze();
        ModelDotBrush.Freeze();
    }

    public TopicAnchorViewModel(string id, string preview, bool isUser)
    {
        Id = id;
        Preview = preview;
        IsUser = isUser;
    }

    public string Id { get; }
    public string Preview { get; }
    public bool IsUser { get; }
    public Brush DotBrush => IsUser ? UserDotBrush : ModelDotBrush;
}

public sealed class MessageViewModel : ChatTimelineItemViewModel
{
    private string _editText;
    private string _text;
    private string _meta;
    private string _durationText;
    private Visibility _textVisibility;
    private Visibility _actionVisibility;
    private Visibility _editPanelVisibility;
    private bool _isEditing;
    private bool _isLongText;
    private bool _isTextExpanded;
    private readonly bool _isEditorialAssistant;
    private readonly bool _isUser;

    private MessageViewModel(
        string id,
        string text,
        string meta,
        string durationText,
        HorizontalAlignment alignment,
        string background,
        string border,
        Thickness borderThickness,
        Thickness padding,
        Thickness margin,
        double maxWidth,
        double textSize,
        double lineHeight,
        Brush textBrush,
        Brush headerBrush,
        Visibility headerVisibility,
        Visibility actionVisibility,
        Visibility copyVisibility,
        Visibility editVisibility,
        Visibility forkVisibility,
        Visibility textVisibility,
        Visibility workVisibility,
        CornerRadius cornerRadius,
        string copyLabel,
        string editLabel,
        string forkLabel,
        double actionButtonWidth,
        bool hasBubbleShadow,
        bool isEditing)
        : base(id)
    {
        _text = text;
        FullText = text ?? "";
        _editText = text ?? "";
        _meta = meta;
        _durationText = durationText;
        Alignment = alignment;
        Background = Solid(background);
        BorderBrush = Solid(border);
        BorderThickness = borderThickness;
        Padding = padding;
        Margin = margin;
        MaxWidth = maxWidth;
        TextSize = textSize;
        LineHeight = lineHeight;
        TextBrush = textBrush;
        HeaderBrush = headerBrush;
        HeaderVisibility = headerVisibility;
        _actionVisibility = actionVisibility;
        CopyVisibility = copyVisibility;
        EditVisibility = editVisibility;
        ForkVisibility = forkVisibility;
        _textVisibility = textVisibility;
        WorkVisibility = workVisibility;
        BubbleVisibility = workVisibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;
        CornerRadius = cornerRadius;
        CopyLabel = copyLabel;
        EditLabel = editLabel;
        ForkLabel = forkLabel;
        ActionButtonWidth = actionButtonWidth;
        // 纯展示：助手定稿气泡带 SoftShadow（与资源字典 SoftShadow 同参数），工作卡/系统条不带。
        BubbleEffect = hasBubbleShadow && BubbleVisibility == Visibility.Visible
            ? CreateSoftShadow()
            : null;
        _isEditing = isEditing;
        _isEditorialAssistant = forkVisibility == Visibility.Visible;
        _isUser = editVisibility == Visibility.Visible && forkVisibility == Visibility.Collapsed;
        _isLongText = _isEditorialAssistant && FullText.Length > 220;
        _editPanelVisibility = isEditing ? Visibility.Visible : Visibility.Collapsed;
        if (isEditing)
        {
            _textVisibility = Visibility.Collapsed;
            _actionVisibility = Visibility.Collapsed;
        }
    }

    public static MessageViewModel FromMessage(DesktopMessage message, bool isEditing = false)
    {
        var role = (message.Role ?? "").Trim().ToLowerInvariant();
        var kind = (message.Kind ?? "").Trim().ToLowerInvariant();
        var created = FormatTime(message.CreatedAt);
        if (role == "user")
        {
            // The outer reading lane owns responsive width and the stable scrollbar gutter.
            return new MessageViewModel(
                message.Id,
                message.Text,
                "User",
                "",
                HorizontalAlignment.Right,
                "Transparent",
                "Transparent",
                new Thickness(0),
                new Thickness(0),
                new Thickness(0, -6, 0, 31),
                double.PositiveInfinity,
                15,
                27,
                Themed("FantasyTextBrush", "#F0EAE4"),
                Themed("FantasyMutedBrush", "#ACA39B"),
                Visibility.Visible,
                Visibility.Visible,
                Visibility.Visible,
                Visibility.Visible,
                Visibility.Collapsed,
                Visibility.Visible,
                Visibility.Collapsed,
                new CornerRadius(0),
                "⧉",
                "✎",
                "",
                24,
                false,
                isEditing);
        }

        if (role == "system" || kind is "changes" or "command")
        {
            var label = kind switch
            {
                "changes" => "工作区变更",
                "command" => message.Text,
                _ => "系统",
            };
            // 工具流/系统条：脚注风格，faint 等宽小字，居中栏内左侧铜色左轨靠模板呈现。
            return new MessageViewModel(
                message.Id,
                kind == "command" ? "" : message.Text,
                label,
                "",
                HorizontalAlignment.Center,
                "Transparent",
                "Transparent",
                new Thickness(0),
                new Thickness(0),
                new Thickness(0, 0, 0, 18),
                double.PositiveInfinity,
                12,
                20,
                Themed("FantasyFaintTextBrush", "#8B837B"),
                Themed("FantasyMutedBrush", "#ACA39B"),
                Visibility.Visible,
                Visibility.Collapsed,
                Visibility.Visible,
                Visibility.Collapsed,
                Visibility.Collapsed,
                string.IsNullOrWhiteSpace(message.Text) || kind == "command" ? Visibility.Collapsed : Visibility.Visible,
                Visibility.Collapsed,
                new CornerRadius(0),
                "⧉",
                "",
                "",
                24,
                false,
                false);
        }

        // 发言人标识：协作回复的 Subtitle 携带 agent 展示名；状态类字面量不当发言人。
        var speaker = (message.Subtitle ?? "").Trim();
        var speakerIsStatus = speaker.Length == 0
            || speaker.Equals("running", StringComparison.OrdinalIgnoreCase)
            || speaker.Equals("completed", StringComparison.OrdinalIgnoreCase)
            || speaker.Equals("failed", StringComparison.OrdinalIgnoreCase)
            || speaker.Equals("answer", StringComparison.OrdinalIgnoreCase)
            || speaker.Equals("question", StringComparison.OrdinalIgnoreCase)
            || speaker.Equals("collaboration_message", StringComparison.OrdinalIgnoreCase);
        // 助手定稿：编辑室正文风格。去气泡（透明底/无边框/无投影/无圆角），发言人名铜色标签，
        // 正文 15px/1.78 行高；宽度由外层响应式阅读位统一约束。
        return new MessageViewModel(
            message.Id,
            message.Text,
            speakerIsStatus ? "Spirit" : speaker,
            "",
            HorizontalAlignment.Stretch,
            "Transparent",
            "Transparent",
            new Thickness(0),
            new Thickness(0),
            new Thickness(0, 0, 0, 12),
            double.PositiveInfinity,
            15,
            27,
            Themed("FantasyTextBrush", "#F0EAE4"),
            Themed("FantasyCopperBrush", "#E99541"),
            Visibility.Visible,
            Visibility.Collapsed,
            Visibility.Visible,
            Visibility.Collapsed,
            Visibility.Visible,
            Visibility.Visible,
            Visibility.Collapsed,
            new CornerRadius(0),
            "⧉",
            "",
            "↳",
            28,
            false,
            false);
    }

    public string Text
    {
        get => _text;
        private set
        {
            if (string.Equals(_text, value, StringComparison.Ordinal))
            {
                return;
            }
            _text = value;
            RaisePropertyChanged(nameof(Text));
        }
    }
    // 权威全文：打字机揭示期间 Text 是动画中间态（RevealFromEmpty 甚至先清空再逐字放），
    // 锚点/搜索等"读内容"的消费方一律用 FullText，否则正在揭示的消息会被当成空文本漏掉。
    public string FullText { get; private set; } = "";
    public string EditText
    {
        get => _editText;
        set
        {
            if (_editText == value)
            {
                return;
            }
            _editText = value;
            RaisePropertyChanged(nameof(EditText));
        }
    }
    public string Meta
    {
        get => _meta;
        private set => SetValue(ref _meta, value, nameof(Meta));
    }
    public string DurationText
    {
        get => _durationText;
        private set => SetValue(ref _durationText, value, nameof(DurationText));
    }
    public HorizontalAlignment Alignment { get; }
    public Brush Background { get; }
    public Brush BorderBrush { get; }
    public Thickness BorderThickness { get; }
    public Thickness Padding { get; }
    public override Thickness Margin { get; }
    public double MaxWidth { get; }
    // Width must stay Auto. Binding PositiveInfinity into FrameworkElement.Width is
    // invalid and made assistant replies shrink to their text width while centered.
    public double BubbleWidth => double.NaN;
    public double TextSize { get; }
    public double LineHeight { get; }
    public Brush TextBrush { get; }
    public Brush HeaderBrush { get; }
    public bool IsUserMessage => _isUser;
    public bool IsAssistantMessage => _isEditorialAssistant;
    public bool IsSystemMessage => !_isUser && !_isEditorialAssistant;
    public Visibility HeaderVisibility { get; }
    public Visibility ActionVisibility
    {
        get => _actionVisibility;
        private set => SetValue(ref _actionVisibility, value, nameof(ActionVisibility));
    }
    public Visibility CopyVisibility { get; }
    public Visibility EditVisibility { get; }
    public Visibility ForkVisibility { get; }
    public Visibility TextVisibility
    {
        get => _textVisibility;
        private set => SetValue(ref _textVisibility, value, nameof(TextVisibility));
    }
    public Visibility WorkVisibility { get; }
    public Visibility BubbleVisibility { get; }
    public Visibility EditPanelVisibility
    {
        get => _editPanelVisibility;
        private set => SetValue(ref _editPanelVisibility, value, nameof(EditPanelVisibility));
    }
    public CornerRadius CornerRadius { get; }
    // 纯展示：气泡投影（助手定稿卡带 SoftShadow，用户/系统条不带）。
    public Effect? BubbleEffect { get; }
    public string CopyLabel { get; }
    public string EditLabel { get; }
    public string ForkLabel { get; }
    public double ActionButtonWidth { get; }
    public bool IsEditing
    {
        get => _isEditing;
        private set => SetValue(ref _isEditing, value, nameof(IsEditing));
    }
    public Visibility ExpandTextVisibility => _isLongText ? Visibility.Visible : Visibility.Collapsed;
    public double TextMaxHeight => _isUser ? 27 : _isTextExpanded || !_isLongText ? double.PositiveInfinity : 54;
    public string ExpandTextLabel => _isTextExpanded ? "收起全文" : "展开全文";
    public HorizontalAlignment HeaderAlignment => HorizontalAlignment.Stretch;
    public TextAlignment MessageTextAlignment => TextAlignment.Left;

    public void ToggleTextExpanded()
    {
        if (!_isLongText)
        {
            return;
        }
        _isTextExpanded = !_isTextExpanded;
        RaisePropertyChanged(nameof(TextMaxHeight));
        RaisePropertyChanged(nameof(ExpandTextLabel));
    }

    public void UpdateFromMessage(DesktopMessage message, bool isEditing = false)
    {
        var updated = FromMessage(message, isEditing);
        // 流式草稿气泡走打字机：批次文本到达节奏实测 1.5-4s 一批，一次性贴上会呈"块状弹出"。
        // 自适应配速：按批次到达间隔（EMA）把每批新增字符匀速摊满整个批间隔，观感即逐字线性，
        // 不再是"0.5s 快放追平 + 干等下一批"。
        // 判定条件：draft kind + 副标题仍是"生成中"（定稿投影会改写副标题为纯 agent 名 → 立即快照全文）。
        var normalizedDraftKind = (message.Kind ?? "").Trim();
        var streamingDraft = (string.Equals(normalizedDraftKind, "collaboration_stream_draft", StringComparison.OrdinalIgnoreCase)
                || string.Equals(normalizedDraftKind, "assistant_stream_draft", StringComparison.OrdinalIgnoreCase))
            && (message.Subtitle ?? "").Contains("生成中", StringComparison.Ordinal)
            && !(message.Text ?? "").Contains("〔生成中断〕", StringComparison.Ordinal);
        if (string.Equals(normalizedDraftKind, "assistant_stream_draft", StringComparison.OrdinalIgnoreCase))
        {
            streamingDraft = string.Equals((message.Subtitle ?? "").Trim(), "running", StringComparison.OrdinalIgnoreCase);
        }
        var target = updated.Text ?? "";
        FullText = target;
        // 助手类气泡（用户/系统/命令/变更除外）：文本从空被原地填充时同样走揭示动画，
        // 覆盖"回复先以占位消息落地、随后填充全文"的管线。
        var role = (message.Role ?? "").Trim().ToLowerInvariant();
        var updateKind = (message.Kind ?? "").Trim().ToLowerInvariant();
        var assistantBubble = role is not ("user" or "system") && updateKind is not ("changes" or "command" or "work");
        var nextIsLongText = assistantBubble && target.Length > 220;
        if (_isLongText != nextIsLongText)
        {
            _isLongText = nextIsLongText;
            if (!_isLongText)
            {
                _isTextExpanded = false;
            }
            RaisePropertyChanged(nameof(ExpandTextVisibility));
            RaisePropertyChanged(nameof(TextMaxHeight));
            RaisePropertyChanged(nameof(ExpandTextLabel));
        }
        if (streamingDraft
            && target.Length > (_text ?? "").Length
            && target.StartsWith(_text ?? "", StringComparison.Ordinal))
        {
            (_typewriter ??= new AdaptiveTypewriter(text => Text = text)).Push(target, _text ?? "");
        }
        else if (_typewriter?.IsActiveFor(target) == true)
        {
            // 新到气泡的揭示动画进行中且目标未变（本次刷新由其他消息的事件触发）：让动画继续跑，
            // 否则每次渲染都会被直贴打断，打字机永远只能显示一帧。
        }
        else if (assistantBubble && string.IsNullOrEmpty(_text) && target.Length > 0 && !isEditing)
        {
            (_typewriter ??= new AdaptiveTypewriter(text => Text = text)).Reveal(target, "", RevealDurationMs(target.Length));
        }
        else
        {
            _typewriter?.Stop();
            Text = target;
        }
        Meta = updated.Meta;
        DurationText = updated.DurationText;
        TextVisibility = updated.TextVisibility;
        ActionVisibility = updated.ActionVisibility;
        EditPanelVisibility = updated.EditPanelVisibility;
        IsEditing = updated.IsEditing;
    }

    private AdaptiveTypewriter? _typewriter;

    // 主聊天等非流式管线的回复是整条一次性到达（没有批次节奏可依），新气泡从空白按固定时长
    // 逐字揭示，与协作流式打字机同一观感。速度尽量快（用户要求不人为拖慢）：约 12ms/字，
    // 长文封顶 3.5s。只对"本轮新出现"的气泡调用，历史加载/会话切换不触发。
    internal static double RevealDurationMs(int length) => Math.Clamp(length * 12.0, 400, 3500);

    public void RevealFromEmpty()
    {
        var target = _text ?? "";
        if (target.Length == 0 || TextVisibility != Visibility.Visible)
        {
            return;
        }
        Text = "";
        (_typewriter ??= new AdaptiveTypewriter(text => Text = text)).Reveal(target, "", RevealDurationMs(target.Length));
    }

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));

    private static Brush Themed(string key, string fallback) =>
        Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);

    // 与 MainWindowResources.xaml 的 SoftShadow 同参数（VM 层拿不到 XAML 资源，复制常量保持一致）。
    private static DropShadowEffect CreateSoftShadow()
    {
        var effect = new DropShadowEffect
        {
            Color = (Color)ColorConverter.ConvertFromString("#12224A"),
            BlurRadius = 14,
            ShadowDepth = 2,
            Direction = 270,
            Opacity = 0.08,
            RenderingBias = RenderingBias.Quality,
        };
        effect.Freeze();
        return effect;
    }

    private static string FormatTime(double seconds) => seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");
    internal static string WorkDetailText(string text)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Equals("Model activity", StringComparison.OrdinalIgnoreCase))
        {
            return "";
        }
        return text;
    }

    internal static string FormatDuration(double seconds)
    {
        if (seconds < 60)
        {
            return $"{Math.Max(0, (int)seconds)}s";
        }
        var minutes = (int)(seconds / 60);
        var remainder = (int)(seconds % 60);
        return remainder > 0 ? $"{minutes}m {remainder}s" : $"{minutes}m";
    }

    internal static IReadOnlyList<WorkStepViewModel> BuildSteps(DesktopMessage message)
    {
        if (message.Steps is null || message.Steps.Count == 0)
        {
            return Array.Empty<WorkStepViewModel>();
        }
        // schema v1 兼容：非流式生命周期事件按 SpanId 折叠为单行状态机。
        // 流式思考片段必须逐段保留；工具边界之后即使复用了同一 span，也不能覆盖此前片段。
        var ordered = message.Steps
            .OrderBy(step => step.Seq > 0 ? 0 : 1)
            .ThenBy(step => step.Seq)
            .ThenBy(step => step.CreatedAt)
            .ToList();
        var collapsed = CollapseBySpan(ordered);
        // Runtime upgrades can temporarily emit both the old key-only route event and
        // its schema-v1 span event. Prefer the structured event so the key-only copy
        // cannot appear as a false gray pending node after the real step completed.
        var structuredKeys = collapsed
            .Where(HasStructuredTrace)
            .Select(step => (step.Key ?? "").Trim())
            .Where(key => key.Length > 0)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var hasStructuredExecution = collapsed.Any(step => HasStructuredTrace(step)
            && (step.SpanId ?? "").Contains(":execution:", StringComparison.OrdinalIgnoreCase));
        var visible = collapsed
            .Where(step => !IsLegacyDuplicate(step, structuredKeys))
            .Where(step => !IsLegacyExecutionToolDuplicate(step, hasStructuredExecution))
            .Where(IsVisibleWorkTraceStep)
            .ToList();
        visible = NormalizeExecutionFrontier(
            visible,
            message.Subtitle);
        // 层级深度：仅靠 span_id/parent_id 还原 run>step>tool 树，不依赖文案。
        // span_id→深度映射：父节点先于子节点出现（seq 单调），逐个解析 parent 链。
        var depthBySpan = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var step in visible)
        {
            var span = (step.SpanId ?? "").Trim();
            if (string.IsNullOrEmpty(span))
            {
                continue;
            }
            var parent = (step.ParentId ?? "").Trim();
            var depth = !string.IsNullOrEmpty(parent) && depthBySpan.TryGetValue(parent, out var parentDepth)
                ? parentDepth + 1
                : 0;
            depthBySpan[span] = depth;
        }
        // 当前运行中步骤高亮：status 表明 running 且非 terminal。多 agent 场景下可同时有多个 running。
        return visible
            .Select(step =>
            {
                var span = (step.SpanId ?? "").Trim();
                var depth = !string.IsNullOrEmpty(span) && depthBySpan.TryGetValue(span, out var d) ? d : 0;
                return WorkStepViewModel.FromStep(step, depth);
            })
            .ToList();
    }

    private static bool IsVisibleWorkTraceStep(DesktopWorkStep step)
    {
        if (step.IsTerminal)
        {
            return true;
        }
        var detail = (step.Detail ?? "").Trim();
        var runId = (step.RunId ?? "").Trim().ToLowerInvariant();
        var span = (step.SpanId ?? "").Trim().ToLowerInvariant();
        var key = (step.Key ?? "").Trim().ToLowerInvariant();
        if (runId.StartsWith("collab-", StringComparison.Ordinal)
            || span.StartsWith("collab-", StringComparison.Ordinal))
        {
            return true;
        }
        if (key.StartsWith("route:", StringComparison.Ordinal))
        {
            return true;
        }
        if (span.Contains(":scheduler:context", StringComparison.Ordinal)
            || span.Contains(":scheduler:dispatch", StringComparison.Ordinal)
            || span.Contains(":agent:", StringComparison.Ordinal)
            || span.Contains(":model:", StringComparison.Ordinal)
            || span.Contains(":tool:", StringComparison.Ordinal)
            || span.Contains(":execution:", StringComparison.Ordinal)
            || span.Contains(":skill:", StringComparison.Ordinal))
        {
            return true;
        }
        return detail.Contains("收到本轮输入", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("读取当前会话", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("提交到 agent 编排器", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("执行桌面指令", StringComparison.OrdinalIgnoreCase)
            || detail.Contains("调用工具", StringComparison.OrdinalIgnoreCase);
    }

    private static bool HasStructuredTrace(DesktopWorkStep step) =>
        step.Seq > 0
        || !string.IsNullOrWhiteSpace(step.RunId)
        || !string.IsNullOrWhiteSpace(step.EventId)
        || !string.IsNullOrWhiteSpace(step.SpanId);

    private static List<DesktopWorkStep> NormalizeExecutionFrontier(List<DesktopWorkStep> steps, string? runStatus)
    {
        var normalizedRunStatus = (runStatus ?? "").Trim().ToLowerInvariant();
        var runRunning = normalizedRunStatus == "running";
        if (steps.Count == 0)
        {
            return steps;
        }
        if (!runRunning)
        {
            var terminalStatus = normalizedRunStatus switch
            {
                "failed" or "error" => "failed",
                "cancelled" or "canceled" => "cancelled",
                "blocked" => "blocked",
                _ => "completed",
            };
            return steps
                .Select(step => IsUnresolvedExecutionStatus(step.Status) && !step.IsTerminal
                    ? CopyWorkStep(step, terminalStatus)
                    : step)
                .ToList();
        }
        if (steps.Count < 2)
        {
            return steps;
        }
        var lastActive = -1;
        var lastProgressed = -1;
        for (var index = 0; index < steps.Count; index++)
        {
            var step = steps[index];
            if (IsActiveExecutionStatus(step.Status) && !step.IsTerminal)
            {
                lastActive = index;
                lastProgressed = index;
            }
            else if (IsResolvedExecutionStatus(step.Status) || step.IsTerminal)
            {
                lastProgressed = index;
            }
        }
        if (lastActive < 0)
        {
            return steps;
        }
        // A linear work card has one current frontier. If a later span already
        // completed, every earlier lingering started/running span is stale and
        // must be closed as completed. This prevents green -> orange -> green
        // timelines when a lifecycle completion was missed or arrived late.
        var currentActive = lastActive == lastProgressed ? lastActive : -1;
        var normalized = new List<DesktopWorkStep>(steps.Count);
        for (var index = 0; index < steps.Count; index++)
        {
            var step = steps[index];
            normalized.Add(index != currentActive && IsActiveExecutionStatus(step.Status)
                ? CopyWorkStep(step, "completed")
                : step);
        }
        return normalized;
    }

    private static bool IsActiveExecutionStatus(string? status) =>
        (status ?? "").Trim().ToLowerInvariant() is "running" or "started" or "in_progress" or "processing" or "stream";

    private static bool IsUnresolvedExecutionStatus(string? status) =>
        IsActiveExecutionStatus(status)
        || (status ?? "").Trim().ToLowerInvariant() is "queued" or "pending";

    private static bool IsResolvedExecutionStatus(string? status) =>
        (status ?? "").Trim().ToLowerInvariant() is
            "completed" or "done" or "ok" or "success"
            or "failed" or "error" or "blocked"
            or "cancelled" or "canceled";

    private static DesktopWorkStep CopyWorkStep(DesktopWorkStep step, string status) => new()
    {
        Kind = step.Kind,
        Title = step.Title,
        Detail = step.Detail,
        Key = step.Key,
        CreatedAt = step.CreatedAt,
        Seq = step.Seq,
        RunId = step.RunId,
        EventId = step.EventId,
        SpanId = step.SpanId,
        ParentId = step.ParentId,
        Status = status,
        IsTerminal = step.IsTerminal,
        AgentId = step.AgentId,
        IsStreamLane = step.IsStreamLane,
        ReasoningVisibility = step.ReasoningVisibility,
        CallAgent = step.CallAgent,
        CallModel = step.CallModel,
        CallProvider = step.CallProvider,
        CommandText = step.CommandText,
        CommandOutput = step.CommandOutput,
        ShellLabel = step.ShellLabel,
    };

    private static bool IsLegacyDuplicate(DesktopWorkStep step, HashSet<string> structuredKeys)
    {
        var key = (step.Key ?? "").Trim();
        return key.Length > 0 && !HasStructuredTrace(step) && structuredKeys.Contains(key);
    }

    private static bool IsLegacyExecutionToolDuplicate(DesktopWorkStep step, bool hasStructuredExecution)
    {
        if (!hasStructuredExecution || HasStructuredTrace(step))
        {
            return false;
        }
        var key = (step.Key ?? "").Trim();
        var detail = (step.Detail ?? "").Trim();
        // 旧 assistant.message 在 executor 路径上还会产出 "调用工具：app.launch"，
        // 它没有实参、状态也为空，只会变成灰点。真实 execution span 已存在时直接隐藏。
        return key.StartsWith("tool:", StringComparison.OrdinalIgnoreCase)
            && detail.StartsWith("调用工具", StringComparison.OrdinalIgnoreCase);
    }

    private static List<DesktopWorkStep> CollapseBySpan(List<DesktopWorkStep> ordered)
    {
        var result = new List<DesktopWorkStep>(ordered.Count);
        var spanIndex = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        foreach (var step in ordered)
        {
            var spanId = (step.SpanId ?? "").Trim();
            if (string.IsNullOrEmpty(spanId))
            {
                result.Add(step);
                continue;
            }
            if (step.IsTerminal || step.IsStreamLane)
            {
                result.Add(step);
                continue;
            }
            if (spanIndex.TryGetValue(spanId, out var existingIndex))
            {
                // 同 span 状态跃迁：用后到事件的状态/正文覆盖该行，保持单行状态机。
                result[existingIndex] = MergeSpanStep(result[existingIndex], step);
            }
            else
            {
                spanIndex[spanId] = result.Count;
                result.Add(step);
            }
        }
        return result;
    }

    private static DesktopWorkStep MergeSpanStep(DesktopWorkStep prior, DesktopWorkStep next)
    {
        var isExecutionSpan = (prior.SpanId ?? "").Contains(":execution:", StringComparison.OrdinalIgnoreCase);
        var preserveCommandProjection = isExecutionSpan
            && string.Equals((prior.Kind ?? "").Trim(), "command", StringComparison.OrdinalIgnoreCase)
            && string.Equals((next.Kind ?? "").Trim(), "result", StringComparison.OrdinalIgnoreCase);
        return new DesktopWorkStep
        {
            // 后到事件决定视觉/状态；正文优先取非空的后到值，否则保留先前。
            // execution span 已拿到真实命令后，兼容汇总只能更新生命周期，不能把
            // Codex 风格命令行降级回 generic result 叙事。
            Kind = preserveCommandProjection || string.IsNullOrWhiteSpace(next.Kind) ? prior.Kind ?? "" : next.Kind,
            Title = preserveCommandProjection || string.IsNullOrWhiteSpace(next.Title) ? prior.Title : next.Title,
            Detail = preserveCommandProjection || string.IsNullOrWhiteSpace(next.Detail) ? prior.Detail : next.Detail,
            Key = preserveCommandProjection || string.IsNullOrWhiteSpace(next.Key) ? prior.Key : next.Key,
            CreatedAt = prior.CreatedAt,
            Seq = prior.Seq,
            RunId = string.IsNullOrWhiteSpace(next.RunId) ? prior.RunId : next.RunId,
            EventId = string.IsNullOrWhiteSpace(next.EventId) ? prior.EventId : next.EventId,
            SpanId = prior.SpanId ?? "",
            ParentId = string.IsNullOrWhiteSpace(next.ParentId) ? prior.ParentId : next.ParentId,
            Status = string.IsNullOrWhiteSpace(next.Status) ? prior.Status : next.Status,
            IsTerminal = prior.IsTerminal || next.IsTerminal,
            AgentId = string.IsNullOrWhiteSpace(next.AgentId) ? prior.AgentId : next.AgentId,
            IsStreamLane = prior.IsStreamLane || next.IsStreamLane,
            ReasoningVisibility = string.IsNullOrWhiteSpace(next.ReasoningVisibility)
                ? prior.ReasoningVisibility
                : next.ReasoningVisibility,
            CallAgent = string.IsNullOrWhiteSpace(next.CallAgent) ? prior.CallAgent : next.CallAgent,
            CallModel = string.IsNullOrWhiteSpace(next.CallModel) ? prior.CallModel : next.CallModel,
            CallProvider = string.IsNullOrWhiteSpace(next.CallProvider) ? prior.CallProvider : next.CallProvider,
            CommandText = string.IsNullOrWhiteSpace(next.CommandText) ? prior.CommandText : next.CommandText,
            CommandOutput = string.IsNullOrWhiteSpace(next.CommandOutput) ? prior.CommandOutput : next.CommandOutput,
            ShellLabel = string.IsNullOrWhiteSpace(next.ShellLabel) ? prior.ShellLabel : next.ShellLabel,
        };
    }
}

public sealed class WorkChainViewModel : ChatTimelineItemViewModel
{
    // 右缘只留锚点导航的过道：工作卡要像 codex 一样吃满可用宽度，窄窗口才不会右侧被裁。
    private static readonly Thickness WorkMargin = new(0, 0, 0, 12);
    private string _meta = "";
    private string _text = "";
    private string _status = "";
    private string _summaryText = "";
    private Visibility _summaryVisibility = Visibility.Collapsed;
    private bool _isExpanded = true;
    private IReadOnlyList<WorkStepViewModel> _steps = Array.Empty<WorkStepViewModel>();
    private IReadOnlyList<WorkEntryViewModel> _entries = Array.Empty<WorkEntryViewModel>();
    private IReadOnlyList<WorkPhaseViewModel> _phases = Array.Empty<WorkPhaseViewModel>();
    private string _runtimeDeltaText = "正在等待 Runtime 事件...";
    private string _runtimeMetricsText = "0 个事件";
    private string _chainLabel = "思考链";
    private Visibility _stepsVisibility = Visibility.Collapsed;
    private Visibility _phasesVisibility = Visibility.Collapsed;
    private Visibility _textVisibility = Visibility.Collapsed;
    private bool _hasInitializedExpansion;
    private bool _isInvocationCard;

    private WorkChainViewModel(string id)
        : base(id)
    {
    }

    public static WorkChainViewModel FromMessage(DesktopMessage message)
    {
        var model = new WorkChainViewModel(message.Id);
        model.UpdateFromMessage(message);
        return model;
    }

    public void UpdateFromMessage(DesktopMessage message)
    {
        var running = string.Equals(message.Subtitle, "running", StringComparison.OrdinalIgnoreCase);
        var cancelled = string.Equals(message.Subtitle, "cancelled", StringComparison.OrdinalIgnoreCase)
            || string.Equals(message.Subtitle, "canceled", StringComparison.OrdinalIgnoreCase);
        // 步骤 VM 复用：流式思考批次只是 Detail 变长，整表重建（旧行为）会让绑定整块替换呈块状；
        // 原位接管后由步骤自己的打字机逐字揭示，结构未变时 Steps/Entries 实例都不换、不触发重渲染。
        var steps = MergeSteps(_steps, MessageViewModel.BuildSteps(message), running);
        var detail = MessageViewModel.WorkDetailText(message.Text);
        var terminal = message.Steps?.LastOrDefault(step => step.IsTerminal);
        var status = cancelled
            ? "cancelled"
            : string.IsNullOrWhiteSpace(terminal?.Status)
                ? running ? "running" : "completed"
                : terminal!.Status.Trim().ToLowerInvariant();

        // Worked for 计时：终态用落库的用时；运行中的卡没有专属计时器，用 CreatedAt 实时推算，
        // 每次流式事件触发重渲染即刷新（此前外部模型卡 DurationSeconds 恒 0 导致计时失效）。
        var durationSeconds = message.DurationSeconds;
        if (running && message.CreatedAt > 0)
        {
            durationSeconds = Math.Max(durationSeconds, DateTimeOffset.UtcNow.ToUnixTimeSeconds() - message.CreatedAt);
        }
        var durationLabel = running ? "正在工作" : cancelled ? "已停止" : "已工作";
        var duration = $"{durationLabel} {MessageViewModel.FormatDuration(durationSeconds)}";
        // 分卡后每张工作链卡标注归属模型（协作场景一个参与者一张卡），主聊天单卡无标注。
        Meta = string.IsNullOrWhiteSpace(message.WorkAgent) ? duration : $"{message.WorkAgent} · {duration}";
        // 空卡兜底：既没有可显示步骤也没有正文时给出提示，避免展开后一片空白像"坏了"。
        if (steps.Count == 0 && string.IsNullOrWhiteSpace(detail))
        {
            detail = running ? "正在等待过程事件…" : "本次运行没有可显示的过程事件。";
        }
        Text = detail;
        Status = status;
        var stepsChanged = !ReferenceEquals(steps, _steps);
        Steps = steps;
        var latestDetail = steps.LastOrDefault(step => !string.IsNullOrWhiteSpace(step.Detail))?.Detail;
        RuntimeDeltaText = CompactRuntimeText(string.IsNullOrWhiteSpace(latestDetail) ? detail : latestDetail);
        RuntimeMetricsText = $"{steps.Count} 个事件";
        var isInvocationCard = IsPrimaryAgentInvocationCard(message, steps);
        if (stepsChanged || isInvocationCard != _isInvocationCard)
        {
            Entries = BuildEntries(steps, _entries, running, isInvocationCard);
        }
        _isInvocationCard = isInvocationCard;
        ChainLabel = isInvocationCard ? "Agent / 模型调用" : "思考链";
        StepsVisibility = steps.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        TextVisibility = steps.Count == 0 && !string.IsNullOrWhiteSpace(detail) ? Visibility.Visible : Visibility.Collapsed;
        UpdatePhases(steps, status, running);
        // The live event stream starts open so work is visible while it happens.
        // Subsequent updates preserve the user's explicit expanded/collapsed choice.
        if (!_hasInitializedExpansion)
        {
            _hasInitializedExpansion = true;
            IsExpanded = true;
        }
        // Backend snapshots may carry a stale WorkExpanded flag and must not override
        // the local choice after the event stream has initialized.

        // terminal 汇总页脚：仅当整条 run 已到终态（非 running）时显示，给出步骤计数 + 终态结论。
        // 不依赖中文文案，纯由 status + 步骤数推导。
        var isTerminalRun = status is not ("running" or "queued" or "started");
        if (isTerminalRun && steps.Count > 0)
        {
            var conclusion = status switch
            {
                "failed" or "error" => "运行失败",
                "cancelled" or "canceled" => "已取消",
                "blocked" => "已阻塞",
                _ => "已完成",
            };
            SummaryText = $"{steps.Count} 个事件 · {conclusion}";
            SummaryVisibility = Visibility.Visible;
        }
        else
        {
            SummaryText = "";
            SummaryVisibility = Visibility.Collapsed;
        }
    }

    public override Thickness Margin => WorkMargin;

    public string Meta
    {
        get => _meta;
        private set => SetValue(ref _meta, value, nameof(Meta));
    }

    public string Text
    {
        get => _text;
        private set => SetValue(ref _text, value, nameof(Text));
    }

    public string Status
    {
        get => _status;
        private set
        {
            if (EqualityComparer<string>.Default.Equals(_status, value))
            {
                return;
            }
            _status = value;
            RaisePropertyChanged(nameof(Status));
            RaisePropertyChanged(nameof(StatusLabel));
            RaisePropertyChanged(nameof(StatusBrush));
            RaisePropertyChanged(nameof(StatusBadgeBackground));
            RaisePropertyChanged(nameof(StatusBarBrush));
            RaisePropertyChanged(nameof(IsRunning));
        }
    }

    public string SummaryText
    {
        get => _summaryText;
        private set => SetValue(ref _summaryText, value, nameof(SummaryText));
    }

    public Visibility SummaryVisibility
    {
        get => _summaryVisibility;
        private set => SetValue(ref _summaryVisibility, value, nameof(SummaryVisibility));
    }

    public string StatusLabel => Status switch
    {
        "failed" or "error" => "失败",
        "cancelled" or "canceled" => "已取消",
        "blocked" => "阻塞",
        "running" or "queued" or "started" => "进行中",
        _ => "已完成",
    };

    public bool IsRunning => Status is "running" or "queued" or "started";

    public Brush StatusBrush => Status switch
    {
        "failed" or "error" => Themed("FantasyDangerTextBrush", "#FF6B68"),
        "cancelled" or "canceled" => Themed("FantasyMutedNeutralBrush", "#8B837B"),
        "blocked" => Themed("FantasyWarningTextBrush", "#EFBC4B"),
        "running" or "queued" or "started" => Themed("FantasyCopperBrush", "#E99541"),
        _ => Themed("FantasySuccessTextBrush", "#5BCC80"),
    };

    // 头部状态徽章底色：与 StatusBrush 同色系的浅底，折叠态也可见整条 run 的最终状态。
    public Brush StatusBadgeBackground => Status switch
    {
        "failed" or "error" => Themed("FantasyDangerWashBrush", "#3B1D1C"),
        "cancelled" or "canceled" => Themed("FantasyNeutralWashBrush", "#2A221C"),
        "blocked" => Themed("FantasyWarningWashBrush", "#3A2B10"),
        "running" or "queued" or "started" => Themed("FantasyInfoWashBrush", "#172A43"),
        _ => Themed("FantasySuccessWashBrush", "#173523"),
    };

    // Atelier keeps the authored runtime path copper. Risk states retain their
    // semantic colors so the accent never masks an operational failure.
    public Brush StatusBarBrush => Status switch
    {
        "failed" or "error" => Themed("FantasyDangerBrush", "#FF6B68"),
        "cancelled" or "canceled" => Themed("FantasyMutedNeutralBrush", "#8B837B"),
        "blocked" => Themed("FantasyWarningBrush", "#EFBC4B"),
        "running" or "queued" or "started" => Themed("FantasyCopperBrush", "#E99541"),
        _ => Themed("FantasySuccessTextBrush", "#5BCC80"),
    };

    public IReadOnlyList<WorkStepViewModel> Steps
    {
        get => _steps;
        private set => SetValue(ref _steps, value, nameof(Steps));
    }

    public string RuntimeDeltaText
    {
        get => _runtimeDeltaText;
        private set => SetValue(ref _runtimeDeltaText, value, nameof(RuntimeDeltaText));
    }

    public string RuntimeMetricsText
    {
        get => _runtimeMetricsText;
        private set => SetValue(ref _runtimeMetricsText, value, nameof(RuntimeMetricsText));
    }

    public string ChainLabel
    {
        get => _chainLabel;
        private set => SetValue(ref _chainLabel, value, nameof(ChainLabel));
    }

    private static string CompactRuntimeText(string? value)
    {
        var compact = Regex.Replace((value ?? "").Trim(), @"\s+", " ");
        if (compact.Length == 0)
        {
            return "正在等待 Runtime 事件...";
        }
        if (compact.Contains("thinking process", StringComparison.OrdinalIgnoreCase)
            || compact.Contains("**User Request", StringComparison.OrdinalIgnoreCase)
            || compact.Contains("Analyze User Input", StringComparison.OrdinalIgnoreCase))
        {
            return "模型推理已完成；详细过程保留在可展开的 Runtime tool stream 中。";
        }
        compact = compact.Replace("**", "", StringComparison.Ordinal)
                         .Replace("`", "", StringComparison.Ordinal)
                         .Trim();
        return compact.Length <= 170 ? compact : compact[..167] + "...";
    }

    public IReadOnlyList<WorkEntryViewModel> Entries
    {
        get => _entries;
        private set => SetValue(ref _entries, value, nameof(Entries));
    }

    // 按身份键复用步骤 VM：同 IdentityKey 的旧 VM 原位接管 Detail/状态（流式泳道走打字机），
    // 与位置无关——40 步删头、按 Seq 中间插入、状态跃迁都不再引发"该位起整批重建、全文整块闪现"。
    // 同键多条（如多段"执行命令"）用队列按出现顺序配对，不错配。
    // 全部复用且顺序未变 → 返回旧列表实例（引用不变 → Steps/Entries 均不触发重渲染）。
    private static IReadOnlyList<WorkStepViewModel> MergeSteps(
        IReadOnlyList<WorkStepViewModel> previous,
        IReadOnlyList<WorkStepViewModel> built,
        bool running)
    {
        if (previous.Count == 0 || built.Count == 0)
        {
            return built;
        }
        var pool = new Dictionary<string, Queue<WorkStepViewModel>>(StringComparer.Ordinal);
        foreach (var prior in previous)
        {
            if (!pool.TryGetValue(prior.IdentityKey, out var queue))
            {
                queue = new Queue<WorkStepViewModel>();
                pool[prior.IdentityKey] = queue;
            }
            queue.Enqueue(prior);
        }
        var merged = new List<WorkStepViewModel>(built.Count);
        var allSame = built.Count == previous.Count;
        for (var index = 0; index < built.Count; index++)
        {
            var next = built[index];
            if (pool.TryGetValue(next.IdentityKey, out var queue)
                && queue.Count > 0
                && queue.Peek().CanAdopt(next))
            {
                var reused = queue.Dequeue();
                // 状态跃迁原地接管；变了必须换列表实例，Entries 外壳（徽章/失败标记是构造快照）才会重建。
                if (reused.AdoptStatus(next))
                {
                    allSame = false;
                }
                if (reused.AdoptStructured(next))
                {
                    allSame = false;
                }
                // animate：流式泳道（思考/起草/回复）无论卡是否已完结都走打字机（收尾批不瞬贴）；
                // 非流式沿用原语义（运行中的非块状步骤才动画）。
                reused.AdoptDetail(next.Detail, animate: next.IsStreamLane || (running && !next.IsBlock));
                if (next.IsStreamLane && !running)
                {
                    // 卡已完结：收尾封顶（≤3.5s，与气泡揭示同值）。否则泳道按批间隔 EMA
                    // （慢模型上限 12.6s）继续慢放，回复气泡定稿直贴后 step 还在爬——观感倒挂。
                    reused.ExpediteTail();
                }
                if (allSame && !ReferenceEquals(index < previous.Count ? previous[index] : null, reused))
                {
                    allSame = false; // 复用成功但位置漂移，仍需换列表让 Entries 重排
                }
                merged.Add(reused);
            }
            else
            {
                allSame = false;
                merged.Add(next);
            }
        }
        return allSame ? previous : merged;
    }

    // Codex 式时间线：叙事步骤保持可读段落，连续的命令/输出/diff 块折叠成 "Ran N commands" 组。
    private static IReadOnlyList<WorkEntryViewModel> BuildEntries(
        IReadOnlyList<WorkStepViewModel> steps,
        IReadOnlyList<WorkEntryViewModel> previous,
        bool runRunning,
        bool allowCallGroups)
    {
        var entries = new List<WorkEntryViewModel>();
        var commandBuffer = new List<WorkStepViewModel>();
        var callBuffer = new List<WorkStepViewModel>();
        foreach (var step in steps)
        {
            var kind = (step.Kind ?? "").Trim().ToLowerInvariant();
            if (allowCallGroups && kind == "call" && step.IsExternalCall)
            {
                FlushCommands();
                callBuffer.Add(step);
                continue;
            }
            if ((kind == "command" && step.HasCommandInvocation) || kind is "diff" or "permission")
            {
                FlushCalls();
                commandBuffer.Add(step);
                continue;
            }
            if (kind == "result" && commandBuffer.Count > 0)
            {
                commandBuffer.Add(step);
                continue;
            }
            FlushCommands();
            FlushCalls();
            entries.Add(new WorkNarrativeEntryViewModel(step));
        }
        FlushCommands();
        FlushCalls();

        // Stream updates can insert narrative lanes before a command group. Preserve
        // expansion by the group's stable first command/span identity, not list index,
        // so one model run never borrows another group's UI state.
        var previousGroups = previous
            .OfType<WorkCommandGroupViewModel>()
            .GroupBy(group => group.GroupKey, StringComparer.Ordinal)
            .ToDictionary(
                group => group.Key,
                group => new Queue<WorkCommandGroupViewModel>(group),
                StringComparer.Ordinal);
        foreach (var next in entries.OfType<WorkCommandGroupViewModel>())
        {
            if (previousGroups.TryGetValue(next.GroupKey, out var matches) && matches.Count > 0)
            {
                var prior = matches.Dequeue();
                next.IsExpanded = prior.IsExpanded || next.HasFailure;
            }
        }
        var previousCalls = previous
            .OfType<WorkCallGroupViewModel>()
            .GroupBy(group => group.GroupKey, StringComparer.Ordinal)
            .ToDictionary(
                group => group.Key,
                group => new Queue<WorkCallGroupViewModel>(group),
                StringComparer.Ordinal);
        foreach (var next in entries.OfType<WorkCallGroupViewModel>())
        {
            if (previousCalls.TryGetValue(next.GroupKey, out var matches) && matches.Count > 0)
            {
                next.IsExpanded = matches.Dequeue().IsExpanded;
            }
        }
        for (var index = 0; index < entries.Count; index++)
        {
            entries[index].ConnectTo(index + 1 < entries.Count ? entries[index + 1] : null, runRunning);
        }
        return entries;

        void FlushCommands()
        {
            if (commandBuffer.Count == 0)
            {
                return;
            }
            entries.Add(new WorkCommandGroupViewModel(commandBuffer.ToList()));
            commandBuffer.Clear();
        }

        void FlushCalls()
        {
            if (callBuffer.Count == 0)
            {
                return;
            }
            entries.Add(new WorkCallGroupViewModel(callBuffer.ToList()));
            callBuffer.Clear();
        }
    }

    private static bool IsPrimaryAgentInvocationCard(
        DesktopMessage message,
        IReadOnlyList<WorkStepViewModel> steps)
    {
        var owner = (message.WorkAgent ?? "").Trim();
        var isPrimaryOwner = string.IsNullOrWhiteSpace(owner)
            || owner.Equals("Spirit", StringComparison.OrdinalIgnoreCase)
            || owner.Equals("main_text", StringComparison.OrdinalIgnoreCase)
            || owner.Equals("主 Agent", StringComparison.OrdinalIgnoreCase)
            || owner.Equals("主Agent", StringComparison.OrdinalIgnoreCase)
            // Compatibility for dispatch cards persisted before WorkAgent was
            // normalized to Spirit.
            || owner.StartsWith("调用 ", StringComparison.OrdinalIgnoreCase);
        return isPrimaryOwner && steps.Any(step => step.IsExternalCall);
    }

    public Visibility StepsVisibility
    {
        get => _stepsVisibility;
        private set => SetValue(ref _stepsVisibility, value, nameof(StepsVisibility));
    }

    public IReadOnlyList<WorkPhaseViewModel> Phases
    {
        get => _phases;
        private set => SetValue(ref _phases, value, nameof(Phases));
    }

    public Visibility PhasesVisibility
    {
        get => _phasesVisibility;
        private set => SetValue(ref _phasesVisibility, value, nameof(PhasesVisibility));
    }

    // Stage projection never previews future work. Only reached stages are
    // emitted; DONE appears after a successful terminal event.
    private void UpdatePhases(IReadOnlyList<WorkStepViewModel> steps, string status, bool running)
    {
        if (steps.Count == 0)
        {
            Phases = Array.Empty<WorkPhaseViewModel>();
            PhasesVisibility = Visibility.Collapsed;
            return;
        }

        // 归并规则（4 段）：
        //  THINKING ← thinking 泳道 / 语言模型调用
        //  READING  ← 读取类命令/上下文加载（command 且非编辑）
        //  EDITING  ← diff / 文件编辑
        //  DONE     ← 终态
        var reached = 0; // 0=thinking 1=reading 2=editing 3=done
        foreach (var step in steps)
        {
            var kind = (step.Kind ?? "").Trim().ToLowerInvariant();
            var idx = kind switch
            {
                "diff" => 2,
                "command" or "result" => 1,
                _ => 0,
            };
            if (idx > reached)
            {
                reached = idx;
            }
        }

        var isTerminal = status is not ("running" or "queued" or "started");
        var isFailure = status is "failed" or "error" or "cancelled" or "canceled" or "blocked";
        if (isTerminal && !isFailure)
        {
            reached = 3;
        }

        var labels = new[] { "THINKING", "READING", "EDITING", "DONE" };
        var phases = new List<WorkPhaseViewModel>(reached + 1);
        for (var i = 0; i <= reached; i++)
        {
            // The last emitted stage is current unless the run has completed.
            var state = i < reached
                ? "done"
                : (isTerminal && !isFailure) ? "done" : (running || !isTerminal ? "active" : "done");
            var phase = new WorkPhaseViewModel(labels[i], state) { ConnectorVisibility = i == 0 ? Visibility.Collapsed : Visibility.Visible };
            phases.Add(phase);
        }

        Phases = phases;
        PhasesVisibility = Visibility.Visible;
    }

    public Visibility TextVisibility
    {
        get => _textVisibility;
        private set => SetValue(ref _textVisibility, value, nameof(TextVisibility));
    }

    public bool IsExpanded
    {
        get => _isExpanded;
        set => SetValue(ref _isExpanded, value, nameof(IsExpanded));
    }

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));

    // 从当前主题字典解析 v4 语义 Brush；解析失败回退 hex。时间线不随切主题重建（既有局限），
    // 新建卡片按当前主题取色即可，不再硬编码 v2 蓝底浅色。
    private static Brush Themed(string key, string fallback) =>
        Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);
}

// 概念稿 Permission Gate 上方的阶段步进条（THINKING→READING→EDITING→DONE）。
// 纯投影：由 WorkChainViewModel 从已有步骤 Kind + 终态推导，不引入新后端字段。
// 三态：done(已过) / active(进行中) / pending(未达)，驱动色相与透明度。
public sealed class WorkPhaseViewModel
{
    public WorkPhaseViewModel(string label, string state)
    {
        Label = label;
        State = state;
        (Brush, Opacity, Weight) = state switch
        {
            "done" => (Themed("FantasySuccessTextBrush", "#5BCC80"), 1.0, FontWeights.SemiBold),
            "active" => (Themed("FantasyCopperBrush", "#E99541"), 1.0, FontWeights.Bold),
            _ => (Themed("FantasyMutedNeutralBrush", "#8B837B"), 0.5, FontWeights.Normal),
        };
        // 连接线：非首个阶段前的短横，done/active 用亮色，pending 用暗色。
        ConnectorBrush = state switch
        {
            "done" => Themed("FantasySuccessTextBrush", "#5BCC80"),
            "active" => Themed("FantasyCopperBrush", "#E99541"),
            _ => Themed("FantasyLineBrush", "#4E463F"),
        };
        Glyph = state switch
        {
            "done" => "●",
            "active" => "◉",
            _ => "○",
        };
    }

    public string Label { get; }
    public string State { get; }
    public Brush Brush { get; }
    public Brush ConnectorBrush { get; }
    public double Opacity { get; }
    public FontWeight Weight { get; }
    public string Glyph { get; }
    public Visibility ConnectorVisibility { get; set; } = Visibility.Visible;

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));

    private static Brush Themed(string key, string fallback) =>
        Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);
}

public abstract class WorkEntryViewModel
{
    protected WorkEntryViewModel(string state)
    {
        State = string.IsNullOrWhiteSpace(state) ? "pending" : state.Trim().ToLowerInvariant();
        NodeBrush = State switch
        {
            "running" => Themed("FantasyCopperBrush", "#E99541"),
            "completed" => Themed("FantasySuccessTextBrush", "#5BCC80"),
            "failed" => Themed("FantasyDangerTextBrush", "#FF6B68"),
            "blocked" => Themed("FantasyWarningTextBrush", "#EFBC4B"),
            "cancelled" => Themed("FantasyMutedNeutralBrush", "#8B837B"),
            _ => Themed("FantasyMutedNeutralBrush", "#8B837B"),
        };
        ConnectorBrush = Themed("FantasyLineBrush", "#4E463F");
        IncomingConnectorBrush = ConnectorBrush;
    }

    public string State { get; }
    public bool IsRunning => State == "running";
    public bool IsCompleted => State == "completed";
    public bool IsPending => State == "pending";
    public Brush NodeBrush { get; }
    public Brush ConnectorBrush { get; private set; }
    public Brush IncomingConnectorBrush { get; private set; }
    public Visibility ConnectorVisibility { get; private set; } = Visibility.Collapsed;
    public Visibility IncomingConnectorVisibility { get; private set; } = Visibility.Collapsed;
    public Visibility ConnectorPulseVisibility { get; private set; } = Visibility.Collapsed;

    internal void ConnectTo(WorkEntryViewModel? next, bool runRunning)
    {
        ConnectorVisibility = next is null ? Visibility.Collapsed : Visibility.Visible;
        if (next is null)
        {
            ConnectorPulseVisibility = Visibility.Collapsed;
            return;
        }
        var activeTransition = runRunning && IsCompleted && (next.IsRunning || next.IsPending);
        ConnectorBrush = IsCompleted && next.IsCompleted
            ? Themed("FantasySuccessTextBrush", "#5BCC80")
            : activeTransition
                ? Themed("FantasyCopperBrush", "#E99541")
                : Themed("FantasyLineBrush", "#4E463F");
        ConnectorPulseVisibility = activeTransition
            ? Visibility.Visible
            : Visibility.Collapsed;
        next.IncomingConnectorBrush = ConnectorBrush;
        next.IncomingConnectorVisibility = Visibility.Visible;
    }

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));

    private static Brush Themed(string key, string fallback) =>
        Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);
}

public sealed class WorkEntryTemplateSelector : DataTemplateSelector
{
    public DataTemplate? NarrativeTemplate { get; set; }
    public DataTemplate? CallTemplate { get; set; }
    public DataTemplate? CommandTemplate { get; set; }

    public override DataTemplate? SelectTemplate(object item, DependencyObject container) => item switch
    {
        WorkCallGroupViewModel => CallTemplate,
        WorkCommandGroupViewModel => CommandTemplate,
        WorkNarrativeEntryViewModel => NarrativeTemplate,
        _ => base.SelectTemplate(item, container),
    };
}

public sealed class WorkNarrativeEntryViewModel : WorkEntryViewModel
{
    public WorkNarrativeEntryViewModel(WorkStepViewModel step)
        : base(step.StateBucket)
    {
        Step = step;
        // 叙事段落降噪：只有异常态才配徽章，正常完成不打 DONE。
        BadgeVisibility = step.StatusLabel is "FAILED" or "BLOCKED" or "CANCELLED"
            ? Visibility.Visible
            : Visibility.Collapsed;
        FooterVisibility = BadgeVisibility == Visibility.Visible || step.AgentVisibility == Visibility.Visible
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    public WorkStepViewModel Step { get; }
    public Visibility BadgeVisibility { get; }
    public Visibility FooterVisibility { get; }
}

public sealed class WorkCallGroupViewModel : WorkEntryViewModel, INotifyPropertyChanged
{
    private bool _isExpanded = true;

    public WorkCallGroupViewModel(IReadOnlyList<WorkStepViewModel> steps)
        : base(AggregateState(steps))
    {
        Steps = steps;
        GroupKey = steps.FirstOrDefault()?.IdentityKey ?? Guid.NewGuid().ToString("N");
        var agents = steps.Select(step => step.CallAgent).Where(value => !string.IsNullOrWhiteSpace(value)).Distinct(StringComparer.OrdinalIgnoreCase);
        var models = steps.Select(step => step.CallModel).Where(value => !string.IsNullOrWhiteSpace(value)).Distinct(StringComparer.OrdinalIgnoreCase);
        var providers = steps.Select(step => step.CallProvider).Where(value => !string.IsNullOrWhiteSpace(value)).Distinct(StringComparer.OrdinalIgnoreCase);
        var agentText = string.Join(", ", agents);
        var modelText = string.Join(", ", models);
        var providerText = string.Join(", ", providers);
        HeaderText = string.IsNullOrWhiteSpace(agentText) ? "调用 Agent / 模型" : $"调用 {agentText}";
        TargetText = string.Join(" · ", new[] { providerText, modelText }.Where(value => !string.IsNullOrWhiteSpace(value)));
        TargetVisibility = string.IsNullOrWhiteSpace(TargetText) ? Visibility.Collapsed : Visibility.Visible;
    }

    public IReadOnlyList<WorkStepViewModel> Steps { get; }
    public string GroupKey { get; }
    public string HeaderText { get; }
    public string TargetText { get; }
    public Visibility TargetVisibility { get; }

    public bool IsExpanded
    {
        get => _isExpanded;
        set
        {
            if (_isExpanded == value) return;
            _isExpanded = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsExpanded)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private static string AggregateState(IReadOnlyList<WorkStepViewModel> steps)
    {
        if (steps.Any(step => step.StateBucket is "failed" or "blocked")) return steps.Any(step => step.StateBucket == "failed") ? "failed" : "blocked";
        if (steps.Any(step => step.StateBucket == "running")) return "running";
        if (steps.Count > 0 && steps.All(step => step.StateBucket == "completed")) return "completed";
        return "pending";
    }
}

public sealed class WorkCommandGroupViewModel : WorkEntryViewModel, INotifyPropertyChanged
{
    private bool _isExpanded;

    public WorkCommandGroupViewModel(IReadOnlyList<WorkStepViewModel> steps)
        : base(AggregateState(steps))
    {
        Steps = steps;
        GroupKey = steps.FirstOrDefault(step => step.Kind is "command" or "diff" or "permission")?.IdentityKey
            ?? steps.FirstOrDefault()?.IdentityKey
            ?? Guid.NewGuid().ToString("N");
        Invocations = BuildInvocations(steps);
        AuxiliarySteps = steps.Where(step => step.Kind is "diff" or "permission").ToList();
        InvocationsVisibility = Invocations.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        AuxiliaryVisibility = AuxiliarySteps.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        var commandCount = Invocations.Count;
        var toolCount = Invocations.Count(invocation => invocation.IsTool);
        var shellCount = commandCount - toolCount;
        var editCount = steps.Count(step => string.Equals(step.Kind, "diff", StringComparison.OrdinalIgnoreCase));
        var permissionCount = steps.Count(step => string.Equals(step.Kind, "permission", StringComparison.OrdinalIgnoreCase));
        var headerParts = new List<string>();
        if (shellCount == 1)
        {
            headerParts.Add("Ran command");
        }
        else if (shellCount > 1)
        {
            headerParts.Add($"Ran {shellCount} commands");
        }
        if (toolCount == 1)
        {
            headerParts.Add("Called tool");
        }
        else if (toolCount > 1)
        {
            headerParts.Add($"Called {toolCount} tools");
        }
        if (editCount > 0)
        {
            headerParts.Add($"编辑了 {editCount} 个文件");
        }
        if (permissionCount > 0)
        {
            headerParts.Add("等待操作授权");
        }
        HeaderText = headerParts.Count > 0
            ? string.Join(" · ", headerParts)
            : $"{steps.Count} 个步骤";
        GroupLabel = permissionCount > 0
            ? "权限请求"
            : editCount > 0 && commandCount == 0
                ? "文件修改"
                : shellCount > 0
                    ? "命令调用"
                    : "工具调用";
        HasFailure = steps.Any(step => step.StatusLabel is "FAILED" or "BLOCKED");
        // Commands default open so the actual shell, command, output and result are visible.
        _isExpanded = commandCount > 0 || HasFailure || IsRunning;
    }

    public IReadOnlyList<WorkStepViewModel> Steps { get; }
    public IReadOnlyList<WorkCommandInvocationViewModel> Invocations { get; }
    public IReadOnlyList<WorkStepViewModel> AuxiliarySteps { get; }
    public Visibility InvocationsVisibility { get; }
    public Visibility AuxiliaryVisibility { get; }
    public string GroupKey { get; }
    public string HeaderText { get; }
    public string GroupLabel { get; }
    public bool HasFailure { get; }

    public bool IsExpanded
    {
        get => _isExpanded;
        set
        {
            if (_isExpanded == value)
            {
                return;
            }
            _isExpanded = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsExpanded)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(StepsListVisibility)));
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(ChevronText)));
        }
    }

    public Visibility StepsListVisibility => _isExpanded ? Visibility.Visible : Visibility.Collapsed;
    public string ChevronText => _isExpanded ? "⌃" : "⌄";

    public event PropertyChangedEventHandler? PropertyChanged;

    private static string CommandPreview(string? value)
    {
        var line = (value ?? "")
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .FirstOrDefault() ?? "";
        line = Regex.Replace(line, @"\s+", " ");
        return line.Length <= 76 ? line : line[..73] + "...";
    }

    private static IReadOnlyList<WorkCommandInvocationViewModel> BuildInvocations(IReadOnlyList<WorkStepViewModel> steps)
    {
        var result = new List<WorkCommandInvocationViewModel>();
        foreach (var step in steps)
        {
            if (string.Equals(step.Kind, "command", StringComparison.OrdinalIgnoreCase)
                && step.HasCommandInvocation)
            {
                result.Add(new WorkCommandInvocationViewModel(step));
            }
            else if (string.Equals(step.Kind, "result", StringComparison.OrdinalIgnoreCase) && result.Count > 0)
            {
                result[^1].AdoptResult(step);
            }
        }
        return result;
    }

    private static string AggregateState(IReadOnlyList<WorkStepViewModel> steps)
    {
        if (steps.Any(step => step.StateBucket is "failed" or "blocked"))
        {
            return steps.Any(step => step.StateBucket == "failed") ? "failed" : "blocked";
        }
        if (steps.Any(step => step.StateBucket == "running"))
        {
            return "running";
        }
        if (steps.Count > 0 && steps.All(step => step.StateBucket == "completed"))
        {
            return "completed";
        }
        return "pending";
    }
}

public sealed class WorkCommandInvocationViewModel
{
    public WorkCommandInvocationViewModel(WorkStepViewModel step)
    {
        CommandText = (step.CommandText ?? "").Trim();
        ShellLabel = string.IsNullOrWhiteSpace(step.ShellLabel) ? "Tool" : step.ShellLabel.Trim();
        IsTool = ShellLabel is "Tool" or "Skill";
        PromptText = IsTool ? "\u203a " : "$ ";
        CommandOutput = step.CommandOutput;
        State = step.StateBucket;
        ApplyState();
    }

    public string ShellLabel { get; }
    public bool IsTool { get; }
    public string PromptText { get; }
    public string CommandText { get; }
    public string CommandOutput { get; private set; }
    public string State { get; private set; }
    public string ResultGlyph { get; private set; } = SemanticIcons.Success;
    public string ResultLabel { get; private set; } = "Success";
    public Brush ResultBrush { get; private set; } = Themed("FantasySuccessTextBrush", "#5BCC80");
    public Visibility OutputVisibility => string.IsNullOrWhiteSpace(CommandOutput) ? Visibility.Collapsed : Visibility.Visible;

    internal void AdoptResult(WorkStepViewModel step)
    {
        if (!string.IsNullOrWhiteSpace(step.CommandOutput)) CommandOutput = step.CommandOutput;
        else if (!string.IsNullOrWhiteSpace(step.Detail)) CommandOutput = step.Detail;
        State = step.StateBucket;
        ApplyState();
    }

    private void ApplyState()
    {
        (ResultGlyph, ResultLabel, ResultBrush) = State switch
        {
            "running" => (SemanticIcons.Loading, "Running", Themed("FantasyCopperBrush", "#E99541")),
            "pending" => (SemanticIcons.Info, "Queued", Themed("FantasyMutedNeutralBrush", "#8B837B")),
            "failed" => (SemanticIcons.Danger, "Failed", Themed("FantasyDangerTextBrush", "#FF6B68")),
            "blocked" => (SemanticIcons.Warning, "Blocked", Themed("FantasyWarningTextBrush", "#EFBC4B")),
            "cancelled" => (SemanticIcons.Stop, "Cancelled", Themed("FantasyMutedNeutralBrush", "#8B837B")),
            _ => (SemanticIcons.Success, "Success", Themed("FantasySuccessTextBrush", "#5BCC80")),
        };
    }

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));
    private static Brush Themed(string key, string fallback) => Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);
}

// 自适应配速打字机：协作流式批次实测 1.5-4s 才到一批（模型出 token 只有 ~18ms 间隔，节奏被
// worker 批量器+HTTP 中转摊平），固定速率揭示必然"快放追平 → 干等下一批"呈块状。
// 这里用 EMA 估计批次到达间隔，把每批新增字符匀速摊满到"预计下一批到达"时刻，观感即连续逐字。
internal sealed class AdaptiveTypewriter
{
    private const double TickIntervalMs = 33;
    private const double MinBatchIntervalMs = 350;
    // 上限放宽到 12s：本地大模型（35B）实测 ~10s 才出一批，钳在 6s 会"快放追平 → 干等 4s"。
    private const double MaxBatchIntervalMs = 12000;

    private readonly Action<string> _apply;
    private DispatcherTimer? _timer;
    private string _shown = "";
    private string _target = "";
    private DateTime _lastPushAt = DateTime.MinValue;
    private double _batchIntervalMs = 1600; // 初值取实测批间隔下沿，首批也能摊开而不是闪现
    private DateTime _deadline = DateTime.MinValue;
    // 收尾封顶（Expedite 设置）：卡完结后 deadline 不再按 EMA 拉长，后续 Push 一并受限。
    private double _maxDeadlineMs = double.MaxValue;

    public AdaptiveTypewriter(Action<string> apply) => _apply = apply;

    public string Target => _target;

    // 收尾提速：把当前及后续的播完期限封顶到 maxMs。只收 deadline 不清积压——
    // 正在滚动的文字加速追平而非瞬贴（卡完结后 step 不应比回复气泡慢一个数量级）。
    public void Expedite(double maxMs)
    {
        _maxDeadlineMs = Math.Max(1, maxMs);
        var cap = DateTime.UtcNow.AddMilliseconds(_maxDeadlineMs);
        if (_deadline > cap)
        {
            _deadline = cap;
        }
    }

    // 新批次到达：更新批间隔估计并重设揭示截止点。shown 传调用方当前已显示文本（动画中即上次 tick 进度）。
    public void Push(string target, string shown)
    {
        target ??= "";
        if (string.Equals(target, _target, StringComparison.Ordinal))
        {
            return; // 目标未变（非文本字段触发的刷新），不污染批间隔估计
        }
        _shown = shown ?? "";
        _target = target;
        var now = DateTime.UtcNow;
        if (_lastPushAt != DateTime.MinValue)
        {
            var gapMs = (now - _lastPushAt).TotalMilliseconds;
            if (gapMs is > 80 and < 15000)
            {
                _batchIntervalMs = _batchIntervalMs * 0.6 + gapMs * 0.4;
            }
        }
        _lastPushAt = now;
        // 摊满 105% 批间隔：宁可下一批到达时略有积压（无缝续播），也不要提前追平后干等。
        // 收尾封顶生效时（卡已完结）迟到批次不再把期限重新拉长。
        var spreadMs = Math.Min(Math.Clamp(_batchIntervalMs, MinBatchIntervalMs, MaxBatchIntervalMs) * 1.05, _maxDeadlineMs);
        _deadline = now.AddMilliseconds(spreadMs);
        _timer ??= CreateTimer();
        if (!_timer.IsEnabled)
        {
            _timer.Start();
        }
    }

    // 定稿/中断：停表并（由调用方）直贴全文，同时清掉节奏记忆，下一轮流式重新估计。
    public void Stop()
    {
        _timer?.Stop();
        _target = "";
        _shown = "";
        _lastPushAt = DateTime.MinValue;
    }

    // 一次性全文的打字机揭示（主聊天回复整条到达无批次节奏可依）：按给定时长匀速放完。
    public void Reveal(string target, string shown, double durationMs)
    {
        target ??= "";
        if (string.Equals(target, _target, StringComparison.Ordinal))
        {
            return; // 同一目标动画进行中（渲染重入），继续跑
        }
        _shown = shown ?? "";
        _target = target;
        _lastPushAt = DateTime.MinValue; // 固定时长模式不参与批间隔估计
        _deadline = DateTime.UtcNow.AddMilliseconds(Math.Max(200, durationMs));
        _timer ??= CreateTimer();
        if (!_timer.IsEnabled)
        {
            _timer.Start();
        }
    }

    // 是否正朝该目标做揭示动画（用于渲染重入时避免直贴打断动画）。
    public bool IsActiveFor(string target) =>
        _timer?.IsEnabled == true && string.Equals(_target, target ?? "", StringComparison.Ordinal);

    private DispatcherTimer CreateTimer()
    {
        // Input 优先级（高于 Background）：Background 档在流式期间会被渲染/输入持续饿死，
        // 打字机几百毫秒才轮到一次 tick，表现为"卡着输出、一顿一顿"。tick 本身只做
        // substring+赋值（微秒级），提档不会反过来挤占渲染。
        var timer = new DispatcherTimer(DispatcherPriority.Input)
        {
            Interval = TimeSpan.FromMilliseconds(TickIntervalMs),
        };
        timer.Tick += (_, _) => Tick();
        return timer;
    }

    private void Tick()
    {
        var pending = _target.Length - _shown.Length;
        if (pending <= 0)
        {
            _timer?.Stop();
            return;
        }
        // 步长 = 积压 × tick间隔 / 剩余揭示预算；截止点已过（批次迟到）时用 500ms 尾速放完余量，仍保持连续。
        var remainingMs = Math.Max((_deadline - DateTime.UtcNow).TotalMilliseconds, 500);
        var step = Math.Max(1, (int)Math.Ceiling(pending * TickIntervalMs / remainingMs));
        _shown = _target[..Math.Min(_target.Length, _shown.Length + step)];
        _apply(_shown);
    }
}

public sealed class WorkStepViewModel : INotifyPropertyChanged
{
    private static readonly FontFamily MonoFont = new("pack://application:,,,/Assets/Fonts/#JetBrains Mono, Cascadia Mono, Consolas");
    private static readonly FontFamily UiFont = new("Microsoft YaHei UI, Segoe UI");

    private string _detail = "";
    private Visibility _detailVisibility = Visibility.Collapsed;
    private AdaptiveTypewriter? _detailTypewriter;

    private WorkStepViewModel(string kind, string title, string detail, string status, bool isTerminal, bool isRunning, int depth, string agentId)
    {
        Kind = kind;
        Title = title;
        Detail = detail;

        switch (kind)
        {
            case "command":
                Glyph = "⌘";
                AccentBrush = Themed("FantasyInfoBrush", "#75AFFF");
                BlockBackground = Themed("FantasyNeutralWashBrush", "#2A221C");
                BlockBorder = Themed("FantasyCardBorderBrush", "#4E463F");
                DetailBrush = Themed("FantasyTextBrush", "#F0EAE4");
                DetailFont = MonoFont;
                IsBlock = true;
                break;
            case "result":
                Glyph = "⤳";
                AccentBrush = Themed("FantasySuccessTextBrush", "#5BCC80");
                BlockBackground = Themed("FantasyNeutralWashBrush", "#2A221C");
                BlockBorder = Themed("FantasyCardBorderBrush", "#4E463F");
                DetailBrush = Themed("FantasyMutedBrush", "#ACA39B");
                DetailFont = MonoFont;
                IsBlock = true;
                break;
            case "diff":
                Glyph = "±";
                AccentBrush = Themed("FantasyWarningTextBrush", "#EFBC4B");
                BlockBackground = Themed("FantasyNeutralWashBrush", "#2A221C");
                BlockBorder = Themed("FantasyCardBorderBrush", "#4E463F");
                DetailBrush = Themed("FantasyTextBrush", "#F0EAE4");
                DetailFont = MonoFont;
                IsBlock = true;
                break;
            case "permission":
                Glyph = "⚠";
                AccentBrush = Themed("FantasyDangerTextBrush", "#FF6B68");
                BlockBackground = Themed("FantasyDangerWashBrush", "#3B1D1C");
                BlockBorder = Themed("FantasyDangerBorderBrush", "#FF6B68");
                DetailBrush = Themed("FantasyDangerTextBrush", "#FF6B68");
                DetailFont = UiFont;
                IsBlock = true;
                break;
            case "call":
                Glyph = "·";
                AccentBrush = Themed("FantasyInfoBrush", "#75AFFF");
                BlockBackground = Solid("#00000000");
                BlockBorder = Solid("#00000000");
                DetailBrush = Themed("FantasyFieldLabelBrush", "#ACA39B");
                DetailFont = UiFont;
                IsBlock = false;
                break;
            default:
                Kind = "thinking";
                Glyph = "·";
                AccentBrush = Themed("FantasyMutedNeutralBrush", "#8B837B");
                BlockBackground = Solid("#00000000");
                BlockBorder = Solid("#00000000");
                DetailBrush = Themed("FantasyFieldLabelBrush", "#ACA39B");
                DetailFont = UiFont;
                IsBlock = false;
                break;
        }

        BlockVisibility = IsBlock ? Visibility.Visible : Visibility.Collapsed;
        PlainVisibility = IsBlock ? Visibility.Collapsed : Visibility.Visible;

        // ── 结构化状态机：徽章 / 高亮 / 缩进 / agent 标签，全部由 schema v1 字段驱动，不读文案 ──
        var normalizedStatus = (status ?? "").Trim().ToLowerInvariant();
        var statusBucket = isTerminal
            ? (normalizedStatus is "failed" or "error" ? "failed"
               : normalizedStatus is "cancelled" or "canceled" ? "cancelled"
               : "completed")
            : normalizedStatus switch
            {
                "failed" or "error" => "failed",
                "blocked" => "blocked",
                "cancelled" or "canceled" => "cancelled",
                "completed" or "done" or "ok" or "success" => "completed",
                "running" or "started" or "in_progress" or "processing" or "stream" => "running",
                "queued" or "pending" => "pending",
                _ => isRunning ? "running" : "",
            };

        StateBucket = string.IsNullOrWhiteSpace(statusBucket) ? "pending" : statusBucket;

        (StatusLabel, StatusBrush, StatusBadgeBackground) = statusBucket switch
        {
            "running" => ("RUNNING", Themed("FantasyCopperBrush", "#E99541"), Themed("FantasyGoldWashBrush", "#332617")),
            "pending" => ("QUEUED", Themed("FantasyMutedNeutralBrush", "#8B837B"), Themed("FantasyNeutralWashBrush", "#2A221C")),
            "completed" => ("DONE", Themed("FantasySuccessTextBrush", "#5BCC80"), Themed("FantasySuccessWashBrush", "#173523")),
            "failed" => ("FAILED", Themed("FantasyDangerTextBrush", "#FF6B68"), Themed("FantasyDangerWashBrush", "#3B1D1C")),
            "blocked" => ("BLOCKED", Themed("FantasyWarningTextBrush", "#EFBC4B"), Themed("FantasyWarningWashBrush", "#3A2B10")),
            "cancelled" => ("CANCELLED", Themed("FantasyMutedNeutralBrush", "#8B837B"), Themed("FantasyNeutralWashBrush", "#2A221C")),
            _ => ("", Solid("#00000000"), Solid("#00000000")),
        };
        StatusVisibility = string.IsNullOrEmpty(StatusLabel) ? Visibility.Collapsed : Visibility.Visible;

        // 当前运行中步骤高亮：左侧 accent 条 + 浅底。完成/失败态不高亮。
        IsRunning = statusBucket == "running";
        RunningBarBrush = IsRunning ? Themed("FantasyCopperBrush", "#E99541") : Solid("#00000000");
        HighlightBackground = IsRunning ? Themed("FantasyGoldWashBrush", "#332617") : Solid("#00000000");

        // 层级缩进：每层 22px，上限 3 层防跑飞。run 根=0。
        Depth = Math.Max(0, Math.Min(depth, 3));
        Indent = new Thickness(Depth * 22, 0, 0, 0);

        // agent 标签：多 agent 场景区分执行者；general/system/空 不显示（降噪）。
        var normalizedAgent = (agentId ?? "").Trim();
        var hideAgent = normalizedAgent.Length == 0
            || normalizedAgent.Equals("general", StringComparison.OrdinalIgnoreCase)
            || normalizedAgent.Equals("system", StringComparison.OrdinalIgnoreCase)
            || normalizedAgent.Equals("main_text", StringComparison.OrdinalIgnoreCase)
            || normalizedAgent.Equals("agent_cluster", StringComparison.OrdinalIgnoreCase);
        AgentLabel = hideAgent ? "" : WorkflowDisplayText.ActorLabel(normalizedAgent);
        AgentVisibility = hideAgent ? Visibility.Collapsed : Visibility.Visible;
    }

    public static WorkStepViewModel FromStep(DesktopWorkStep step) => FromStep(step, 0);

    public static WorkStepViewModel FromStep(DesktopWorkStep step, int depth)
    {
        var kind = (step.Kind ?? "").Trim().ToLowerInvariant();
        var title = (step.Title ?? "").Trim();
        var spanId = (step.SpanId ?? "").Trim();
        if (spanId.Contains(":model:", StringComparison.OrdinalIgnoreCase))
        {
            title = string.IsNullOrWhiteSpace(step.CallAgent) ? "思考" : "调用";
            kind = string.IsNullOrWhiteSpace(step.CallAgent) ? "thinking" : "call";
        }
        var detail = DisplayDetail(step);
        // running 推断：有 span 但未到达任何 terminal/completed 状态，视为进行中（status 为空时的兜底）。
        var status = (step.Status ?? "").Trim();
        var isRunning = !step.IsTerminal
            && !string.IsNullOrEmpty((step.SpanId ?? "").Trim())
            && status.Length == 0;
        var model = new WorkStepViewModel(kind, title, detail, status, step.IsTerminal, isRunning, depth, step.AgentId);
        model.IsStreamLane = step.IsStreamLane;
        model.CallAgent = step.CallAgent ?? "";
        model.CallModel = step.CallModel ?? "";
        model.CallProvider = step.CallProvider ?? "";
        model.CommandText = step.CommandText ?? "";
        model.CommandOutput = step.CommandOutput ?? "";
        model.ShellLabel = step.ShellLabel ?? "";
        // 身份键：跨渲染批次稳定标识"同一逻辑步骤"，供 MergeSteps 按身份（而非位置）复用 VM。
        // span 步骤（主聊天 schema v1）用 SpanId；协作泳道无 span，用 Kind+Title+AgentId+CreatedAt——
        // 三处写入 CreatedAt 均只写一次（泳道补零/结构化追加/投影深拷贝透传），全程稳定。
        // 用原始 AgentId 而非 AgentLabel：后者对 general/system 归一为空会让不同 agent 互串。
        var span = spanId;
        model.IdentityKey = span.Length > 0
            ? $"span|{span}"
            : $"{kind}|{title}|{(step.AgentId ?? "").Trim().ToLowerInvariant()}|{step.CreatedAt:R}";
        return model;
    }

    private static string DisplayDetail(DesktopWorkStep step)
    {
        var detail = (step.Detail ?? "").Trim();
        var runId = (step.RunId ?? "").Trim().ToLowerInvariant();
        var span = (step.SpanId ?? "").Trim().ToLowerInvariant();
        if (step.IsTerminal)
        {
            if (!string.IsNullOrWhiteSpace(detail))
            {
                return detail;
            }
            return (step.Status ?? "").Equals("failed", StringComparison.OrdinalIgnoreCase)
                ? "请求失败"
                : "已完成";
        }
        // Older persisted main-chat cards predate IsStreamLane but already carry
        // the structured :reasoning span. The span is authoritative for display.
        if (span.EndsWith(":reasoning", StringComparison.Ordinal))
        {
            return detail;
        }
        if (string.Equals((step.Kind ?? "").Trim(), "result", StringComparison.OrdinalIgnoreCase)
            && step.IsStreamLane
            && string.Equals((step.Status ?? "").Trim(), "completed", StringComparison.OrdinalIgnoreCase))
        {
            return "回复已生成，正文见下方消息。";
        }
        if (runId.StartsWith("collab-", StringComparison.Ordinal)
            || span.StartsWith("collab-", StringComparison.Ordinal))
        {
            if (step.IsStreamLane
                && string.Equals((step.Kind ?? "").Trim(), "thinking", StringComparison.OrdinalIgnoreCase)
                && string.Equals((step.Title ?? "").Trim(), "思考", StringComparison.OrdinalIgnoreCase))
            {
                var reasoningVisibility = (step.ReasoningVisibility ?? "").Trim().ToLowerInvariant();
                if (reasoningVisibility is "summary" or "process" or "private")
                {
                    return detail;
                }
                return ContextController.CodexStyleReasoningActivity(detail, detail);
            }
            return detail;
        }
        // The backend trace text is the auditable process record. Do not replace
        // context, routing, model, or tool details with generic display copy.
        return detail;
    }

    public string Kind { get; }
    public string Title { get; }
    public string CallAgent { get; private set; } = "";
    public string CallModel { get; private set; } = "";
    public string CallProvider { get; private set; } = "";
    public string CommandText { get; private set; } = "";
    public string CommandOutput { get; private set; } = "";
    public string ShellLabel { get; private set; } = "";
    public bool HasCommandInvocation => Kind == "command" && !string.IsNullOrWhiteSpace(CommandText);
    public bool IsExternalCall => Kind == "call" && IsExternalCallTarget(CallAgent);

    private static bool IsExternalCallTarget(string? value)
    {
        var target = (value ?? "").Trim();
        return target.Length > 0;
    }

    // 思考泳道流式增长：Detail 可变 + 变更通知，配合 WorkChainViewModel 的步骤 VM 复用，
    // 让打字机逐字揭示直接反映到已绑定的 TextBlock，而不是整表重建后整块替换。
    public string Detail
    {
        get => _detail;
        private set
        {
            if (string.Equals(_detail, value, StringComparison.Ordinal))
            {
                return;
            }
            _detail = value ?? "";
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Detail)));
            DetailVisibility = string.IsNullOrWhiteSpace(_detail) ? Visibility.Collapsed : Visibility.Visible;
        }
    }

    public Visibility DetailVisibility
    {
        get => _detailVisibility;
        private set
        {
            if (_detailVisibility == value)
            {
                return;
            }
            _detailVisibility = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(DetailVisibility)));
        }
    }

    // 步骤 VM 复用判定：身份键已保证 Kind/Title/Agent 相同，这里只校验结构性硬条件；
    // 状态桶变化不再触发重建（由 AdoptStatus 原地接管，打字机因此存活）。
    internal bool CanAdopt(WorkStepViewModel next) =>
        IsBlock == next.IsBlock
        && Depth == next.Depth;

    internal bool AdoptStructured(WorkStepViewModel next)
    {
        var changed = !string.Equals(CallAgent, next.CallAgent, StringComparison.Ordinal)
            || !string.Equals(CallModel, next.CallModel, StringComparison.Ordinal)
            || !string.Equals(CallProvider, next.CallProvider, StringComparison.Ordinal)
            || !string.Equals(CommandText, next.CommandText, StringComparison.Ordinal)
            || !string.Equals(CommandOutput, next.CommandOutput, StringComparison.Ordinal)
            || !string.Equals(ShellLabel, next.ShellLabel, StringComparison.Ordinal);
        CallAgent = next.CallAgent;
        CallModel = next.CallModel;
        CallProvider = next.CallProvider;
        CommandText = next.CommandText;
        CommandOutput = next.CommandOutput;
        ShellLabel = next.ShellLabel;
        return changed;
    }

    // 原位更新 Detail：思考泳道（非块状）运行中且新文本是旧文本的扩展 → 打字机逐字揭示；
    // 其余情况（命令块 / 截断改写 / 终态收尾）直贴。
    internal void AdoptDetail(string target, bool animate)
    {
        target ??= "";
        if (string.Equals(target, _detailTypewriter?.Target ?? Detail, StringComparison.Ordinal))
        {
            return; // 目标未变，动画继续追
        }
        var shown = Detail ?? "";
        if (animate && target.Length > shown.Length && target.StartsWith(shown, StringComparison.Ordinal))
        {
            (_detailTypewriter ??= new AdaptiveTypewriter(text => Detail = text)).Push(target, shown);
        }
        else
        {
            _detailTypewriter?.Stop();
            Detail = target;
        }
    }

    // 卡完结后的收尾提速：把泳道打字机的播完期限封顶到 3.5s（与气泡 RevealDurationMs 封顶同值）。
    // 只收 deadline 不瞬贴——正在滚动的文字加速播完，观感与回复气泡定稿节奏对齐。
    internal void ExpediteTail() => _detailTypewriter?.Expedite(3500);

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Glyph { get; }
    public bool IsBlock { get; }
    public Brush AccentBrush { get; }
    public Brush PulseBrush => NodeBrush;
    public string StateBucket { get; private set; } = "pending";
    public bool IsCompleted => StateBucket == "completed";
    public bool IsPending => StateBucket == "pending";
    public Brush NodeBrush => StateBucket switch
    {
        "running" => Themed("FantasyCopperBrush", "#E99541"),
        "completed" => Themed("FantasySuccessTextBrush", "#5BCC80"),
        "failed" => Themed("FantasyDangerTextBrush", "#FF6B68"),
        "blocked" => Themed("FantasyWarningTextBrush", "#EFBC4B"),
        "cancelled" => Themed("FantasyMutedNeutralBrush", "#8B837B"),
        _ => Themed("FantasyMutedNeutralBrush", "#8B837B"),
    };
    public Brush BlockBackground { get; }
    public Brush BlockBorder { get; }
    public Brush DetailBrush { get; }
    public FontFamily DetailFont { get; }
    public Visibility BlockVisibility { get; }
    public Visibility PlainVisibility { get; }

    // ── 结构化状态机投影（schema v1 驱动，不读文案）──
    // 状态组改为可通知属性：running→completed 等跃迁由 AdoptStatus 原地接管，VM（连同打字机）不再因状态变化被重建。
    public string StatusLabel
    {
        get => _statusLabel;
        private set => SetStatusValue(ref _statusLabel, value, nameof(StatusLabel));
    }

    public Brush StatusBrush
    {
        get => _statusBrush;
        private set => SetStatusValue(ref _statusBrush, value, nameof(StatusBrush));
    }

    public Brush StatusBadgeBackground
    {
        get => _statusBadgeBackground;
        private set => SetStatusValue(ref _statusBadgeBackground, value, nameof(StatusBadgeBackground));
    }

    public Visibility StatusVisibility
    {
        get => _statusVisibility;
        private set => SetStatusValue(ref _statusVisibility, value, nameof(StatusVisibility));
    }

    public bool IsRunning
    {
        get => _isRunning;
        private set => SetStatusValue(ref _isRunning, value, nameof(IsRunning));
    }

    public Brush RunningBarBrush
    {
        get => _runningBarBrush;
        private set => SetStatusValue(ref _runningBarBrush, value, nameof(RunningBarBrush));
    }

    public Brush HighlightBackground
    {
        get => _highlightBackground;
        private set => SetStatusValue(ref _highlightBackground, value, nameof(HighlightBackground));
    }

    public int Depth { get; }
    public Thickness Indent { get; }
    public string AgentLabel { get; }
    public Visibility AgentVisibility { get; }

    // 身份键（MergeSteps 按此复用 VM）与流式泳道标记（animate 判定），FromStep 构造后赋值。
    internal string IdentityKey { get; private set; } = "";
    internal bool IsStreamLane { get; private set; }

    // 原地接管状态桶：直接复制 next 构造时算好的投影值，有变化时逐项通知。
    // 返回 true 表示状态确实变了（调用方需重建 Entries 外壳——徽章/失败标记是构造快照）。
    internal bool AdoptStatus(WorkStepViewModel next)
    {
        if (string.Equals(StateBucket, next.StateBucket, StringComparison.Ordinal))
        {
            return false;
        }
        StateBucket = next.StateBucket;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(StateBucket)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsCompleted)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsPending)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(NodeBrush)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(PulseBrush)));
        StatusLabel = next.StatusLabel;
        StatusBrush = next.StatusBrush;
        StatusBadgeBackground = next.StatusBadgeBackground;
        StatusVisibility = next.StatusVisibility;
        var runningChanged = IsRunning != next.IsRunning;
        IsRunning = next.IsRunning;
        RunningBarBrush = next.RunningBarBrush;
        HighlightBackground = next.HighlightBackground;
        return true;
    }

    private void SetStatusValue<T>(ref T field, T value, string propertyName)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return;
        }
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    private string _statusLabel = "";
    private Brush _statusBrush = Brushes.Transparent;
    private Brush _statusBadgeBackground = Brushes.Transparent;
    private Visibility _statusVisibility = Visibility.Collapsed;
    private bool _isRunning;
    private Brush _runningBarBrush = Brushes.Transparent;
    private Brush _highlightBackground = Brushes.Transparent;

    private static Brush Solid(string color) => new SolidColorBrush((Color)ColorConverter.ConvertFromString(color));

    private static Brush Themed(string key, string fallback) =>
        Application.Current?.TryFindResource(key) as Brush ?? Solid(fallback);
}

public sealed class ComposerAttachmentViewModel
{
    private ComposerAttachmentViewModel(string fileId, string name, string mimeType, string localPath, long sizeBytes)
    {
        FileId = fileId;
        Name = string.IsNullOrWhiteSpace(name) ? fileId : name;
        MimeType = mimeType;
        ThumbnailPath = IsImageMime(mimeType) && File.Exists(localPath) ? localPath : "";
        ThumbnailVisibility = string.IsNullOrWhiteSpace(ThumbnailPath) ? Visibility.Collapsed : Visibility.Visible;
        FileVisibility = string.IsNullOrWhiteSpace(ThumbnailPath) ? Visibility.Visible : Visibility.Collapsed;
        Extension = Path.GetExtension(Name).TrimStart('.').ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(Extension))
        {
            Extension = "FILE";
        }
        Icon = mimeType.Contains("pdf", StringComparison.OrdinalIgnoreCase) ? "PDF" : "▱";
        Tooltip = $"{Name}{Environment.NewLine}{mimeType}{Environment.NewLine}{FormatBytes(sizeBytes)}";
    }

    public static ComposerAttachmentViewModel FromAttachment(string fileId, string name, string mimeType, string localPath, long sizeBytes) =>
        new(fileId, name, mimeType, localPath, sizeBytes);

    public string FileId { get; }
    public string Name { get; }
    public string MimeType { get; }
    public string ThumbnailPath { get; }
    public Visibility ThumbnailVisibility { get; }
    public Visibility FileVisibility { get; }
    public string Extension { get; }
    public string Icon { get; }
    public string Tooltip { get; }

    private static bool IsImageMime(string mimeType) => mimeType.StartsWith("image/", StringComparison.OrdinalIgnoreCase);

    private static string FormatBytes(long bytes)
    {
        if (bytes <= 0)
        {
            return "--";
        }
        if (bytes < 1024)
        {
            return $"{bytes} B";
        }
        if (bytes < 1024 * 1024)
        {
            return $"{bytes / 1024.0:0.#} KB";
        }
        return $"{bytes / 1024.0 / 1024.0:0.#} MB";
    }
}
