using NAudio.Wave;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace SpiritKinDesktop;

internal enum MusicLoopMode
{
    Off,
    All,
    One,
}

internal enum MusicPlaybackStatus
{
    Empty,
    Stopped,
    Playing,
    Paused,
    Error,
}

internal sealed record MusicTrack(string Source, string Title, bool IsRemote = false);

internal sealed record MusicPlaybackSnapshot(
    IReadOnlyList<MusicTrack> Queue,
    int CurrentIndex,
    MusicPlaybackStatus Status,
    double PositionSeconds,
    double DurationSeconds,
    double Volume,
    MusicLoopMode LoopMode,
    string Error = "")
{
    internal MusicTrack? CurrentTrack => CurrentIndex >= 0 && CurrentIndex < Queue.Count ? Queue[CurrentIndex] : null;
}

internal sealed class MusicQueueModel
{
    private readonly List<MusicTrack> _tracks = new();

    internal IReadOnlyList<MusicTrack> Tracks => _tracks;
    internal int CurrentIndex { get; private set; } = -1;
    internal MusicLoopMode LoopMode { get; set; }

    internal void Replace(IEnumerable<MusicTrack> tracks)
    {
        _tracks.Clear();
        _tracks.AddRange(tracks);
        CurrentIndex = _tracks.Count > 0 ? 0 : -1;
    }

    internal void Append(IEnumerable<MusicTrack> tracks)
    {
        var wasEmpty = _tracks.Count == 0;
        _tracks.AddRange(tracks);
        if (wasEmpty && _tracks.Count > 0)
        {
            CurrentIndex = 0;
        }
    }

    internal bool Select(int index)
    {
        if (index < 0 || index >= _tracks.Count)
        {
            return false;
        }
        CurrentIndex = index;
        return true;
    }

    internal bool RemoveAt(int index)
    {
        if (index < 0 || index >= _tracks.Count)
        {
            return false;
        }
        _tracks.RemoveAt(index);
        if (_tracks.Count == 0)
        {
            CurrentIndex = -1;
        }
        else if (index < CurrentIndex)
        {
            CurrentIndex--;
        }
        else if (CurrentIndex >= _tracks.Count)
        {
            CurrentIndex = _tracks.Count - 1;
        }
        return true;
    }

    internal int NextIndex(bool automatic)
    {
        if (_tracks.Count == 0)
        {
            return -1;
        }
        if (automatic && LoopMode == MusicLoopMode.One && CurrentIndex >= 0)
        {
            return CurrentIndex;
        }
        var next = CurrentIndex + 1;
        if (next < _tracks.Count)
        {
            return next;
        }
        return LoopMode == MusicLoopMode.All || !automatic ? 0 : -1;
    }

    internal int PreviousIndex()
    {
        if (_tracks.Count == 0)
        {
            return -1;
        }
        return CurrentIndex > 0 ? CurrentIndex - 1 : _tracks.Count - 1;
    }
}

internal enum MusicBackendState
{
    Stopped,
    Playing,
    Paused,
}

internal sealed record MusicBackendStoppedEventArgs(Exception? Error = null);

internal interface IMusicPlaybackBackend : IDisposable
{
    event EventHandler<MusicBackendStoppedEventArgs>? PlaybackStopped;
    double PositionSeconds { get; set; }
    double DurationSeconds { get; }
    float Volume { get; set; }
    MusicBackendState State { get; }
    void Play();
    void Pause();
    void Stop();
}

internal sealed class NAudioMusicPlaybackBackend : IMusicPlaybackBackend
{
    private readonly WaveOutEvent _output = new();
    private readonly WaveStream _reader;

    internal NAudioMusicPlaybackBackend(string source, bool remote)
    {
        _reader = remote ? new MediaFoundationReader(source) : CreateLocalReader(source);
        _output.Init(_reader);
        _output.PlaybackStopped += (_, args) => PlaybackStopped?.Invoke(this, new MusicBackendStoppedEventArgs(args.Exception));
    }

    public event EventHandler<MusicBackendStoppedEventArgs>? PlaybackStopped;
    public double PositionSeconds
    {
        get => _reader.CurrentTime.TotalSeconds;
        set => _reader.CurrentTime = TimeSpan.FromSeconds(Math.Clamp(value, 0, DurationSeconds));
    }
    public double DurationSeconds => Math.Max(0, _reader.TotalTime.TotalSeconds);
    public float Volume { get => _output.Volume; set => _output.Volume = Math.Clamp(value, 0, 1); }
    public MusicBackendState State => _output.PlaybackState switch
    {
        PlaybackState.Playing => MusicBackendState.Playing,
        PlaybackState.Paused => MusicBackendState.Paused,
        _ => MusicBackendState.Stopped,
    };

    public void Play() => _output.Play();
    public void Pause() => _output.Pause();
    public void Stop() => _output.Stop();

    public void Dispose()
    {
        _output.Dispose();
        _reader.Dispose();
    }

    private static WaveStream CreateLocalReader(string path)
    {
        return Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".mp3" or ".wav" or ".aiff" or ".aif" => new AudioFileReader(path),
            _ => new MediaFoundationReader(path),
        };
    }
}

