using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class WorkspaceControllerTests
{
    [Fact]
    public void QuickChatDraftDoesNotCreateSessionUntilFirstMessageIsCommitted()
    {
        var existing = new DesktopSession
        {
            Id = "session_existing",
            Title = "Existing",
            Messages = new List<DesktopMessage>
            {
                new() { Id = "msg_existing", Role = "user", Text = "之前的消息" },
            },
        };
        var state = new DesktopState
        {
            ActiveSessionId = existing.Id,
            Sessions = new List<DesktopSession> { existing },
        };

        var beforeClickCount = state.Sessions.Count;

        Assert.Equal(1, beforeClickCount);
        Assert.Same(existing, WorkspaceController.ResolveQuickChatSessionForSend(
            state,
            quickChatMode: false,
            sessionId: "unused",
            now: 100,
            out var materializedExisting));
        Assert.False(materializedExisting);
        Assert.Single(state.Sessions);

        var draft = WorkspaceController.ResolveQuickChatSessionForSend(
            state,
            quickChatMode: true,
            sessionId: "session_quick",
            now: 200,
            out var materializedDraft);

        Assert.True(materializedDraft);
        Assert.Equal("session_quick", state.ActiveSessionId);
        Assert.Equal("新会话", draft.Title);
        Assert.Empty(draft.Messages);
        Assert.Equal(2, state.Sessions.Count);
        Assert.Equal("帮我整理今天的会议记录，并列出待办", RuntimeController.SummarizeSessionTitle("帮我整理今天的会议记录，并列出待办。"));
    }

    [Fact]
    public void AvatarOpeningBubbleOnlyPrefillsComposerWithoutSending()
    {
        var accepted = WorkspaceController.TryReadAvatarSuggestionMessage(
            """{"type":"spiritkin.open_suggestion","signal_id":"signal-1","prompt":"请恢复未完成任务"}""",
            out var prompt);
        var ignored = WorkspaceController.TryReadAvatarSuggestionMessage(
            """{"type":"spiritkin.execute_suggestion","prompt":"立即执行"}""",
            out _);

        Assert.True(accepted);
        Assert.Equal("请恢复未完成任务", prompt);
        Assert.False(ignored);
    }

    [Fact]
    public void DetectPackageManagerPrefersUvLock()
    {
        var workspace = CreateTempWorkspace();
        File.WriteAllText(Path.Combine(workspace, "package.json"), "{}");
        File.WriteAllText(Path.Combine(workspace, "uv.lock"), "");

        Assert.Equal("uv", WorkspaceController.DetectPackageManager(workspace));
    }

    [Fact]
    public void DetectPackageManagerReadsCommonProjectMarkers()
    {
        var npmWorkspace = CreateTempWorkspace();
        File.WriteAllText(Path.Combine(npmWorkspace, "package.json"), "{}");

        var dotnetWorkspace = CreateTempWorkspace();
        File.WriteAllText(Path.Combine(dotnetWorkspace, "App.csproj"), "<Project />");

        Assert.Equal("npm", WorkspaceController.DetectPackageManager(npmWorkspace));
        Assert.Equal("dotnet", WorkspaceController.DetectPackageManager(dotnetWorkspace));
    }

    [Fact]
    public void ExtractWorkspaceCandidatesReadsLabelsAndRootedPaths()
    {
        var detail =
            """
            owner: desktop
            workspace: D:\SpiritKinAI
            工作区：D:\Commerce
            C:\Standalone
            """;

        var candidates = WorkspaceController.ExtractWorkspaceCandidates(detail).ToArray();

        Assert.Contains(@"D:\SpiritKinAI", candidates);
        Assert.Contains(@"D:\Commerce", candidates);
        Assert.Contains(@"C:\Standalone", candidates);
    }

    [Theory]
    [InlineData("0", 0, true)]
    [InlineData("65535", 65535, true)]
    [InlineData("65536", 0, false)]
    [InlineData("abc", 0, false)]
    public void TryReadPortFromStringValidatesRange(string raw, int expectedPort, bool expected)
    {
        Assert.Equal(expected, WorkspaceController.TryReadPort(raw, out var port));
        Assert.Equal(expectedPort, port);
    }

    [Fact]
    public void TryReadPortFromJsonReadsNumbersAndStrings()
    {
        using var doc = JsonDocument.Parse("""{"number": 8790, "text": "8788", "bad": -1}""");

        Assert.True(WorkspaceController.TryReadPort(doc.RootElement.GetProperty("number"), out var number));
        Assert.True(WorkspaceController.TryReadPort(doc.RootElement.GetProperty("text"), out var text));
        Assert.False(WorkspaceController.TryReadPort(doc.RootElement.GetProperty("bad"), out _));
        Assert.Equal(8790, number);
        Assert.Equal(8788, text);
    }

    [Fact]
    public void ReadEnvFileHandlesExportSyntaxAndQuotes()
    {
        var workspace = CreateTempWorkspace();
        var envFile = Path.Combine(workspace, ".env");
        File.WriteAllText(
            envFile,
            """
            # comment
            export API_KEY="abc"
            PLAIN=value
            SINGLE='quoted'
            ignored
            """);

        var env = WorkspaceController.ReadEnvFile(envFile);

        Assert.Equal("abc", env["API_KEY"]);
        Assert.Equal("value", env["PLAIN"]);
        Assert.Equal("quoted", env["SINGLE"]);
        Assert.False(env.ContainsKey("ignored"));
    }

    [Fact]
    public void ApplyEnvironmentCopiesValuesIntoProcessStartInfo()
    {
        var startInfo = new ProcessStartInfo("cmd.exe");

        WorkspaceController.ApplyEnvironment(startInfo, new Dictionary<string, string>
        {
            ["SPIRITKIN_TEST"] = "enabled",
            [""] = "ignored",
        });

        Assert.Equal("enabled", startInfo.Environment["SPIRITKIN_TEST"]);
        Assert.False(startInfo.Environment.ContainsKey(""));
    }

    [Fact]
    public void ProjectCreationStartsAtDriveRootInsteadOfApplicationFolder()
    {
        var applicationFolder = Path.Combine(Path.GetTempPath(), "SpiritKinAI");

        var initial = NavigationController.ProjectCreationInitialDirectory(applicationFolder);

        Assert.Equal(Path.GetPathRoot(applicationFolder), initial);
        Assert.NotEqual(applicationFolder, initial);
    }

    [Fact]
    public void WorkspaceProjectContextOverridesThePreviousSessionProject()
    {
        var projects = new[]
        {
            new DesktopItem { Id = "project_a", Title = "A", WorkspacePath = @"C:\A" },
            new DesktopItem { Id = "project_b", Title = "B", WorkspacePath = @"D:\B" },
        };
        var activeSession = new DesktopSession { Id = "session_a", ProjectId = "project_a" };

        var selected = WorkspaceController.ResolveWorkspaceProjectContext(projects, activeSession, "project_b");
        var active = WorkspaceController.ResolveWorkspaceProjectContext(projects, activeSession, "");

        Assert.Equal("project_b", selected?.Id);
        Assert.Equal("project_a", active?.Id);
    }

    private static string CreateTempWorkspace()
    {
        var path = Path.Combine(Path.GetTempPath(), "spiritkin-workspace-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
