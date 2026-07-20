using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed partial class MobileManagementController
{
    internal static string HealthText(JsonElement health)
    {
        if (health.ValueKind != JsonValueKind.Object)
        {
            return "--";
        }
        var ok = ReadJsonBool(health, "ok");
        var status = ReadJsonString(health, "status", "0");
        var error = ReadJsonString(health, "error");
        return string.IsNullOrWhiteSpace(error) ? $"{(ok ? "online" : "offline")} · HTTP {status}" : $"{(ok ? "online" : "offline")} · {error}";
    }
    internal static string BuildIosShortcutText(JsonElement ios)
    {
        if (ios.ValueKind != JsonValueKind.Object || !ios.TryGetProperty("shortcuts", out var shortcuts) || shortcuts.ValueKind != JsonValueKind.Array)
        {
            return "--";
        }
        var lines = shortcuts.EnumerateArray()
            .Select(item =>
            {
                var name = ReadJsonString(item, "name", "--");
                var description = ReadJsonString(item, "description");
                var url = ReadJsonString(item, "url");
                return $"{name}: {description}{Environment.NewLine}{url}".Trim();
            })
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .ToArray();
        return lines.Length == 0 ? "--" : string.Join($"{Environment.NewLine}{Environment.NewLine}", lines);
    }
    internal static string BuildAndroidWorkerText(JsonElement worker)
    {
        if (worker.ValueKind != JsonValueKind.Object)
        {
            return "";
        }
        var queue = TryReadJsonObject(worker, "queue", out var queueElement) ? queueElement : default;
        var permissions = TryReadJsonObject(worker, "permissions", out var permissionsElement) ? permissionsElement : default;
        var lifecycle = TryReadJsonObject(worker, "lifecycle", out var lifecycleElement) ? lifecycleElement : default;
        var update = TryReadJsonObject(worker, "update", out var updateElement) ? updateElement : default;
        var promotionGate = TryReadJsonObject(worker, "promotion_gate", out var promotionGateElement) ? promotionGateElement : default;
        var capabilities = new List<string>();
        if (worker.TryGetProperty("capabilities", out var capabilityArray) && capabilityArray.ValueKind == JsonValueKind.Array)
        {
            capabilities.AddRange(capabilityArray.EnumerateArray().Take(12).Select(item => JsonElementText(item)).Where(item => !string.IsNullOrWhiteSpace(item)));
        }
        var lines = new List<string>
        {
            $"Android Control Worker: {UiDisplayText.Status(ReadJsonString(worker, "status", "--"))} · {ReadJsonString(worker, "role", "controlled_execution_worker")}",
            $"Devices: {ReadJsonString(worker, "online_device_count", "0")}/{ReadJsonString(worker, "device_count", "0")} online · capabilities {ReadJsonString(worker, "capability_count", "0")}",
            $"Queue: pending {ReadSafeJsonString(queue, "pending", ReadJsonString(worker, "pending_command_count", "0"))} · running {ReadSafeJsonString(queue, "inflight", ReadJsonString(worker, "inflight_command_count", "0"))} · {WorkerQueueStatusSummary(queue, worker)}",
            $"Permissions: gaps {ReadSafeJsonString(permissions, "gap_count", ReadJsonString(worker, "permission_gap_count", "0"))} · receive {(ReadSafeJsonBool(lifecycle, "can_receive_commands", false) ? "yes" : "no")} · automation {(ReadSafeJsonBool(lifecycle, "can_run_automation", false) ? "yes" : "no")} · screenshot {(ReadSafeJsonBool(lifecycle, "can_capture_screen", false) ? "yes" : "no")}",
            $"Update: APK {(ReadSafeJsonBool(update, "apk_exists", false) ? "built" : "missing")} · installed {(ReadSafeJsonBool(update, "installed", false) ? "yes" : "no")} · current {ReadSafeJsonString(update, "installed_version_name", "--")} · release {ReadSafeJsonString(update, "release_version_name", "--")}",
            BuildAndroidApkPromotionText(promotionGate),
        };
        if (capabilities.Count > 0)
        {
            lines.Add($"Capabilities: {string.Join(", ", capabilities)}");
        }
        return string.Join(Environment.NewLine, lines);
    }

    internal static string BuildAndroidApkPromotionText(JsonElement promotionGate)
    {
        if (promotionGate.ValueKind != JsonValueKind.Object)
        {
            return "Promotion: --";
        }
        var required = new List<string>();
        if (promotionGate.TryGetProperty("required_actions", out var actions) && actions.ValueKind == JsonValueKind.Array)
        {
            required.AddRange(actions.EnumerateArray().Select(JsonElementText).Where(item => !string.IsNullOrWhiteSpace(item)));
        }
        return $"Promotion: {ReadJsonString(promotionGate, "status", "--")} · serving {(ReadJsonBool(promotionGate, "serving_allowed", false) ? "allowed" : "blocked")} · required {(required.Count == 0 ? "--" : string.Join(", ", required))}";
    }

    internal static string BuildAndroidCompanionText(JsonElement companion)
    {
        if (companion.ValueKind != JsonValueKind.Object)
        {
            return "--";
        }
        var lines = new List<string>
        {
            "主控终端：本地桌面端 + iOS；Android 是被控执行端。",
            $"Devices: {ReadJsonString(companion, "device_count", "0")} · Pending commands: {ReadJsonString(companion, "pending_command_count", "0")} · {CommandStatusSummary(companion)}",
        };
        lines.AddRange(BuildAndroidPermissionPolicyLines(companion));
        if (companion.TryGetProperty("devices", out var devices) && devices.ValueKind == JsonValueKind.Array)
        {
            foreach (var device in devices.EnumerateArray().Take(6))
            {
                var id = ReadJsonString(device, "device_id", "--");
                var online = ReadJsonBool(device, "online") ? "online" : "offline";
                var pending = ReadJsonString(device, "pending_command_count", "0");
                var battery = ReadJsonString(device, "battery_pct", "--");
                var app = ReadJsonString(device, "current_app", "--");
                var inflight = ReadJsonString(device, "inflight_command_count", "0");
                var lastCommand = "";
                if (device.TryGetProperty("last_command", out var last) && last.ValueKind == JsonValueKind.Object)
                {
                    lastCommand = $" · last {ReadJsonString(last, "operation", "--")}:{ReadJsonString(last, "status", "--")}";
                }
                lines.Add($"{id} · {online} · pending {pending} · running {inflight} · battery {battery} · app {app}{lastCommand}");
                lines.AddRange(BuildAndroidPermissionPostureLines(device));
            }
        }
        if (companion.TryGetProperty("recent_commands", out var recentCommands) && recentCommands.ValueKind == JsonValueKind.Array)
        {
            foreach (var command in recentCommands.EnumerateArray().Reverse().Take(6).Reverse())
            {
                var deviceId = ReadJsonString(command, "device_id", "--");
                var operation = ReadJsonString(command, "operation", "--");
                var status = ReadJsonString(command, "status", "--");
                var message = ReadJsonString(command, "message");
                var permission = TryReadJsonObject(command, "permission", out var permissionElement) ? permissionElement : default;
                var tier = ReadSafeJsonString(permission, "label", ReadSafeJsonString(permission, "tier", "--"));
                lines.Add($"recent {deviceId}: {operation} · {status} · {tier}{(string.IsNullOrWhiteSpace(message) ? "" : $" · {message}")}");
            }
        }
        if (companion.TryGetProperty("commands", out var commands) && commands.ValueKind == JsonValueKind.Object)
        {
            foreach (var deviceQueue in commands.EnumerateObject().Take(4))
            {
                if (deviceQueue.Value.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }
                foreach (var command in deviceQueue.Value.EnumerateArray().Take(4))
                {
                    var permission = TryReadJsonObject(command, "permission", out var permissionElement) ? permissionElement : default;
                    var tier = ReadSafeJsonString(permission, "label", ReadSafeJsonString(permission, "tier", "--"));
                    lines.Add($"queued {deviceQueue.Name}: {ReadJsonString(command, "operation", "--")} · {tier}");
                }
            }
        }
        return string.Join(Environment.NewLine, lines);
    }

    internal static string BuildMobileArtifactText(JsonElement artifacts)
    {
        if (artifacts.ValueKind != JsonValueKind.Object)
        {
            return "--";
        }
        var lines = new List<string>
        {
            $"素材组：{ReadJsonString(artifacts, "artifact_count", "0")} · 图片：{ReadJsonString(artifacts, "image_count", "0")} · 已过期：{ReadJsonString(artifacts, "expired_count", "0")}",
            $"大小：{ReadJsonString(artifacts, "total_size_bytes", "0")} bytes",
        };
        if (artifacts.TryGetProperty("recent", out var recent) && recent.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in recent.EnumerateArray().Take(6))
            {
                lines.Add($"{ReadJsonString(item, "artifact_id", "--")} · {ReadJsonString(item, "purpose", "--")} · {ReadJsonString(item, "name", "--")} · {ReadJsonString(item, "source", "--")}");
            }
        }
        return string.Join(Environment.NewLine, lines);
    }
    internal static string CommandStatusSummary(JsonElement companion)
    {
        if (!companion.TryGetProperty("command_status_counts", out var counts) || counts.ValueKind != JsonValueKind.Object)
        {
            return "commands --";
        }
        var parts = counts.EnumerateObject()
            .Select(item => $"{item.Name} {JsonElementText(item.Value)}")
            .ToArray();
        return parts.Length == 0 ? "commands --" : string.Join(" · ", parts);
    }
    internal static string WorkerQueueStatusSummary(JsonElement queue, JsonElement worker)
    {
        JsonElement counts = default;
        if (queue.ValueKind == JsonValueKind.Object && queue.TryGetProperty("status_counts", out var queueCounts) && queueCounts.ValueKind == JsonValueKind.Object)
        {
            counts = queueCounts;
        }
        else if (worker.ValueKind == JsonValueKind.Object && worker.TryGetProperty("command_status_counts", out var workerCounts) && workerCounts.ValueKind == JsonValueKind.Object)
        {
            counts = workerCounts;
        }
        if (counts.ValueKind != JsonValueKind.Object)
        {
            return "commands --";
        }
        var parts = counts.EnumerateObject()
            .Select(item => $"{item.Name} {JsonElementText(item.Value)}")
            .ToArray();
        return parts.Length == 0 ? "commands --" : string.Join(" · ", parts);
    }
    internal static string JsonElementText(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.String => element.GetString() ?? "",
            JsonValueKind.Number => element.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "",
            _ => element.GetRawText(),
        };
    }
}