internal sealed class MusicPlaybackService : IDisposable
{
    internal static readonly HashSet<string> SupportedExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".wma",
    };

    private readonly object _gate = new();
    private readonly Func<MusicTrack, IMusicPlaybackBackend> _backendFactory;
    private readonly MusicQueueModel _queue = new();
    private IMusicPlaybackBackend? _backend;
    private MusicPlaybackStatus _status = MusicPlaybackStatus.Empty;
    private double _volume = 0.8;
    private double _focusMultiplier = 1;
    private bool _manualTransition;
    private string _error = "";

    internal MusicPlaybackService(Func<MusicTrack, IMusicPlaybackBackend>? backendFactory = null)
    {
        _backendFactory = backendFactory ?? (track => new NAudioMusicPlaybackBackend(track.Source, track.IsRemote));
    }

    internal event EventHandler<MusicPlaybackSnapshot>? StateChanged;

    internal MusicPlaybackSnapshot Snapshot()
    {
        lock (_gate)
        {
            return SnapshotUnsafe();
        }
    }

    internal void ReplaceQueue(IEnumerable<string> sources, bool autoplay = true, bool allowRemote = false)
    {
        lock (_gate)
        {
            _queue.Replace(ExpandTracks(sources, allowRemote));
            StopBackendUnsafe();
            _status = _queue.Tracks.Count == 0 ? MusicPlaybackStatus.Empty : MusicPlaybackStatus.Stopped;
            _error = "";
            if (autoplay && _queue.CurrentIndex >= 0)
            {
                PlayIndexUnsafe(_queue.CurrentIndex, new HashSet<int>());
            }
            PublishUnsafe();
        }
    }

    internal void AppendQueue(IEnumerable<string> sources, bool autoplay = false, bool allowRemote = false)
    {
        lock (_gate)
        {
            var wasEmpty = _queue.Tracks.Count == 0;
            _queue.Append(ExpandTracks(sources, allowRemote));
            if (wasEmpty)
            {
                _status = _queue.Tracks.Count == 0 ? MusicPlaybackStatus.Empty : MusicPlaybackStatus.Stopped;
            }
            if (autoplay && _queue.CurrentIndex >= 0)
            {
                PlayIndexUnsafe(_queue.CurrentIndex, new HashSet<int>());
            }
            PublishUnsafe();
        }
    }

    internal void PlayOrResume()
    {
        lock (_gate)
        {
            if (_backend is null && _queue.CurrentIndex >= 0)
            {
                PlayIndexUnsafe(_queue.CurrentIndex, new HashSet<int>());
            }
            else if (_backend is not null)
            {
                _backend.Play();
                _status = MusicPlaybackStatus.Playing;
            }
            PublishUnsafe();
        }
    }

    internal void Pause()
    {
        lock (_gate)
        {
            _backend?.Pause();
            if (_backend is not null)
            {
                _status = MusicPlaybackStatus.Paused;
            }
            PublishUnsafe();
        }
    }

    internal void Stop()
    {
        lock (_gate)
        {
            StopBackendUnsafe();
            _status = _queue.Tracks.Count == 0 ? MusicPlaybackStatus.Empty : MusicPlaybackStatus.Stopped;
            PublishUnsafe();
        }
    }

    internal void ClearQueue()
    {
        lock (_gate)
        {
            StopBackendUnsafe();
            _queue.Replace(Array.Empty<MusicTrack>());
            _status = MusicPlaybackStatus.Empty;
            _error = "";
            PublishUnsafe();
        }
    }

    internal void RemoveAt(int index)
    {
        lock (_gate)
        {
            var wasCurrent = index == _queue.CurrentIndex;
            var resume = wasCurrent && _status == MusicPlaybackStatus.Playing;
            if (!_queue.RemoveAt(index))
            {
                return;
            }
            if (wasCurrent)
            {
                StopBackendUnsafe();
                _status = _queue.Tracks.Count == 0 ? MusicPlaybackStatus.Empty : MusicPlaybackStatus.Stopped;
                if (resume && _queue.CurrentIndex >= 0)
                {
                    PlayIndexUnsafe(_queue.CurrentIndex, new HashSet<int>());
                }
            }
            PublishUnsafe();
        }
    }

    internal void Next() => MoveTo(_queue.NextIndex(automatic: false));
    internal void Previous() => MoveTo(_queue.PreviousIndex());
    internal void Select(int index) => MoveTo(index);

    internal void Seek(double seconds)
    {
        lock (_gate)
        {
            if (_backend is not null)
            {
                _backend.PositionSeconds = Math.Clamp(seconds, 0, _backend.DurationSeconds);
            }
            PublishUnsafe();
        }
    }

    internal void SetVolume(double volume)
    {
        lock (_gate)
        {
            _volume = Math.Clamp(volume, 0, 1);
            ApplyEffectiveVolumeUnsafe();
            PublishUnsafe();
        }
    }

    internal void SetLoopMode(MusicLoopMode mode)
    {
        lock (_gate)
        {
            _queue.LoopMode = mode;
            PublishUnsafe();
        }
    }

    internal void SetFocusMultiplier(double multiplier)
    {
        lock (_gate)
        {
            _focusMultiplier = Math.Clamp(multiplier, 0, 1);
            ApplyEffectiveVolumeUnsafe();
            PublishUnsafe();
        }
    }

    private void MoveTo(int index)
    {
        lock (_gate)
        {
            if (_queue.Select(index))
            {
                PlayIndexUnsafe(index, new HashSet<int>());
                PublishUnsafe();
            }
        }
    }

    private void PlayIndexUnsafe(int index, HashSet<int> attempted)
    {
        if (!_queue.Select(index) || !attempted.Add(index))
        {
            _status = MusicPlaybackStatus.Error;
            _error = "队列中没有可播放的音频。";
            return;
        }
        StopBackendUnsafe();
        var track = _queue.Tracks[index];
        try
        {
            var backend = _backendFactory(track);
            backend.PlaybackStopped += Backend_PlaybackStopped;
            _backend = backend;
            ApplyEffectiveVolumeUnsafe();
            backend.Play();
            _status = MusicPlaybackStatus.Playing;
            _error = "";
        }
        catch (Exception ex)
        {
            _status = MusicPlaybackStatus.Error;
            var skippedError = $"已跳过 {track.Title}：{ex.Message}";
            _error = skippedError;
            var next = _queue.NextIndex(automatic: false);
            if (next >= 0 && !attempted.Contains(next))
            {
                PlayIndexUnsafe(next, attempted);
                if (_status == MusicPlaybackStatus.Playing)
                {
                    _error = skippedError;
                }
            }
        }
    }

    private void Backend_PlaybackStopped(object? sender, MusicBackendStoppedEventArgs e)
    {
        lock (_gate)
        {
            if (_manualTransition || !ReferenceEquals(sender, _backend))
            {
                return;
            }
            if (e.Error is not null)
            {
                _error = $"播放中断，已尝试下一首：{e.Error.Message}";
                _status = MusicPlaybackStatus.Error;
            }
            var playbackError = _error;
            var next = _queue.NextIndex(automatic: true);
            if (next >= 0)
            {
                PlayIndexUnsafe(next, new HashSet<int>());
                if (e.Error is not null && _status == MusicPlaybackStatus.Playing)
                {
                    _error = playbackError;
                }
            }
            else if (_status != MusicPlaybackStatus.Error)
            {
                _status = MusicPlaybackStatus.Stopped;
            }
            PublishUnsafe();
        }
    }

    private void StopBackendUnsafe()
    {
        var backend = _backend;
        _backend = null;
        if (backend is null)
        {
            return;
        }
        _manualTransition = true;
        try
        {
            backend.PlaybackStopped -= Backend_PlaybackStopped;
            backend.Stop();
            backend.Dispose();
        }
        finally
        {
            _manualTransition = false;
        }
    }

    private void ApplyEffectiveVolumeUnsafe()
    {
        if (_backend is not null)
        {
            _backend.Volume = (float)Math.Clamp(_volume * _focusMultiplier, 0, 1);
        }
    }

    private MusicPlaybackSnapshot SnapshotUnsafe()
    {
        return new MusicPlaybackSnapshot(
            _queue.Tracks.ToList(),
            _queue.CurrentIndex,
            _status,
            _backend?.PositionSeconds ?? 0,
            _backend?.DurationSeconds ?? 0,
            _volume,
            _queue.LoopMode,
            _error);
    }

    private void PublishUnsafe()
    {
        StateChanged?.Invoke(this, SnapshotUnsafe());
    }

    internal static IReadOnlyList<MusicTrack> ExpandTracks(IEnumerable<string> sources, bool allowRemote)
    {
        var tracks = new List<MusicTrack>();
        foreach (var raw in sources ?? Array.Empty<string>())
        {
            var source = (raw ?? "").Trim();
            if (source.Length == 0)
            {
                continue;
            }
            if (Uri.TryCreate(source, UriKind.Absolute, out var uri) && uri.Scheme is "http" or "https")
            {
                if (allowRemote)
                {
                    tracks.Add(new MusicTrack(source, Path.GetFileName(uri.LocalPath).Trim() is { Length: > 0 } name ? name : uri.Host, true));
                }
                continue;
            }
            if (Directory.Exists(source))
            {
                tracks.AddRange(Directory.EnumerateFiles(source, "*", SearchOption.TopDirectoryOnly)
                    .Where(path => SupportedExtensions.Contains(Path.GetExtension(path)))
                    .OrderBy(path => path, StringComparer.CurrentCultureIgnoreCase)
                    .Select(path => new MusicTrack(Path.GetFullPath(path), Path.GetFileNameWithoutExtension(path))));
            }
            else if (File.Exists(source) && SupportedExtensions.Contains(Path.GetExtension(source)))
            {
                tracks.Add(new MusicTrack(Path.GetFullPath(source), Path.GetFileNameWithoutExtension(source)));
            }
        }
        return tracks;
    }

    public void Dispose()
    {
        lock (_gate)
        {
            StopBackendUnsafe();
        }
    }
}
