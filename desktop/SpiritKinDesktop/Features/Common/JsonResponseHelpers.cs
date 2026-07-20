using System;
using System.Globalization;
using System.Text.Json;

namespace SpiritKinDesktop;

internal static class JsonResponseHelpers
{
    internal static void EnsureOkResponse(JsonElement root, string actionLabel)
    {
        if (!root.TryGetProperty("ok", out var okElement) || okElement.ValueKind != JsonValueKind.False)
        {
            return;
        }
        var error = ReadJsonString(root, "error", actionLabel);
        var detail = ReadJsonString(root, "detail");
        throw new InvalidOperationException(string.IsNullOrWhiteSpace(detail) ? error : $"{error}: {detail}");
    }

    internal static string ReadJsonString(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return "";
        }
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? "",
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "",
            _ => value.GetRawText(),
        };
    }

    internal static string ReadJsonString(JsonElement element, string key, string fallback)
    {
        var value = ReadJsonString(element, key);
        return string.IsNullOrWhiteSpace(value) ? fallback : value;
    }

    internal static int ReadJsonInt(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
        {
            return number;
        }
        return int.TryParse(ReadJsonString(element, key), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed) ? parsed : 0;
    }

    internal static bool ReadJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return fallback;
        }
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => bool.TryParse(value.GetString(), out var parsed) ? parsed : fallback,
            _ => fallback,
        };
    }

    internal static string FormatTimeFromDouble(string raw) =>
        double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds)
            ? DateTimeOffset.FromUnixTimeSeconds((long)seconds).LocalDateTime.ToString("g")
            : "--";
}
