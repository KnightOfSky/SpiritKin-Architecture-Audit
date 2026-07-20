using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed record VoiceInputDevice(int Index, string Name)
{
    public override string ToString() => Name;
}

public partial class VoiceCallWindow : Window
{
    private readonly DispatcherTimer _elapsedTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private DateTime _startedAt = DateTime.UtcNow;
    private bool _bindingDevices;

    internal event EventHandler? MicToggleRequested;
    internal event EventHandler? SpeakerToggleRequested;
    internal event EventHandler? EndRequested;
    internal event EventHandler? RetryRequested;
    internal event EventHandler? TextFallbackRequested;
    internal event Action<VoiceInputDevice>? DeviceChanged;

    public VoiceCallWindow()
    {
        InitializeComponent();
        _elapsedTimer.Tick += (_, _) => UpdateElapsed();
        _elapsedTimer.Start();
        Closed += (_, _) => _elapsedTimer.Stop();
    }

    internal void ResetElapsed()
    {
        _startedAt = DateTime.UtcNow;
        UpdateElapsed();
    }

    internal void Render(VoiceCallUiState state)
    {
        StatusText.Text = VoiceCallUiState.DefaultStatus(state.Phase);
        StatusDetailText.Text = state.StatusText == StatusText.Text ? "" : state.StatusText;
        PartialTranscriptText.Text = state.PartialTranscript;
        PartialTranscriptText.Visibility = string.IsNullOrWhiteSpace(state.PartialTranscript)
            ? Visibility.Collapsed
            : Visibility.Visible;
        RecoveryActionsPanel.Visibility = state.Phase == VoiceCallPhase.Error ? Visibility.Visible : Visibility.Collapsed;
        ConnectionDetailText.Text = state.Phase == VoiceCallPhase.Reconnecting ? "连接中断，正在恢复" : "本地流式 ASR / TTS";
        StateIconText.Text = state.Phase switch
        {
            VoiceCallPhase.Thinking => "\uE9CE",
            VoiceCallPhase.Speaking => "\uE767",
            VoiceCallPhase.Error => "\uE783",
            VoiceCallPhase.Ended => "\uE71A",
            _ => "\uE720",
        };
        StateIconText.Foreground = ResolveBrush(state.Phase == VoiceCallPhase.Error ? "FantasyDangerBrush" : "FantasyInfoBrush");
        RenderTranscripts(state.Transcripts);
    }

    internal void SetDevices(IReadOnlyList<VoiceInputDevice> devices, int? selectedIndex)
    {
        _bindingDevices = true;
        try
        {
            DeviceBox.ItemsSource = devices;
            DeviceBox.SelectedItem = devices.FirstOrDefault(device => device.Index == selectedIndex) ?? devices.FirstOrDefault();
            DeviceBox.IsEnabled = devices.Count > 0;
        }
        finally
        {
            _bindingDevices = false;
        }
    }

    internal void SetMicMuted(bool muted)
    {
        MicToggleButton.Content = muted ? "\uE74F" : "\uE720";
        MicToggleButton.ToolTip = muted ? "开启麦克风" : "静音麦克风";
    }

    internal void SetSpeakerEnabled(bool enabled)
    {
        SpeakerToggleButton.Content = enabled ? "\uE767" : "\uE74F";
        SpeakerToggleButton.ToolTip = enabled ? "关闭扬声器" : "开启扬声器";
    }

    private void RenderTranscripts(IReadOnlyList<VoiceCallTranscript> transcripts)
    {
        TranscriptPanel.Children.Clear();
        foreach (var transcript in transcripts)
        {
            var roleLabel = string.Equals(transcript.Role, "user", StringComparison.OrdinalIgnoreCase) ? "你" : "SpiritKin";
            var block = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            block.Children.Add(new TextBlock
            {
                Text = roleLabel,
                FontSize = 10,
                Foreground = ResolveBrush("FantasyMutedBrush"),
            });
            block.Children.Add(new TextBlock
            {
                Text = transcript.Text,
                Margin = new Thickness(0, 3, 0, 0),
                FontSize = 13,
                Foreground = ResolveBrush("FantasyTextBrush"),
                TextWrapping = TextWrapping.Wrap,
            });
            TranscriptPanel.Children.Add(block);
        }
        TranscriptScrollViewer.ScrollToEnd();
    }

    private Brush ResolveBrush(string key)
    {
        return TryFindResource(key) as Brush ?? Brushes.Gray;
    }

    private void UpdateElapsed()
    {
        var elapsed = DateTime.UtcNow - _startedAt;
        ElapsedText.Text = elapsed.TotalHours >= 1 ? elapsed.ToString(@"hh\:mm\:ss") : elapsed.ToString(@"mm\:ss");
    }

    private void DeviceBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_bindingDevices && DeviceBox.SelectedItem is VoiceInputDevice device)
        {
            DeviceChanged?.Invoke(device);
        }
    }

    private void MicToggleButton_Click(object sender, RoutedEventArgs e) => MicToggleRequested?.Invoke(this, EventArgs.Empty);
    private void SpeakerToggleButton_Click(object sender, RoutedEventArgs e) => SpeakerToggleRequested?.Invoke(this, EventArgs.Empty);
    private void EndButton_Click(object sender, RoutedEventArgs e) => EndRequested?.Invoke(this, EventArgs.Empty);
    private void RetryButton_Click(object sender, RoutedEventArgs e) => RetryRequested?.Invoke(this, EventArgs.Empty);
    private void TextFallbackButton_Click(object sender, RoutedEventArgs e) => TextFallbackRequested?.Invoke(this, EventArgs.Empty);
}
