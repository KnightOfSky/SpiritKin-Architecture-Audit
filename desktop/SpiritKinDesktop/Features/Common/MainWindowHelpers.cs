using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

namespace SpiritKinDesktop;

internal static partial class DesktopRuntimeHelpers
{
    internal static long NowSeconds() => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    internal static string NewId(string prefix) => $"{prefix}_{Guid.NewGuid():N}"[..Math.Min(prefix.Length + 17, prefix.Length + 33)];

    internal static string SafeFileName(string value)
    {
        var invalid = Path.GetInvalidFileNameChars().ToHashSet();
        var safe = new string((value.Length == 0 ? "assistant" : value).Select(ch => invalid.Contains(ch) ? '_' : ch).ToArray());
        return string.IsNullOrWhiteSpace(safe) ? "assistant" : safe;
    }

    internal static string SafeRemoteExportId(string value)
    {
        var safe = new string(value.Where(ch => char.IsLetterOrDigit(ch) || ch is '-' or '_').ToArray());
        return string.IsNullOrWhiteSpace(safe) ? $"remote-export-{DateTimeOffset.UtcNow.ToUnixTimeSeconds()}" : safe;
    }

    internal static string? SelectExistingId(string? preferred, IEnumerable<string> candidates)
    {
        if (string.IsNullOrWhiteSpace(preferred))
        {
            return null;
        }
        return candidates.FirstOrDefault(candidate => string.Equals(candidate, preferred, StringComparison.OrdinalIgnoreCase));
    }

    internal static string UniqueId(string prefix, IEnumerable<string> existingIds)
    {
        var existing = existingIds.Where(id => !string.IsNullOrWhiteSpace(id)).ToHashSet(StringComparer.OrdinalIgnoreCase);
        for (var index = 1; index < 1000; index++)
        {
            var candidate = $"{prefix}_{index:00}";
            if (!existing.Contains(candidate))
            {
                return candidate;
            }
        }
        return NewId(prefix);
    }

    internal static string[] LinesFromText(string value) =>
        value.Replace("；", "\n")
            .Replace(";", "\n")
            .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(line => line.Trim(' ', '\t', '-', '*'))
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Take(40)
            .ToArray();

    internal static string FormatTime(double seconds) => seconds <= 0 ? "--" : DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g");
    internal static string FormatTimeFromDouble(string raw) => double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds) ? FormatTime(seconds) : "--";
}
