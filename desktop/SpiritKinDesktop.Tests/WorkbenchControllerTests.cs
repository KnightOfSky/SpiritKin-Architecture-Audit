using SpiritKinDesktop.Controls;
using System.IO;
using System.Windows.Input;

namespace SpiritKinDesktop.Tests;

public sealed class WorkbenchControllerTests
{
    [Fact]
    public void WorkbenchLayoutInterpolationIsStrictlyLinear()
    {
        var midpoint = WorkbenchShellView.InterpolateWorkbenchLayout(
            workbenchFrom: 600,
            workbenchTo: 36,
            splitterFrom: 6,
            splitterTo: 0,
            avatarFrom: 300,
            avatarTo: 870,
            progress: 0.5);

        Assert.Equal(318, midpoint.Workbench);
        Assert.Equal(3, midpoint.Splitter);
        Assert.Equal(585, midpoint.Avatar);
        Assert.Equal(906, midpoint.Workbench + midpoint.Splitter + midpoint.Avatar);
    }

    [Theory]
    [InlineData(300, 870, 291.25)]
    [InlineData(300, 301, 240)]
    [InlineData(100, 1000, 332.5)]
    public void WorkbenchLayoutDurationScalesWithTravel(double from, double to, double expected)
    {
        Assert.Equal(expected, WorkbenchShellView.WorkbenchLayoutDurationMilliseconds(from, to));
    }

    [Theory]
    [InlineData("src/old.cs -> src/new.cs", "src/new.cs")]
    [InlineData("\"desktop\\MainWindow.xaml.cs\"", "desktop/MainWindow.xaml.cs")]
    [InlineData(" backend/app.py ", "backend/app.py")]
    public void NormalizeGitStatusPathKeepsFinalPath(string raw, string expected)
    {
        Assert.Equal(expected, WorkbenchController.NormalizeGitStatusPath(raw));
    }

    [Fact]
    public void ParseNumstatSummarizesLineDeltasByNormalizedPath()
    {
        var deltas = WorkbenchController.ParseNumstat(
            """
            12	3	desktop/MainWindow.xaml.cs
            -	-	assets/logo.png
            1	0	src/old.cs -> src/new.cs
            """);

        Assert.Equal("+12 -3", deltas["desktop/MainWindow.xaml.cs"]);
        Assert.Equal("+? -?", deltas["assets/logo.png"]);
        Assert.Equal("+1 -0", deltas["src/new.cs"]);
    }

    [Theory]
    [InlineData("## main...origin/main [ahead 2, behind 1]", "ahead", 2)]
    [InlineData("## main...origin/main [ahead 2, behind 1]", "behind", 1)]
    [InlineData("## main...origin/main", "ahead", 0)]
    public void ParseGitCounterReadsAheadBehindCounts(string text, string key, int expected)
    {
        Assert.Equal(expected, WorkbenchController.ParseGitCounter(text, key));
    }

    [Theory]
    [InlineData(0, "0s")]
    [InlineData(59, "59s")]
    [InlineData(60, "1m")]
    [InlineData(125, "2m 5s")]
    public void FormatDurationUsesCompactLabels(double seconds, string expected)
    {
        Assert.Equal(expected, WorkbenchController.FormatDuration(seconds));
    }

    [Theory]
    [InlineData(Key.A, true)]
    [InlineData(Key.D9, true)]
    [InlineData(Key.OemMinus, true)]
    [InlineData(Key.Escape, false)]
    public void IsTextInputKeyDetectsEditableTerminalKeys(Key key, bool expected)
    {
        Assert.Equal(expected, WorkbenchController.IsTextInputKey(key));
    }

    [Fact]
    public void GitWorkingDirectoryUsesTheCurrentProjectInsteadOfTheApplicationRoot()
    {
        var applicationRoot = Path.Combine(Path.GetTempPath(), "spiritkin-app", Guid.NewGuid().ToString("N"));
        var projectRoot = Path.Combine(Path.GetTempPath(), "spiritkin-project", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(applicationRoot);
        Directory.CreateDirectory(projectRoot);

        var resolved = WorkbenchController.ResolveGitWorkingDirectory(projectRoot, applicationRoot);

        Assert.Equal(Path.GetFullPath(projectRoot), resolved);
    }
}
