using System;
using System.Collections.Generic;
using System.Text.Json;

namespace SpiritKinDesktop;

internal enum VoiceCallPhase
{
    Idle,
    Connecting,
    Listening,
    Thinking,
    Speaking,
    Interrupted,
    Reconnecting,
    Ended,
    Error,
}

internal sealed record VoiceCallTranscript(string Role, string Text, bool Final);

internal sealed class VoiceCallUiState
{
    private readonly List<VoiceCallTranscript> _transcripts = new();

    internal string CallId { get; private set; } = "";
    internal VoiceCallPhase Phase { get; private set; } = VoiceCallPhase.Idle;
    internal string StatusText { get; private set; } = "准备通话";
    internal string PartialTranscript { get; private set; } = "";
    internal long Sequence { get; private set; }
    internal IReadOnlyList<VoiceCallTranscript> Transcripts => _transcripts;

    internal void BeginCall(string callId, bool preserveTranscript = true)
    {
        CallId = (callId ?? "").Trim();
        Sequence = 0;
        PartialTranscript = "";
        Phase = VoiceCallPhase.Connecting;
        StatusText = "正在连接";
        if (!preserveTranscript)
        {
            _transcripts.Clear();
        }
    }

    internal void ApplyLocalPhase(VoiceCallPhase phase, string message = "")
    {
        Phase = phase;
        StatusText = string.IsNullOrWhiteSpace(message) ? DefaultStatus(phase) : message.Trim();
        if (phase is VoiceCallPhase.Ended or VoiceCallPhase.Error)
        {
            PartialTranscript = "";
        }
    }

    internal bool Apply(RuntimeEvent ev)
    {
        if (ev.Payload.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        var eventCallId = ReadString(ev.Payload, "call_id");
        if (!string.IsNullOrWhiteSpace(CallId)
            && !string.IsNullOrWhiteSpace(eventCallId)
            && !string.Equals(CallId, eventCallId, StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        if (ev.Type == RealtimeContract.Events.VoiceCallState)
        {
            var sequence = ReadInt64(ev.Payload, "sequence");
            if (sequence > 0 && sequence <= Sequence)
            {
                return false;
            }
            if (sequence > 0)
            {
                Sequence = sequence;
            }
            Phase = ParsePhase(ReadString(ev.Payload, "phase"));
            var message = ReadString(ev.Payload, "message");
            StatusText = string.IsNullOrWhiteSpace(message) ? DefaultStatus(Phase) : message;
            return true;
        }
        if (ev.Type == RealtimeContract.Events.VoiceCallTranscript)
        {
            var text = ReadString(ev.Payload, "text").Trim();
            if (text.Length == 0)
            {
                return false;
            }
            var role = ReadString(ev.Payload, "role");
            var final = !ev.Payload.TryGetProperty("final", out var finalElement) || finalElement.ValueKind != JsonValueKind.False;
            _transcripts.Add(new VoiceCallTranscript(role, text, final));
            if (_transcripts.Count > 60)
            {
                _transcripts.RemoveRange(0, _transcripts.Count - 60);
            }
            PartialTranscript = "";
            return true;
        }
        if (ev.Type == RealtimeContract.Events.AsrPartial)
        {
            PartialTranscript = ReadString(ev.Payload, "text").Trim();
            return true;
        }
        if (ev.Type == RealtimeContract.Events.AsrFinal)
        {
            PartialTranscript = "";
            return true;
        }
        return false;
    }

    internal static VoiceCallPhase ParsePhase(string value)
    {
        return (value ?? "").Trim().ToLowerInvariant() switch
        {
            "connecting" => VoiceCallPhase.Connecting,
            "listening" => VoiceCallPhase.Listening,
            "thinking" => VoiceCallPhase.Thinking,
            "speaking" => VoiceCallPhase.Speaking,
            "interrupted" => VoiceCallPhase.Interrupted,
            "reconnecting" => VoiceCallPhase.Reconnecting,
            "ended" => VoiceCallPhase.Ended,
            "error" => VoiceCallPhase.Error,
            _ => VoiceCallPhase.Idle,
        };
    }

    internal static string DefaultStatus(VoiceCallPhase phase)
    {
        return phase switch
        {
            VoiceCallPhase.Connecting => "正在连接",
            VoiceCallPhase.Listening => "正在聆听",
            VoiceCallPhase.Thinking => "正在思考",
            VoiceCallPhase.Speaking => "正在回应",
            VoiceCallPhase.Interrupted => "已停止播报",
            VoiceCallPhase.Reconnecting => "正在重连",
            VoiceCallPhase.Ended => "通话已结束",
            VoiceCallPhase.Error => "通话暂时不可用",
            _ => "准备通话",
        };
    }

    private static string ReadString(JsonElement payload, string name)
    {
        return payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";
    }

    private static long ReadInt64(JsonElement payload, string name)
    {
        return payload.TryGetProperty(name, out var value) && value.TryGetInt64(out var number) ? number : 0;
    }
}
