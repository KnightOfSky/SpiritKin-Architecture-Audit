using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Threading;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    // 话题锚点：右侧圆点导航，每个用户提问一枚，hover 显首句、点击滚动定位。
    private const int TopicAnchorLimit = 24;
    private readonly ObservableCollection<TopicAnchorViewModel> _topicAnchors = new();
    private bool _topicAnchorsRefreshQueued;
    // 定稿原地更新（集合不变）时的锚点补扫：2s 一次，unchanged 短路后近乎零成本。
    private readonly DispatcherTimer _topicAnchorsSweepTimer = new() { Interval = TimeSpan.FromSeconds(2) };

    private void InitializeTopicAnchors()
    {
        ChatWorkspace.TopicAnchorsList.ItemsSource = _topicAnchors;
        ChatWorkspace.TopicAnchorsList.AddHandler(ButtonBase.ClickEvent, new RoutedEventHandler(OnTopicAnchorClick));
        _messages.CollectionChanged += (_, _) => QueueTopicAnchorsRefresh();
        // 内容变化兜底：流式草稿定稿是"原地更新"（草稿 Id 与正式回复 Id 同源，
        // RenderActiveMessages 走前缀匹配路径不动集合），CollectionChanged 不会触发，
        // 锚点会一直缺最新定稿。低频轮询 + RebuildTopicAnchors 的 unchanged 短路，成本可忽略。
        _topicAnchorsSweepTimer.Tick += (_, _) => QueueTopicAnchorsRefresh();
        _topicAnchorsSweepTimer.Start();
    }

    // 渲染会整批 Clear+Add 消息，CollectionChanged 会连发；合并到一次空闲重建。
    private void QueueTopicAnchorsRefresh()
    {
        if (_topicAnchorsRefreshQueued)
        {
            return;
        }
        _topicAnchorsRefreshQueued = true;
        Dispatcher.BeginInvoke(new Action(() =>
        {
            _topicAnchorsRefreshQueued = false;
            RebuildTopicAnchors();
        }), DispatcherPriority.Background);
    }

    private void RebuildTopicAnchors()
    {
        // 辩论场景消息几乎全是模型的：模型定稿也生成锚点（绿点），用户提问蓝点。
        // 内容一律读 FullText（权威全文）：定稿气泡揭示动画期间 Text 是逐字中间态
        //（RevealFromEmpty 先清空），读 Text 会把刚定稿的消息当空文本漏掉——
        // 表现为"DS 输出完了锚点没出现"，动画结束后又因内容未再变化迟迟不补。
        var anchorMessages = _messages
            .OfType<MessageViewModel>()
            .Where(item => !string.IsNullOrWhiteSpace(item.FullText))
            .ToList();
        var wanted = anchorMessages
            .Skip(Math.Max(0, anchorMessages.Count - TopicAnchorLimit))
            .Select(message => (
                message.Id,
                Preview: TopicAnchorPreview(message.FullText),
                IsUser: message.Alignment == HorizontalAlignment.Right))
            .ToList();
        // 流式渲染会高频触发重建；内容没变就别 Clear+Add——
        // 否则圆点按钮在鼠标按下与抬起之间被销毁重建，Click 永远不触发。
        var unchanged = wanted.Count == _topicAnchors.Count;
        if (unchanged)
        {
            for (var i = 0; i < wanted.Count; i++)
            {
                if (!string.Equals(wanted[i].Id, _topicAnchors[i].Id, StringComparison.Ordinal)
                    || !string.Equals(wanted[i].Preview, _topicAnchors[i].Preview, StringComparison.Ordinal)
                    || wanted[i].IsUser != _topicAnchors[i].IsUser)
                {
                    unchanged = false;
                    break;
                }
            }
        }
        if (!unchanged)
        {
            _topicAnchors.Clear();
            foreach (var (id, preview, isUser) in wanted)
            {
                _topicAnchors.Add(new TopicAnchorViewModel(id, preview, isUser));
            }
        }
        ChatWorkspace.TopicAnchorsPanel.Visibility = _topicAnchors.Count >= 4 ? Visibility.Visible : Visibility.Collapsed;
    }

    internal static string TopicAnchorPreview(string text)
    {
        var firstLine = (text ?? "")
            .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .FirstOrDefault() ?? "";
        return firstLine.Length > 60 ? firstLine[..60] + "…" : firstLine;
    }

    private void OnTopicAnchorClick(object sender, RoutedEventArgs e)
    {
        // Click 冒泡到 ItemsControl 时 Source 不一定还是 Button 本身，
        // 从 OriginalSource 的 DataContext 解析锚点更稳。
        var anchor = (e.OriginalSource as FrameworkElement)?.DataContext as TopicAnchorViewModel
            ?? (e.Source as FrameworkElement)?.DataContext as TopicAnchorViewModel;
        if (anchor is null)
        {
            return;
        }
        var target = _messages.FirstOrDefault(item => string.Equals(item.Id, anchor.Id, StringComparison.Ordinal));
        if (target is null)
        {
            return;
        }
        _runtimeController.SuspendMessageAutoScroll();
        ChatWorkspace.MessagesList.ScrollIntoView(target);
        // 虚拟化列表首次 ScrollIntoView 只按估算高度滚动；布局稳定后再校准一次。
        Dispatcher.BeginInvoke(new Action(() =>
        {
            _runtimeController.SuspendMessageAutoScroll();
            ChatWorkspace.MessagesList.ScrollIntoView(target);
        }), DispatcherPriority.Loaded);
    }
}
