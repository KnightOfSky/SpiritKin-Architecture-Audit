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
    internal static object[] MergeWorkflowParameterDefinitions(object[] existing, IEnumerable<string> inferredNames)
    {
        var parameters = new List<object>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var parameter in existing)
        {
            if (TryReadWorkflowParameterName(parameter, out var name))
            {
                names.Add(name);
            }
            parameters.Add(parameter);
        }
        foreach (var name in inferredNames.Where(IsRuntimeWorkflowParameterName).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (names.Contains(name))
            {
                continue;
            }
            names.Add(name);
            parameters.Add(new Dictionary<string, object?>
            {
                ["name"] = name,
                ["label"] = WorkflowDisplayText.HumanizeIdentifier(name, name),
                ["type"] = InferWorkflowParameterType(name),
                ["placeholder"] = $"{{{{{name}}}}}",
            });
        }
        return parameters.ToArray();
    }

    internal static bool TryReadWorkflowParameterName(object? parameter, out string name)
    {
        name = "";
        if (parameter is IDictionary<string, object?> dictionary && dictionary.TryGetValue("name", out var rawName))
        {
            name = Convert.ToString(rawName) ?? "";
        }
        else if (parameter is JsonElement element && element.ValueKind == JsonValueKind.Object)
        {
            name = ReadJsonString(element, "name");
        }
        name = name.Trim();
        return !string.IsNullOrWhiteSpace(name);
    }

    internal static void CollectWorkflowRuntimeParameterNames(object? value, List<string> names)
    {
        switch (value)
        {
            case string text:
                if (TryExtractWorkflowRuntimeParameterName(text, out var name))
                {
                    names.Add(name);
                }
                break;
            case IDictionary<string, object?> dictionary:
                foreach (var item in dictionary.Values)
                {
                    CollectWorkflowRuntimeParameterNames(item, names);
                }
                break;
            case IEnumerable<object?> items:
                foreach (var item in items)
                {
                    CollectWorkflowRuntimeParameterNames(item, names);
                }
                break;
        }
    }

    internal static bool TryExtractWorkflowRuntimeParameterName(string text, out string name)
    {
        name = "";
        var match = Regex.Match(text.Trim(), @"^\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}$");
        if (!match.Success)
        {
            return false;
        }
        name = match.Groups[1].Value.Trim();
        return IsRuntimeWorkflowParameterName(name);
    }

    internal static bool IsRuntimeWorkflowParameterName(string name)
    {
        return !string.IsNullOrWhiteSpace(name)
            && !string.Equals(name, "project_root", StringComparison.OrdinalIgnoreCase);
    }

    internal static string InferWorkflowParameterType(string name)
    {
        var normalized = name.Trim().ToLowerInvariant();
        if (normalized.StartsWith("include_", StringComparison.Ordinal)
            || normalized.StartsWith("enable_", StringComparison.Ordinal)
            || normalized.StartsWith("use_", StringComparison.Ordinal)
            || normalized is "dry_run" or "received" or "completed")
        {
            return "boolean";
        }
        if (normalized.Contains("count", StringComparison.Ordinal)
            || normalized.Contains("limit", StringComparison.Ordinal)
            || normalized.Contains("ttl", StringComparison.Ordinal)
            || normalized.Contains("timeout", StringComparison.Ordinal)
            || normalized.EndsWith("_seconds", StringComparison.Ordinal)
            || normalized.EndsWith("_minutes", StringComparison.Ordinal))
        {
            return "number";
        }
        if (normalized.Contains("json", StringComparison.Ordinal)
            || normalized.Contains("payload", StringComparison.Ordinal)
            || normalized.Contains("prompt", StringComparison.Ordinal)
            || normalized.Contains("policy", StringComparison.Ordinal)
            || normalized.Contains("pool", StringComparison.Ordinal)
            || normalized.Contains("images", StringComparison.Ordinal)
            || normalized.Contains("notes", StringComparison.Ordinal))
        {
            return "textarea";
        }
        return "text";
    }

    internal object[] ReadWorkflowParameterDefinitionsFromActiveDefinition()
    {
        if (_activeWorkflowDefinition.ValueKind == JsonValueKind.Object
            && TryReadJsonObject(_activeWorkflowDefinition, "metadata", out var metadata)
            && metadata.TryGetProperty("parameters", out var parameters)
            && parameters.ValueKind == JsonValueKind.Array)
        {
            return parameters.EnumerateArray()
                .Select(JsonElementToObject)
                .Where(item => item is not null)
                .Cast<object>()
                .ToArray();
        }
        return Array.Empty<object>();
    }

}
