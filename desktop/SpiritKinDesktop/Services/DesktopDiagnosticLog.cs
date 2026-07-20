using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.Json;

namespace SpiritKinDesktop;

internal static class DesktopDiagnosticLog
{
    private static readonly object Sync = new();

    internal static void Write(string rootDir, string area, string action, string status, string detail = "")
    {
        try
        {
            var directory = Path.Combine(rootDir, "state", "logs");
            Directory.CreateDirectory(directory);
            var entry = JsonSerializer.Serialize(new Dictionary<string, string>
            {
                ["time"] = DateTimeOffset.Now.ToString("O"),
                ["area"] = area,
                ["action"] = action,
                ["status"] = status,
                ["detail"] = detail,
            });
            lock (Sync)
            {
                File.AppendAllText(Path.Combine(directory, "desktop_actions.jsonl"), entry + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch
        {
            // Diagnostics must never become another desktop failure path.
        }
    }
}
