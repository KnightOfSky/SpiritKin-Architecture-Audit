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
    private void TrackDeletedMessages(string sessionId, IEnumerable<string> messageIds)
    {
        var ids = messageIds.Where(id => !string.IsNullOrWhiteSpace(id)).ToList();
        if (string.IsNullOrWhiteSpace(sessionId) || ids.Count == 0)
        {
            return;
        }
        if (!_pendingDeletedMessageIds.TryGetValue(sessionId, out var pending))
        {
            pending = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            _pendingDeletedMessageIds[sessionId] = pending;
        }
        foreach (var id in ids)
        {
            pending.Add(id);
        }
    }

    private static bool IsGeneratedTurnMessage(DesktopMessage message)
    {
        if (message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        return message.Role.Equals("system", StringComparison.OrdinalIgnoreCase)
            && message.Kind is "work" or "changes" or "command";
    }

    private DesktopMessage? FindDuplicateRecentAssistantMessage(DesktopSession session, string text, double now)
    {
        var normalized = NormalizeDuplicateText(text);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return null;
        }
        var lastUser = session.Messages.LastOrDefault(message => message.Role.Equals("user", StringComparison.OrdinalIgnoreCase));
        var turnKey = lastUser?.Id ?? "no_user";
        var key = $"{session.Id}:{turnKey}:{normalized}";
        foreach (var stale in _recentAssistantEventKeys.Where(entry => now - entry.Value > 12).Select(entry => entry.Key).ToList())
        {
            _recentAssistantEventKeys.Remove(stale);
        }
        var last = session.Messages.LastOrDefault(message => message.Role.Equals("assistant", StringComparison.OrdinalIgnoreCase));
        if (_recentAssistantEventKeys.TryGetValue(key, out var lastAt) && now - lastAt < 10)
        {
            return last is not null && NormalizeDuplicateText(last.Text) == normalized ? last : null;
        }
        _recentAssistantEventKeys[key] = now;
        return last is not null
            && (lastUser is null || last.CreatedAt >= lastUser.CreatedAt)
            && NormalizeDuplicateText(last.Text) == normalized
            && Math.Abs(now - last.CreatedAt) < 10
                ? last
                : null;
    }

    private static string NormalizeDuplicateText(string text) =>
        string.Join(" ", (text ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));

    private static string CleanAvatarTags(string text) =>
        Regex.Replace(text ?? "", @"<(?:emotion|action):[^>]+>", "", RegexOptions.IgnoreCase).Trim();

    private static string CleanSpeechText(string text)
    {
        var value = CleanAvatarTags(text);
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }

        value = Regex.Replace(value, @"!\[[^\]]*\]\([^)]+\)", " ");
        value = Regex.Replace(value, @"<img\b[^>]*>", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"[（(【\[]\s*(?:笑|微笑|开心|哭|流泪|害羞|尴尬|捂脸|表情|emoji|sticker)[^）)】\]]{0,16}[）)】\]]", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"\[[^\]]*(?:表情包|表情|图片|image|sticker)[^\]]*\]", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @":[a-z0-9_+\-]+:", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"[\uD83C-\uDBFF][\uDC00-\uDFFF]", " ");
        value = Regex.Replace(value, @"[\u2600-\u27BF\uFE0F\u200D]", " ");
        value = Regex.Replace(value, @"(?:[;:=8xX][\-oO']?[\)\(DPp/\\]|[\)\(][\-oO']?[;:=8xX])", " ");
        value = Regex.Replace(value, @"https?://\S+", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"file:///??\S+", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"\S+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?\S*)?", " ", RegexOptions.IgnoreCase);
        value = Regex.Replace(value, @"\s+", " ").Trim();
        return Regex.IsMatch(value, @"[0-9A-Za-z\u4e00-\u9fff]") ? value : "";
    }

    private static string SummarizeDesktopSpeechText(string text)
    {
        var value = CleanSpeechText(text);
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }

        const int maxLength = 420;
        if (value.Length <= maxLength)
        {
            return value;
        }

        var clipped = value[..maxLength];
        var lastStop = clipped.LastIndexOfAny(new[] { '。', '！', '？', '!', '?', '\n' });
        if (lastStop >= 80)
        {
            return clipped[..(lastStop + 1)].Trim();
        }
        return clipped.TrimEnd() + "。";
    }

    private static bool ShouldAutoTitleSession(DesktopSession session)
    {
        if (session.Messages.Any(message => message.Role.Equals("user", StringComparison.OrdinalIgnoreCase)))
        {
            return false;
        }
        var title = session.Title.Trim();
        return title.Length == 0
            || title.Equals("新会话", StringComparison.OrdinalIgnoreCase)
            || title.Equals("新项目会话", StringComparison.OrdinalIgnoreCase)
            || title.StartsWith("会话 ", StringComparison.OrdinalIgnoreCase);
    }

    internal static string SummarizeSessionTitle(string input)
    {
        var text = string.Join(" ", input.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
        if (string.IsNullOrWhiteSpace(text))
        {
            return "新会话";
        }
        var stopChars = new[] { '。', '！', '？', '.', '!', '?', '\n', '\r' };
        var firstStop = text.IndexOfAny(stopChars);
        if (firstStop > 3)
        {
            text = text[..firstStop];
        }
        const int maxLength = 24;
        return text.Length <= maxLength ? text : $"{text[..maxLength]}...";
    }

}
