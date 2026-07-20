using Microsoft.Win32;
using System;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class MusicPlayerBarView : System.Windows.Controls.UserControl
{
    private bool _rendering;

    internal event EventHandler? PreviousRequested;
    internal event EventHandler? PlayPauseRequested;
    internal event EventHandler? NextRequested;
    internal event EventHandler? CloseRequested;
    internal event EventHandler? AddFilesRequested;
    internal event EventHandler? AddFolderRequested;
    internal event Action<double>? SeekRequested;
    internal event Action<double>? VolumeChanged;
    internal event Action<int>? QueueTrackRequested;
    internal event Action<MusicLoopMode>? LoopModeChanged;
    internal event Action<MusicAudioFocusBehavior>? AudioFocusBehaviorChanged;
    internal event Action<bool>? ResumeAfterPauseChanged;

    public MusicPlayerBarView()
    {
        InitializeComponent();
    }

    internal void Render(MusicPlaybackSnapshot snapshot)
    {
        _rendering = true;
        try
        {
            TrackTitleText.Text = snapshot.CurrentTrack?.Title ?? "未选择音乐";
            PlaybackStatusText.Text = string.IsNullOrWhiteSpace(snapshot.Error)
                ? snapshot.Status switch
                {
                    MusicPlaybackStatus.Playing => "正在播放",
                    MusicPlaybackStatus.Paused => "已暂停",
                    MusicPlaybackStatus.Error => "播放失败",
                    _ => "",
                }
                : snapshot.Error;
            PlayPauseButton.Content = snapshot.Status == MusicPlaybackStatus.Playing ? "\uE769" : "\uE768";
            PlayPauseButton.ToolTip = snapshot.Status == MusicPlaybackStatus.Playing ? "暂停" : "播放";
            PositionSlider.Maximum = Math.Max(1, snapshot.DurationSeconds);
            PositionSlider.Value = Math.Clamp(snapshot.PositionSeconds, 0, PositionSlider.Maximum);
            PositionText.Text = FormatTime(snapshot.PositionSeconds);
            DurationText.Text = FormatTime(snapshot.DurationSeconds);
            VolumeSlider.Value = snapshot.Volume;
            QueueList.ItemsSource = snapshot.Queue;
            QueueList.SelectedIndex = snapshot.CurrentIndex;
            LoopModeBox.SelectedIndex = snapshot.LoopMode switch
            {
                MusicLoopMode.All => 1,
                MusicLoopMode.One => 2,
                _ => 0,
            };
        }
        finally
        {
            _rendering = false;
        }
    }

    internal string[] PickFiles()
    {
        var dialog = new OpenFileDialog
        {
            Multiselect = true,
            Filter = "音频文件|*.mp3;*.wav;*.flac;*.m4a;*.aac;*.ogg;*.wma;*.aif;*.aiff|所有文件|*.*",
            CheckFileExists = true,
        };
        return dialog.ShowDialog() == true ? dialog.FileNames : Array.Empty<string>();
    }

    internal string? PickFolder()
    {
        var dialog = new OpenFolderDialog
        {
            Title = "选择音乐目录",
            Multiselect = false,
        };
        return dialog.ShowDialog() == true ? dialog.FolderName : null;
    }

    private static string FormatTime(double seconds)
    {
        var value = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return value.TotalHours >= 1 ? value.ToString(@"hh\:mm\:ss") : value.ToString(@"mm\:ss");
    }

    private void PreviousButton_Click(object sender, RoutedEventArgs e) => PreviousRequested?.Invoke(this, EventArgs.Empty);
    private void PlayPauseButton_Click(object sender, RoutedEventArgs e) => PlayPauseRequested?.Invoke(this, EventArgs.Empty);
    private void NextButton_Click(object sender, RoutedEventArgs e) => NextRequested?.Invoke(this, EventArgs.Empty);
    private void CloseButton_Click(object sender, RoutedEventArgs e) => CloseRequested?.Invoke(this, EventArgs.Empty);
    private void QueueButton_Click(object sender, RoutedEventArgs e) => QueuePopup.IsOpen = !QueuePopup.IsOpen;
    private void AddFilesButton_Click(object sender, RoutedEventArgs e) => AddFilesRequested?.Invoke(this, EventArgs.Empty);
    private void AddFolderButton_Click(object sender, RoutedEventArgs e) => AddFolderRequested?.Invoke(this, EventArgs.Empty);

    private void PositionSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (!_rendering && PositionSlider.IsMouseCaptureWithin)
        {
            SeekRequested?.Invoke(e.NewValue);
        }
    }

    private void VolumeSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (!_rendering)
        {
            VolumeChanged?.Invoke(e.NewValue);
        }
    }

    private void QueueList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_rendering && QueueList.SelectedIndex >= 0)
        {
            QueueTrackRequested?.Invoke(QueueList.SelectedIndex);
        }
    }

    private void LoopModeBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_rendering && LoopModeBox.SelectedItem is ComboBoxItem item && Enum.TryParse<MusicLoopMode>(item.Tag?.ToString(), out var mode))
        {
            LoopModeChanged?.Invoke(mode);
        }
    }

    private void AudioFocusBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_rendering && AudioFocusBox.SelectedItem is ComboBoxItem item && Enum.TryParse<MusicAudioFocusBehavior>(item.Tag?.ToString(), out var behavior))
        {
            AudioFocusBehaviorChanged?.Invoke(behavior);
        }
    }

    private void ResumeAfterPauseCheckBox_Changed(object sender, RoutedEventArgs e)
    {
        if (!_rendering)
        {
            ResumeAfterPauseChanged?.Invoke(ResumeAfterPauseCheckBox.IsChecked == true);
        }
    }
}
