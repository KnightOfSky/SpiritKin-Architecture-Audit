using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    private void ApplyReplyPendingFallback(CommandEnvelope? envelope, bool confirmationControl)
    {
        if (envelope?.Reply is not { } reply)
        {
            return;
        }
        var responseKind = reply.ResponseKind ?? "";
        if (reply.RequiresConfirmation || string.Equals(responseKind, "confirmation_request", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        if (confirmationControl || ShouldClearPendingForReply(responseKind, reply.Text))
        {
            _state.Pending = null;
            RenderState();
        }
    }

    private void MarkPendingConfirmationConsumed(PendingConfirmationInfo? pendingInfo, string requestId)
    {
        _confirmationChoiceInFlight = true;
        _consumedPendingConfirmation = pendingInfo;
        _consumedPendingConfirmationAt = NowSeconds();
        _consumedPendingConfirmationRequestId = requestId;
    }

    private void ClearConsumedPendingConfirmation()
    {
        _consumedPendingConfirmation = null;
        _consumedPendingConfirmationAt = 0;
        _consumedPendingConfirmationRequestId = "";
        _confirmationChoiceInFlight = false;
    }

    private bool ShouldSuppressConsumedPending(PendingConfirmationInfo? pendingInfo)
    {
        if (pendingInfo is null)
        {
            return false;
        }
        if (_consumedPendingConfirmationAt <= 0)
        {
            return false;
        }
        var suppressionSeconds = _confirmationChoiceInFlight
            ? ConfirmationChoiceInFlightSuppressionSeconds
            : ConfirmationChoiceCompletedSuppressionSeconds;
        if (NowSeconds() - _consumedPendingConfirmationAt > suppressionSeconds)
        {
            ClearConsumedPendingConfirmation();
            return false;
        }
        if (_confirmationChoiceInFlight && _consumedPendingConfirmation is null)
        {
            return true;
        }
        if (_consumedPendingConfirmation is null)
        {
            return false;
        }
        return string.Equals(pendingInfo.Target, _consumedPendingConfirmation.Target, StringComparison.OrdinalIgnoreCase)
            && string.Equals(pendingInfo.Operation, _consumedPendingConfirmation.Operation, StringComparison.OrdinalIgnoreCase);
    }

    private static PendingConfirmationInfo? PendingInfoFromAssistantMessage(JsonElement payload)
    {
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return null;
        }
        var target = TryReadNestedString(payload, "data", "pending_target") ?? "";
        var operation = TryReadNestedString(payload, "data", "pending_operation") ?? "";
        if (string.IsNullOrWhiteSpace(target) || string.IsNullOrWhiteSpace(operation))
        {
            return null;
        }
        var risk = TryReadNestedString(payload, "data", "risk_level") ?? "medium";
        return new PendingConfirmationInfo(target, operation, string.IsNullOrWhiteSpace(risk) ? "medium" : risk);
    }

    private string NewCommandRequestId()
    {
        var sequence = Interlocked.Increment(ref _commandSendSequence);
        return $"desktop-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{sequence}";
    }

    private void TrackCommandRequestSession(string requestId, string sessionId)
    {
        if (string.IsNullOrWhiteSpace(requestId) || string.IsNullOrWhiteSpace(sessionId))
        {
            return;
        }
        // 只需覆盖"发送→回复到达"窗口期，留最近 64 条足够；防长跑会话字典无界增长。
        if (_commandRequestSessionIds.Count >= 64)
        {
            var stale = _commandRequestSessionIds.Keys.Take(_commandRequestSessionIds.Count - 48).ToList();
            foreach (var key in stale)
            {
                _commandRequestSessionIds.Remove(key);
            }
        }
        _commandRequestSessionIds[requestId] = sessionId;
    }

    private DesktopSession? SessionForCommandRequest(string requestId)
    {
        if (string.IsNullOrWhiteSpace(requestId) || !_commandRequestSessionIds.TryGetValue(requestId, out var sessionId))
        {
            return null;
        }
        return _state.Sessions.FirstOrDefault(session => string.Equals(session.Id, sessionId, StringComparison.OrdinalIgnoreCase));
    }

    private CancellationTokenSource ReplaceActiveCommandRequest(string requestId)
    {
        var previous = _commandSendCts;
        _latestCommandRequestId = requestId;
        var current = new CancellationTokenSource();
        _commandSendCts = current;
        try
        {
            previous?.Cancel();
        }
        catch
        {
            // best effort cancellation only
        }
        finally
        {
            previous?.Dispose();
        }
        StopDesktopTtsPlayback();
        return current;
    }

    private bool IsStaleCommandRequest(string requestId)
    {
        return !string.IsNullOrWhiteSpace(_latestCommandRequestId)
            && !string.Equals(requestId, _latestCommandRequestId, StringComparison.OrdinalIgnoreCase);
    }

    private bool IsStaleCommandEnvelope(CommandEnvelope? envelope, string requestId)
    {
        if (envelope?.Events is not null)
        {
            return envelope.Events.Any(ev => IsStaleEvent(ev, requestId));
        }
        return false;
    }

    private bool IsStaleEvent(RuntimeEvent ev, string? expectedRequestId = null)
    {
        var requestId = TryReadEventRequestId(ev);
        if (string.IsNullOrWhiteSpace(requestId))
        {
            return false;
        }
        var expected = string.IsNullOrWhiteSpace(expectedRequestId) ? _latestCommandRequestId : expectedRequestId;
        return !string.IsNullOrWhiteSpace(expected)
            && !string.Equals(requestId, expected, StringComparison.OrdinalIgnoreCase);
    }

    private static string TryReadEventRequestId(RuntimeEvent ev)
    {
        var payload = ev.Payload;
        if (payload.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        var rootRequestId = ReadJsonString(payload, "request_id");
        if (!string.IsNullOrWhiteSpace(rootRequestId))
        {
            return rootRequestId;
        }
        if (!payload.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        return TryReadReplyRequestId(data);
    }

    private static string TryReadReplyRequestId(JsonElement data)
    {
        var requestId = ReadJsonString(data, "request_id");
        if (!string.IsNullOrWhiteSpace(requestId))
        {
            return requestId;
        }
        if (data.TryGetProperty("client_metadata", out var clientMetadata) && clientMetadata.ValueKind == JsonValueKind.Object)
        {
            return ReadJsonString(clientMetadata, "request_id");
        }
        return "";
    }

}
