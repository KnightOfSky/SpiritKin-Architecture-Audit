using System.IO;
using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class MusicPlaybackServiceTests
{
    [Fact]
    public void QueueModelKeepsOrderAndHonorsLoopModes()
    {
        var queue = new MusicQueueModel();
        queue.Replace(new[]
        {
            new MusicTrack("one.mp3", "One"),
            new MusicTrack("two.mp3", "Two"),
        });

        Assert.Equal(0, queue.CurrentIndex);
        Assert.Equal(1, queue.NextIndex(automatic: true));
        queue.Select(1);
        Assert.Equal(-1, queue.NextIndex(automatic: true));

        queue.LoopMode = MusicLoopMode.All;
        Assert.Equal(0, queue.NextIndex(automatic: true));
        queue.LoopMode = MusicLoopMode.One;
        Assert.Equal(1, queue.NextIndex(automatic: true));
        Assert.Equal(0, queue.PreviousIndex());
    }

    [Fact]
    public void PlaybackClampsSeekAndVolume()
    {
        using var fixture = new MusicFixture();
        var backend = new FakeMusicBackend { DurationSeconds = 120 };
        using var service = new MusicPlaybackService(_ => backend);

        service.ReplaceQueue(new[] { fixture.Song("one.mp3") }, autoplay: true);
        service.Seek(999);
        service.SetVolume(2);

        Assert.Equal(120, backend.PositionSeconds);
        Assert.Equal(1, backend.Volume);
        Assert.Equal(1, service.Snapshot().Volume);
    }

    [Fact]
    public void CorruptTrackIsSkippedWithRecoverableStatus()
    {
        using var fixture = new MusicFixture();
        var goodBackend = new FakeMusicBackend();
        using var service = new MusicPlaybackService(track =>
        {
            if (track.Title == "bad")
            {
                throw new InvalidDataException("bad header");
            }
            return goodBackend;
        });

        service.ReplaceQueue(new[] { fixture.Song("bad.mp3"), fixture.Song("good.mp3") }, autoplay: true);
        var snapshot = service.Snapshot();

        Assert.Equal("good", snapshot.CurrentTrack?.Title);
        Assert.Equal(MusicPlaybackStatus.Playing, snapshot.Status);
        Assert.Contains("已跳过 bad", snapshot.Error);
    }

    [Fact]
    public void AudioFocusDucksAndRestoresEffectiveVolume()
    {
        using var fixture = new MusicFixture();
        var backend = new FakeMusicBackend();
        using var service = new MusicPlaybackService(_ => backend);
        service.ReplaceQueue(new[] { fixture.Song("one.mp3") }, autoplay: true);
        service.SetVolume(0.8);
        var focus = new AudioFocusCoordinator(service) { Behavior = MusicAudioFocusBehavior.Duck };

        focus.Acquire("speech:1");
        Assert.Equal(0.2f, backend.Volume, 3);
        focus.Release("speech:1");

        Assert.Equal(0.8f, backend.Volume, 3);
        Assert.Equal(MusicBackendState.Playing, backend.State);
    }

    [Fact]
    public void PauseFocusOnlyResumesWhenUserAllowedIt()
    {
        using var fixture = new MusicFixture();
        var backend = new FakeMusicBackend();
        using var service = new MusicPlaybackService(_ => backend);
        service.ReplaceQueue(new[] { fixture.Song("one.mp3") }, autoplay: true);
        var focus = new AudioFocusCoordinator(service) { Behavior = MusicAudioFocusBehavior.Pause };

        focus.Acquire("speech:1");
        focus.Release("speech:1");
        Assert.Equal(MusicBackendState.Paused, backend.State);

        service.PlayOrResume();
        focus.ResumeAfterPause = true;
        focus.Acquire("speech:2");
        focus.Release("speech:2");
        Assert.Equal(MusicBackendState.Playing, backend.State);
    }

    [Fact]
    public void MultipleAudioFocusOwnersDoNotRestoreEarly()
    {
        using var fixture = new MusicFixture();
        var backend = new FakeMusicBackend();
        using var service = new MusicPlaybackService(_ => backend);
        service.ReplaceQueue(new[] { fixture.Song("one.mp3") }, autoplay: true);
        var focus = new AudioFocusCoordinator(service) { Behavior = MusicAudioFocusBehavior.Duck };

        focus.Acquire("speech:1");
        focus.Acquire("voice:1");
        focus.Release("speech:1");
        Assert.Equal(0.2f, backend.Volume, 3);
        focus.Release("voice:1");

        Assert.Equal(0.8f, backend.Volume, 3);
    }

    [Fact]
    public void CommandInboxSkipsOldCommandsAndReadsNewOnesInOrder()
    {
        using var fixture = new MusicFixture();
        var path = Path.Combine(fixture.Root, "commands.jsonl");
        File.WriteAllText(path, Command("old", "pause") + Environment.NewLine);
        var inbox = new MusicCommandInbox(path, skipExisting: true);
        File.AppendAllText(path, Command("new1", "play") + Environment.NewLine + Command("new2", "next") + Environment.NewLine);

        var commands = inbox.ReadNew();

        Assert.Equal(new[] { "new1", "new2" }, commands.Select(command => command.CommandId));
        Assert.Empty(inbox.ReadNew());
    }

    [Fact]
    public void RemoteTrackIsIgnoredUnlessExplicitlyAllowed()
    {
        var blocked = MusicPlaybackService.ExpandTracks(new[] { "https://example.com/music.mp3" }, allowRemote: false);
        var allowed = MusicPlaybackService.ExpandTracks(new[] { "https://example.com/music.mp3" }, allowRemote: true);

        Assert.Empty(blocked);
        Assert.True(Assert.Single(allowed).IsRemote);
    }

    [Fact]
    public void QueueCanRemoveCurrentTrackAndClearFromRemoteCommands()
    {
        using var fixture = new MusicFixture();
        var backends = new List<FakeMusicBackend>();
        using var service = new MusicPlaybackService(_ =>
        {
            var backend = new FakeMusicBackend();
            backends.Add(backend);
            return backend;
        });
        service.ReplaceQueue(new[] { fixture.Song("one.mp3"), fixture.Song("two.mp3") }, autoplay: true);

        service.RemoveAt(0);
        var remaining = service.Snapshot();
        Assert.Single(remaining.Queue);
        Assert.Equal("two", remaining.CurrentTrack?.Title);
        Assert.Equal(MusicPlaybackStatus.Playing, remaining.Status);

        service.ClearQueue();
        var cleared = service.Snapshot();
        Assert.Empty(cleared.Queue);
        Assert.Equal(MusicPlaybackStatus.Empty, cleared.Status);
    }

    private static string Command(string id, string action)
    {
        return JsonSerializer.Serialize(new
        {
            schema_version = "spiritkin.music_command.v1",
            command_id = id,
            action,
            arguments = new { },
        });
    }

    private sealed class MusicFixture : IDisposable
    {
        internal MusicFixture()
        {
            Root = Path.Combine(Path.GetTempPath(), "spiritkin-music-tests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Root);
        }

        internal string Root { get; }

        internal string Song(string name)
        {
            var path = Path.Combine(Root, name);
            File.WriteAllBytes(path, new byte[] { 0 });
            return path;
        }

        public void Dispose() => Directory.Delete(Root, recursive: true);
    }

    private sealed class FakeMusicBackend : IMusicPlaybackBackend
    {
        public event EventHandler<MusicBackendStoppedEventArgs>? PlaybackStopped;
        public double PositionSeconds { get; set; }
        public double DurationSeconds { get; set; } = 180;
        public float Volume { get; set; }
        public MusicBackendState State { get; private set; }
        public bool Disposed { get; private set; }

        public void Play() => State = MusicBackendState.Playing;
        public void Pause() => State = MusicBackendState.Paused;
        public void Stop() => State = MusicBackendState.Stopped;
        public void Complete(Exception? error = null) => PlaybackStopped?.Invoke(this, new MusicBackendStoppedEventArgs(error));
        public void Dispose() => Disposed = true;
    }
}
