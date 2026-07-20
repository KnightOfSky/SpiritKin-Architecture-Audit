using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class VoiceCallControllerTests
{
    [Fact]
    public void VoiceCallStateUsesBackendSequenceAsAuthority()
    {
        var state = new VoiceCallUiState();
        state.BeginCall("call_1", preserveTranscript: false);

        Assert.True(state.Apply(Event(RealtimeContract.Events.VoiceCallState,
            """{"call_id":"call_1","phase":"listening","message":"正在聆听。","sequence":2}""")));
        Assert.False(state.Apply(Event(RealtimeContract.Events.VoiceCallState,
            """{"call_id":"call_1","phase":"connecting","sequence":1}""")));

        Assert.Equal(VoiceCallPhase.Listening, state.Phase);
        Assert.Equal(2, state.Sequence);
        Assert.Equal("正在聆听。", state.StatusText);
    }

    [Fact]
    public void VoiceCallStateRejectsEventsFromAnotherCall()
    {
        var state = new VoiceCallUiState();
        state.BeginCall("call_current", preserveTranscript: false);

        var applied = state.Apply(Event(RealtimeContract.Events.VoiceCallState,
            """{"call_id":"call_old","phase":"error","sequence":9}"""));

        Assert.False(applied);
        Assert.Equal(VoiceCallPhase.Connecting, state.Phase);
    }

    [Fact]
    public void VoiceCallTranscriptAndAsrPartialStayConsistent()
    {
        var state = new VoiceCallUiState();
        state.BeginCall("call_1", preserveTranscript: false);

        Assert.True(state.Apply(Event(RealtimeContract.Events.AsrPartial,
            """{"call_id":"call_1","text":"正在识别"}""")));
        Assert.Equal("正在识别", state.PartialTranscript);

        Assert.True(state.Apply(Event(RealtimeContract.Events.VoiceCallTranscript,
            """{"call_id":"call_1","role":"user","text":"最终文本","final":true}""")));

        Assert.Equal("", state.PartialTranscript);
        var transcript = Assert.Single(state.Transcripts);
        Assert.Equal("user", transcript.Role);
        Assert.Equal("最终文本", transcript.Text);
        Assert.True(transcript.Final);
    }

    [Fact]
    public void VoiceCallArgumentsSelectDeviceAndCanDisableSpeaker()
    {
        var arguments = VoiceCallSessionController.BuildArguments("call_1", 7, speakerEnabled: false);

        Assert.Contains("--call-mode", arguments);
        Assert.Contains("call_1", arguments);
        Assert.Contains("--no-hotword", arguments);
        Assert.Contains("--device-index", arguments);
        Assert.Contains("7", arguments);
        Assert.Contains("--no-speak", arguments);
    }

    [Fact]
    public void VoiceCallParsesCanonicalPhases()
    {
        var expected = new Dictionary<string, VoiceCallPhase>
        {
            ["idle"] = VoiceCallPhase.Idle,
            ["connecting"] = VoiceCallPhase.Connecting,
            ["listening"] = VoiceCallPhase.Listening,
            ["thinking"] = VoiceCallPhase.Thinking,
            ["speaking"] = VoiceCallPhase.Speaking,
            ["interrupted"] = VoiceCallPhase.Interrupted,
            ["reconnecting"] = VoiceCallPhase.Reconnecting,
            ["ended"] = VoiceCallPhase.Ended,
            ["error"] = VoiceCallPhase.Error,
        };
        foreach (var (value, phase) in expected)
        {
            Assert.Equal(phase, VoiceCallUiState.ParsePhase(value));
        }
    }

    private static RuntimeEvent Event(string type, string payload)
    {
        using var document = JsonDocument.Parse(payload);
        return new RuntimeEvent { Type = type, Payload = document.RootElement.Clone() };
    }
}
