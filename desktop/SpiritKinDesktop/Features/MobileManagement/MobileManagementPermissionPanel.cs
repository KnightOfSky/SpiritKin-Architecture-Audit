using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal static IEnumerable<string> BuildAndroidPermissionPolicyLines(JsonElement companion)
    {
        if (!TryReadJsonObject(companion, "permission_policy", out var policy)
            || !policy.TryGetProperty("tiers", out var tiers)
            || tiers.ValueKind != JsonValueKind.Array)
        {
            yield break;
        }
        var parts = tiers.EnumerateArray()
            .Select(item =>
            {
                var label = ReadJsonString(item, "label", ReadJsonString(item, "tier", "--"));
                var allowed = ReadJsonBool(item, "allowed", false) ? "允许" : "阻止";
                var confirmation = ReadJsonBool(item, "requires_confirmation", false) ? "/需确认" : "";
                return $"{label}:{allowed}{confirmation}";
            })
            .ToArray();
        if (parts.Length > 0)
        {
            yield return $"权限分级：{string.Join(" · ", parts)}";
        }
    }

    internal static IEnumerable<string> BuildAndroidPermissionPostureLines(JsonElement device)
    {
        if (!TryReadJsonObject(device, "permission_posture", out var posture))
        {
            yield break;
        }
        var status = ReadJsonString(posture, "status", "unknown");
        var available = ReadJsonString(posture, "available_operation_count", "0");
        var total = ReadJsonString(posture, "operation_count", "0");
        var gapCount = ReadJsonString(posture, "gap_count", "0");
        yield return $"权限姿态：{status} · ops {available}/{total} · gaps {gapCount}";
        if (posture.TryGetProperty("gaps", out var gaps) && gaps.ValueKind == JsonValueKind.Array)
        {
            foreach (var gap in gaps.EnumerateArray().Take(3))
            {
                yield return $"  gap {ReadJsonString(gap, "id", "--")} · {ReadJsonString(gap, "message", "--")}";
            }
        }
    }
}

