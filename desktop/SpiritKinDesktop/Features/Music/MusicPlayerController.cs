using SpiritKinDesktop.Controls;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed class MusicPlayerController : IDisposable
{
    private readonly MusicPlayerBarView _view;
    private readonly RowDefinition _row;
    private readonly RuntimeController _runtime;
    private readonly MusicPlaybackService _service;
    private readonly AudioFocusCoordinator _audioFocus;
    private readonly MusicCommandInbox _inbox;
    private readonly string _statusPath;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private bool _disposed;

    internal MusicPlayerController(
        MusicPlayerBarView view,
        RowDefinition row,
        RuntimeController runtime,
        string rootDir,
        MusicPlaybackService? service = null)
    {
        _view = view;
        _row = row;
        _runtime = runtime;
        _service = service ?? new MusicPlaybackService();
        _audioFocus = new AudioFocusCoordinator(_service);
        var commandPath = Environment.GetEnvironmentVariable("SPIRITKIN_MUSIC_COMMAND_PATH");
        if (string.IsNullOrWhiteSpace(commandPath))
        {
            commandPath = Path.Combine(rootDir, "state", "music", "commands.jsonl");
        }
        else if (!Path.IsPathRooted(commandPath))
        {
            commandPath = Path.GetFullPath(Path.Combine(rootDir, commandPath));
        }
        _inbox = new MusicCommandInbox(commandPath);
        var statusPath = Environment.GetEnvironmentVariable("SPIRITKIN_MUSIC_STATUS_PATH");
        if (string.IsNullOrWhiteSpace(statusPath))
        {
            statusPath = Path.Combine(rootDir, "state", "music", "status.json");
        }
        else if (!Path.IsPathRooted(statusPath))
        {
            statusPath = Path.GetFullPath(Path.Combine(rootDir, statusPath));
        }
        _statusPath = statusPath;

        _view.PreviousRequested += (_, _) => _service.Previous();
        _view.PlayPauseRequested += (_, _) => TogglePlayback();
        _view.NextRequested += (_, _) => _service.Next();
        _view.CloseRequested += (_, _) => HideAndStop();
        _view.AddFilesRequested += (_, _) => AddFiles();
        _view.AddFolderRequested += (_, _) => AddFolder();
        _view.SeekRequested += _service.Seek;
        _view.VolumeChanged += _service.SetVolume;
        _view.QueueTrackRequested += _service.Select;
        _view.LoopModeChanged += _service.SetLoopMode;
        _view.AudioFocusBehaviorChanged += behavior => _audioFocus.Behavior = behavior;
        _view.ResumeAfterPauseChanged += enabled => _audioFocus.ResumeAfterPause = enabled;
        _service.StateChanged += (_, snapshot) =>
        {
            PersistSnapshot(snapshot);
            _view.Dispatcher.InvokeAsync(() => _view.Render(snapshot));
        };
        _runtime.EventApplied += Runtime_EventApplied;
        _runtime.DesktopSpeechActivityChanged += Runtime_DesktopSpeechActivityChanged;
        _timer.Tick += (_, _) => Tick();
        _timer.Start();
        _view.Render(_service.Snapshot());
        PersistSnapshot(_service.Snapshot());
    }

    internal MusicPlaybackService Service => _service;
    internal AudioFocusCoordinator AudioFocus => _audioFocus;

    internal void Show()
    {
        _row.Height = new GridLength(58);
        _view.Visibility = Visibility.Visible;
    }

    private void HideAndStop()
    {
        _service.Stop();
        _row.Height = new GridLength(0);
        _view.Visibility = Visibility.Collapsed;
    }

    private void TogglePlayback()
    {
        if (_service.Snapshot().Status == MusicPlaybackStatus.Playing)
        {
            _service.Pause();
        }
        else
        {
            _service.PlayOrResume();
        }
    }

    private void AddFiles()
    {
        var files = _view.PickFiles();
        if (files.Length == 0)
        {
            return;
        }
        var autoplay = _service.Snapshot().Queue.Count == 0;
        _service.AppendQueue(files, autoplay);
        Show();
    }

    private void AddFolder()
    {
        var folder = _view.PickFolder();
        if (string.IsNullOrWhiteSpace(folder))
        {
            return;
        }
        var autoplay = _service.Snapshot().Queue.Count == 0;
        _service.AppendQueue(new[] { folder }, autoplay);
        Show();
    }

    private void Tick()
    {
        foreach (var command in _inbox.ReadNew())
        {
            ApplyCommand(command);
        }
        if (_view.Visibility == Visibility.Visible)
        {
            _view.Render(_service.Snapshot());
        }
        PersistSnapshot(_service.Snapshot());
    }

    internal void ApplyCommand(MusicCommand command)
    {
        var arguments = command.Arguments;
        switch (command.Action)
        {
            case "play":
            case "queue":
                var paths = ReadStrings(arguments, "paths");
                var autoplay = ReadBool(arguments, "autoplay", command.Action == "play");
                var replace = ReadBool(arguments, "replace", command.Action == "play");
                if (replace)
                {
                    _service.ReplaceQueue(paths, autoplay);
                }
                else
                {
                    _service.AppendQueue(paths, autoplay);
                }
                Show();
                break;
            case "play_url":
                if (RemoteMusicEnabled())
                {
                    _service.ReplaceQueue(new[] { ReadString(arguments, "url") }, ReadBool(arguments, "autoplay", true), allowRemote: true);
                    Show();
                }
                break;
            case "pause":
                _service.Pause();
                break;
            case "resume":
                _service.PlayOrResume();
                Show();
                break;
            case "stop":
                _service.Stop();
                break;
            case "next":
                _service.Next();
                break;
            case "previous":
                _service.Previous();
                break;
            case "seek":
                _service.Seek(ReadDouble(arguments, "seconds", 0));
                break;
            case "volume":
                _service.SetVolume(ReadDouble(arguments, "volume", 0.8));
                break;
            case "loop":
                if (Enum.TryParse<MusicLoopMode>(ReadString(arguments, "mode"), true, out var mode))
                {
                    _service.SetLoopMode(mode);
                }
                break;
            case "select":
                _service.Select((int)ReadDouble(arguments, "index", 0));
                Show();
                break;
            case "remove":
                _service.RemoveAt((int)ReadDouble(arguments, "index", 0));
                break;
            case "clear":
                _service.ClearQueue();
                break;
        }
    }

    private void PersistSnapshot(MusicPlaybackSnapshot snapshot)
    {
        try
        {
            var directory = Path.GetDirectoryName(_statusPath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }
            var current = snapshot.CurrentTrack;
            var payload = new
            {
                schema_version = "spiritkin.music_status.v1",
                updated_at = DateTimeOffset.UtcNow.ToString("O"),
                status = snapshot.Status.ToString().ToLowerInvariant(),
                queue = snapshot.Queue.Select(track => new
                {
                    source = track.Source,
                    title = track.Title,
                    is_remote = track.IsRemote,
                }).ToArray(),
                queue_count = snapshot.Queue.Count,
                current_index = snapshot.CurrentIndex,
                current_track = current is null ? null : new
                {
                    source = current.Source,
                    title = current.Title,
                    is_remote = current.IsRemote,
                },
                position_seconds = snapshot.PositionSeconds,
                duration_seconds = snapshot.DurationSeconds,
                volume = snapshot.Volume,
                loop_mode = snapshot.LoopMode.ToString().ToLowerInvariant(),
                error = snapshot.Error,
            };
            var temporary = _statusPath + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(payload));
            File.Move(temporary, _statusPath, true);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private void Runtime_EventApplied(RuntimeEvent ev)
    {
        if (ev.Type == RealtimeContract.Events.SpeechStarted)
        {
            _audioFocus.Acquire("speech:" + ReadString(ev.Payload, "speech_id"));
        }
        else if (ev.Type is RealtimeContract.Events.SpeechEnded or RealtimeContract.Events.SpeechInterrupted)
        {
            _audioFocus.Release("speech:" + ReadString(ev.Payload, "speech_id"));
        }
        else if (ev.Type == RealtimeContract.Events.VoiceCallState)
        {
            var owner = "voice:" + ReadString(ev.Payload, "call_id");
            var phase = ReadString(ev.Payload, "phase");
            if (phase == "speaking")
            {
                _audioFocus.Acquire(owner);
            }
            else if (phase is "listening" or "interrupted" or "ended" or "error")
            {
                _audioFocus.Release(owner);
            }
        }
    }

    private void Runtime_DesktopSpeechActivityChanged(string owner, bool active)
    {
        if (active)
        {
            _audioFocus.Acquire(owner);
        }
        else
        {
            _audioFocus.Release(owner);
        }
    }

    private static bool RemoteMusicEnabled()
    {
        var value = (Environment.GetEnvironmentVariable("SPIRITKIN_MUSIC_REMOTE_URLS") ?? "").Trim().ToLowerInvariant();
        return value is "1" or "true" or "yes" or "on";
    }

    private static IReadOnlyList<string> ReadStrings(JsonElement payload, string name)
    {
        return payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.String).Select(item => item.GetString() ?? "").Where(item => item.Length > 0).ToList()
            : Array.Empty<string>();
    }

    private static string ReadString(JsonElement payload, string name)
    {
        return payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";
    }

    private static bool ReadBool(JsonElement payload, string name, bool fallback)
    {
        return payload.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False ? value.GetBoolean() : fallback;
    }

    private static double ReadDouble(JsonElement payload, string name, double fallback)
    {
        return payload.TryGetProperty(name, out var value) && value.TryGetDouble(out var number) ? number : fallback;
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _timer.Stop();
        _runtime.EventApplied -= Runtime_EventApplied;
        _runtime.DesktopSpeechActivityChanged -= Runtime_DesktopSpeechActivityChanged;
        _audioFocus.Reset();
        _service.Dispose();
    }
}
