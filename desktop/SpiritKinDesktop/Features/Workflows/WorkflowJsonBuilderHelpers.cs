using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpiritKinDesktop;

internal sealed partial class WorkflowController
{
    internal bool TryParseWorkflowNodeArguments(out string normalizedJson, out string error)
    {
        return TryParseJsonObject(WorkbenchShell.ManagementPanels.WorkflowNodeArgumentsBox.Text, out _, out error, out normalizedJson);
    }

    internal static bool TryParseJsonObject(string text, out Dictionary<string, object?> value, out string error)
    {
        return TryParseJsonObject(text, out value, out error, out _);
    }

    internal static bool JsonObjectHasValue(Dictionary<string, object?> value, string key)
    {
        if (!value.TryGetValue(key, out var item) || item is null)
        {
            return false;
        }
        if (item is string text)
        {
            return !string.IsNullOrWhiteSpace(text);
        }
        if (item is ICollection<object?> collection)
        {
            return collection.Count > 0;
        }
        if (item is IDictionary<string, object?> dictionary)
        {
            return dictionary.Count > 0;
        }
        return true;
    }

    internal static bool TryParseJsonObject(string text, out Dictionary<string, object?> value, out string error, out string normalizedJson)
    {
        value = new Dictionary<string, object?>();
        error = "";
        normalizedJson = "{}";
        var raw = string.IsNullOrWhiteSpace(text) ? "{}" : text.Trim();
        try
        {
            using var doc = JsonDocument.Parse(raw);
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
            {
                error = "必须是 JSON object。";
                return false;
            }
            value = doc.RootElement.EnumerateObject().ToDictionary(prop => prop.Name, prop => JsonElementToObject(prop.Value), StringComparer.OrdinalIgnoreCase);
            normalizedJson = JsonSerializer.Serialize(value, new JsonSerializerOptions { WriteIndented = true });
            return true;
        }
        catch (JsonException ex)
        {
            error = ex.Message;
            return false;
        }
    }

    internal static object? JsonElementToObject(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.Object => element.EnumerateObject().ToDictionary(prop => prop.Name, prop => JsonElementToObject(prop.Value), StringComparer.OrdinalIgnoreCase),
            JsonValueKind.Array => element.EnumerateArray().Select(JsonElementToObject).ToArray(),
            JsonValueKind.String => element.GetString(),
            JsonValueKind.Number => element.TryGetInt64(out var integer) ? integer : element.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Null => null,
            _ => null,
        };
    }

    internal static string[] SplitLooseList(string text)
    {
        return text.Split(new[] { "\r\n", "\n", "\r", "," }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

}
