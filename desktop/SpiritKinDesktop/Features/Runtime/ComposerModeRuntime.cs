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
    private void ApplyComposerModeMetadata(JsonElement data)
    {
        var changed = false;
        if (data.TryGetProperty("plan", out var plan) && plan.ValueKind == JsonValueKind.Object)
        {
            var summary = ReadJsonString(plan, "title");
            if (string.IsNullOrWhiteSpace(summary) && plan.TryGetProperty("steps", out var steps) && steps.ValueKind == JsonValueKind.Array && steps.GetArrayLength() > 0)
            {
                var first = steps[0];
                summary = first.ValueKind == JsonValueKind.Object ? ReadJsonString(first, "title") : "";
            }
            if (!string.IsNullOrWhiteSpace(summary))
            {
                _composerControllerValue.SetPlanSummary(summary);
                changed = true;
            }
        }

        if (data.TryGetProperty("goal", out var goal) && goal.ValueKind == JsonValueKind.Object)
        {
            var goalText = ReadJsonString(goal, "text");
            if (!string.IsNullOrWhiteSpace(goalText))
            {
                _composerControllerValue.SetPursueGoalText(goalText);
            }
            _composerControllerValue.SetPursueGoalStatus(ReadJsonString(goal, "status"));
            _composerControllerValue.SetPursueGoalProgress(ReadJsonString(goal, "progress_percent"));
            _composerControllerValue.SetPursueGoalNextAction(ReadJsonString(goal, "next_action"));
            _composerControllerValue.SetPursueGoalTurnCount(ReadJsonString(goal, "turn_count"));
            if (string.Equals(ReadJsonString(goal, "status"), "complete", StringComparison.OrdinalIgnoreCase))
            {
                _composerControllerValue.SetPursueGoalEnabled(false);
            }
            changed = true;
        }

        if (changed)
        {
            _composerControllerValue.RenderComposerAttachmentStatus();
            _ = SaveStateAsync();
        }
    }

    private static readonly JsonElement EmptyJsonObject = JsonDocument.Parse("{}").RootElement;

    private void RecordEvent(RuntimeEvent ev, JsonElement payload, bool replay)
    {
        var eventRecord = new DesktopEvent
        {
            Type = replay ? $"{ev.Type} (history)" : ev.Type,
            Time = replay ? "history" : DateTime.Now.ToString("T"),
            // 心跳/ack 类事件可能没有 payload 字段 → default(JsonElement)=Undefined。
            // Undefined 一旦进 _state.Events，之后每次 SaveStateAsync 序列化都抛
            // InvalidOperationException（"Operation is not valid..."），保存从此全挂。
            Payload = payload.ValueKind == JsonValueKind.Undefined ? EmptyJsonObject : payload,
        };
        var duplicate = _state.Events.Any(existing => existing.Type == eventRecord.Type && existing.Time == eventRecord.Time);
        if (!duplicate)
        {
            _state.Events.Add(eventRecord);
            _state.Events = _state.Events.TakeLast(120).ToList();
        }
    }
}
