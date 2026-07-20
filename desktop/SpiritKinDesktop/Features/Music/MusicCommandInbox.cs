using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;

namespace SpiritKinDesktop;

internal sealed record MusicCommand(string CommandId, string Action, JsonElement Arguments);

internal sealed class MusicCommandInbox
{
    private readonly string _path;
    private long _offset;

    internal MusicCommandInbox(string path, bool skipExisting = true)
    {
        _path = path;
        if (skipExisting && File.Exists(path))
        {
            _offset = new FileInfo(path).Length;
        }
    }

    internal IReadOnlyList<MusicCommand> ReadNew()
    {
        if (!File.Exists(_path))
        {
            return Array.Empty<MusicCommand>();
        }
        var commands = new List<MusicCommand>();
        using var stream = new FileStream(_path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        if (_offset > stream.Length)
        {
            _offset = 0;
        }
        stream.Seek(_offset, SeekOrigin.Begin);
        using var reader = new StreamReader(stream, leaveOpen: true);
        while (reader.ReadLine() is { } line)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }
            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                var commandId = root.TryGetProperty("command_id", out var id) ? id.GetString() ?? "" : "";
                var action = root.TryGetProperty("action", out var actionElement) ? actionElement.GetString() ?? "" : "";
                var arguments = root.TryGetProperty("arguments", out var args) && args.ValueKind == JsonValueKind.Object
                    ? args.Clone()
                    : JsonSerializer.SerializeToElement(new { });
                if (commandId.Length > 0 && action.Length > 0)
                {
                    commands.Add(new MusicCommand(commandId, action, arguments));
                }
            }
            catch (JsonException)
            {
            }
        }
        _offset = stream.Position;
        return commands;
    }
}
