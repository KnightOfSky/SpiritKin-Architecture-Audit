namespace SpiritKinDesktop.Tests;

public sealed class NavigationControllerTests
{
    [Theory]
    [InlineData("session")]
    [InlineData("project")]
    [InlineData("task")]
    public void NewIdUsesPrefixAndStableBoundedLength(string prefix)
    {
        var id = NavigationController.NewId(prefix);

        Assert.StartsWith($"{prefix}_", id);
        Assert.True(id.Length >= prefix.Length + 2);
        Assert.True(id.Length <= prefix.Length + 17);
    }

    [Fact]
    public void TryFindExistingProjectByWorkspaceMatchesNormalizedPath()
    {
        var projects = new[]
        {
            new DesktopItem { Id = "project_a", Title = "A", WorkspacePath = @"D:\Work\Shop" },
            new DesktopItem { Id = "project_b", Title = "B", Detail = @"workspace: D:\Work\Other" },
        };

        var existing = NavigationController.TryFindExistingProjectByWorkspace(
            projects,
            @"D:\Work\Shop\",
            path => path.Replace('/', '\\'));

        Assert.NotNull(existing);
        Assert.Equal("project_a", existing.Id);

        var detailExisting = NavigationController.TryFindExistingProjectByWorkspace(
            projects,
            @"D:\Work\Other",
            path => path.Replace('/', '\\'));
        Assert.NotNull(detailExisting);
        Assert.Equal("project_b", detailExisting.Id);
    }

    [Fact]
    public void DeleteProjectAndSessionsFromStateRemovesProjectSessions()
    {
        var state = new DesktopState
        {
            ActiveSessionId = "session_project",
            Projects = new List<DesktopItem>
            {
                new() { Id = "project_a", Title = "Project A" },
                new() { Id = "project_b", Title = "Project B" },
            },
            Sessions = new List<DesktopSession>
            {
                new() { Id = "session_project", Title = "Project chat", ProjectId = "project_a", UpdatedAt = 3 },
                new() { Id = "session_other_project", Title = "Other project", ProjectId = "project_b", UpdatedAt = 2 },
                new() { Id = "session_chat", Title = "Standalone", UpdatedAt = 1 },
            },
        };
        var pendingSessions = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var pendingProjects = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        var result = NavigationController.DeleteProjectAndSessionsFromState(
            state,
            "project_a",
            pendingSessions,
            pendingProjects);

        Assert.NotNull(result);
        Assert.Equal("Project A", result.ProjectTitle);
        Assert.Equal(1, result.SessionCount);
        Assert.DoesNotContain(state.Projects, project => project.Id == "project_a");
        Assert.DoesNotContain(state.Sessions, session => session.Id == "session_project");
        Assert.Contains(state.Sessions, session => session.Id == "session_other_project");
        Assert.Contains(state.Sessions, session => session.Id == "session_chat");
        Assert.Contains("session_project", pendingSessions);
        Assert.Contains("project_a", pendingProjects);
        Assert.NotEqual("session_project", state.ActiveSessionId);
    }

    [Fact]
    public void CollaborationThreadIdsForProjectDeletionIncludesProjectAndSessions()
    {
        var sessions = new[]
        {
            new DesktopSession { Id = "session_project", ProjectId = "project_a" },
            new DesktopSession { Id = "Session With Spaces", ProjectId = "project_a" },
        };

        var threadIds = NavigationController.CollaborationThreadIdsForProjectDeletion("project_a", sessions);

        Assert.Contains("project-project_a", threadIds);
        Assert.Contains("session-session_project", threadIds);
        Assert.Contains("session-session-with-spaces", threadIds);
        Assert.Equal(threadIds.Count, threadIds.Distinct(StringComparer.OrdinalIgnoreCase).Count());
    }
}
