using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Effects;
using Microsoft.Win32;

namespace SpiritKinDesktop;

internal static partial class DesktopRuntimeHelpers
{
    internal static string ReadDict(Dictionary<string, object?> dict, string key) => dict.TryGetValue(key, out var value) ? Convert.ToString(value) ?? "--" : "--";

    internal static string ReadDictFirst(Dictionary<string, object?> dict, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (dict.TryGetValue(key, out var value))
            {
                var text = Convert.ToString(value)?.Trim();
                if (!string.IsNullOrWhiteSpace(text) && text != "--")
                {
                    return text;
                }
            }
        }
        return "";
    }

    internal static PendingConfirmationInfo? PendingInfo(Dictionary<string, object?>? pending)
    {
        if (pending is null)
        {
            return null;
        }
        var createdAtText = ReadDictFirst(pending, "created_at", "createdAt", "time");
        if (double.TryParse(createdAtText, out var createdAt) && createdAt > 0 && NowSeconds() - createdAt > 300)
        {
            return null;
        }
        var target = ReadDictFirst(pending, "target", "pending_target");
        var operation = ReadDictFirst(pending, "operation", "pending_operation");
        if (string.IsNullOrWhiteSpace(target) || string.IsNullOrWhiteSpace(operation))
        {
            return null;
        }
        var risk = ReadDictFirst(pending, "risk_level", "riskLevel");
        return new PendingConfirmationInfo(target, operation, string.IsNullOrWhiteSpace(risk) ? "medium" : risk);
    }
    internal static string DictText(Dictionary<string, object?>? dict) => dict is null || dict.Count == 0 ? "--" : string.Join(Environment.NewLine, dict.Select(kv => $"{kv.Key}: {kv.Value}"));

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
        return int.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    internal static double ReadJsonDouble(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
        {
            return number;
        }
        return double.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
    }

    internal static long ReadJsonLong(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number))
        {
            return number;
        }
        return long.TryParse(ReadJsonString(element, key), out var parsed) ? parsed : 0;
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

    internal static bool TryReadJsonObject(JsonElement element, string key, out JsonElement value)
    {
        value = default;
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var candidate) || candidate.ValueKind != JsonValueKind.Object)
        {
            return false;
        }
        value = candidate;
        return true;
    }

    internal static bool TryReadJsonArray(JsonElement element, string key)
    {
        return element.ValueKind == JsonValueKind.Object
            && element.TryGetProperty(key, out var candidate)
            && candidate.ValueKind == JsonValueKind.Array;
    }

    internal static string ReadSafeJsonString(JsonElement element, string key, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonString(element, key, fallback);
    }

    internal static int ReadSafeJsonInt(JsonElement element, string key, int fallback = 0)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out _))
        {
            return fallback;
        }
        return ReadJsonInt(element, key);
    }

    internal static bool ReadSafeJsonBool(JsonElement element, string key, bool fallback = false)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }
        return ReadJsonBool(element, key, fallback);
    }

    internal static string[] ReadSummaryStepTitles(JsonElement element, string key, int limit)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out var steps) || steps.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return steps
            .EnumerateArray()
            .Take(Math.Max(0, limit))
            .Select(step =>
            {
                if (step.ValueKind == JsonValueKind.String)
                {
                    return step.GetString() ?? "";
                }
                if (step.ValueKind != JsonValueKind.Object)
                {
                    return "";
                }
                var title = ReadJsonString(step, "title");
                return string.IsNullOrWhiteSpace(title) ? ReadJsonString(step, "id") : title;
            })
            .Where(text => !string.IsNullOrWhiteSpace(text))
            .ToArray();
    }

    internal static string[] ReadJsonStringArray(JsonElement element, string key)
    {
        if (!element.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        return value.EnumerateArray().Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() ?? "" : item.GetRawText()).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
    }

    internal static string FormatJson(JsonElement element)
    {
        try
        {
            return JsonSerializer.Serialize(element, new JsonSerializerOptions { WriteIndented = true });
        }
        catch
        {
            return element.GetRawText();
        }
    }

    internal static Dictionary<string, object?> JsonElementToDictionary(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object)
        {
            return new Dictionary<string, object?>();
        }
        var dict = new Dictionary<string, object?>();
        foreach (var property in element.EnumerateObject())
        {
            dict[property.Name] = property.Value.ValueKind switch
            {
                JsonValueKind.String => property.Value.GetString(),
                JsonValueKind.Number => property.Value.TryGetInt64(out var number) ? number : property.Value.GetDouble(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Null => null,
                _ => property.Value.GetRawText(),
            };
        }
        return dict;
    }

    internal static string? TryReadNestedString(JsonElement element, string parent, string child)
    {
        if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(parent, out var p) && p.ValueKind == JsonValueKind.Object && p.TryGetProperty(child, out var c))
        {
            return c.ValueKind == JsonValueKind.String ? c.GetString() : c.GetRawText();
        }
        return null;
    }

}
