using System;
using System.Collections.Generic;

namespace SpiritKinDesktop;

internal enum MusicAudioFocusBehavior
{
    Duck,
    Pause,
    Ignore,
}

internal sealed class AudioFocusCoordinator
{
    private readonly MusicPlaybackService _music;
    private readonly HashSet<string> _owners = new(StringComparer.OrdinalIgnoreCase);
    private bool _wasPlaying;

    internal AudioFocusCoordinator(MusicPlaybackService music)
    {
        _music = music;
    }

    internal MusicAudioFocusBehavior Behavior { get; set; } = MusicAudioFocusBehavior.Duck;
    internal bool ResumeAfterPause { get; set; }
    internal int ActiveOwnerCount => _owners.Count;

    internal void Acquire(string owner)
    {
        var normalized = (owner ?? "").Trim();
        if (normalized.Length == 0 || !_owners.Add(normalized) || _owners.Count > 1)
        {
            return;
        }
        _wasPlaying = _music.Snapshot().Status == MusicPlaybackStatus.Playing;
        if (!_wasPlaying)
        {
            return;
        }
        if (Behavior == MusicAudioFocusBehavior.Duck)
        {
            _music.SetFocusMultiplier(0.25);
        }
        else if (Behavior == MusicAudioFocusBehavior.Pause)
        {
            _music.Pause();
        }
    }

    internal void Release(string owner)
    {
        if (!_owners.Remove((owner ?? "").Trim()) || _owners.Count > 0)
        {
            return;
        }
        if (Behavior == MusicAudioFocusBehavior.Duck)
        {
            _music.SetFocusMultiplier(1);
        }
        else if (Behavior == MusicAudioFocusBehavior.Pause && _wasPlaying && ResumeAfterPause)
        {
            _music.PlayOrResume();
        }
        _wasPlaying = false;
    }

    internal void Reset()
    {
        _owners.Clear();
        _music.SetFocusMultiplier(1);
        _wasPlaying = false;
    }
}
