namespace SpiritKinDesktop.Tests;

public sealed class GlobalSearchControllerTests
{
    [Fact]
    public void TokenizeSearchQueryTrimsAndSplitsWhitespace()
    {
        Assert.Equal(new[] { "alpha", "beta", "gamma" }, GlobalSearchController.TokenizeSearchQuery("  alpha  beta\tgamma  "));
    }

    [Fact]
    public void SearchScoreRequiresEveryTermAndAddsPriority()
    {
        var score = GlobalSearchController.SearchScore("Agent route profile", new[] { "agent", "route" }, priority: 50);

        Assert.Equal(90, score);
        Assert.Equal(0, GlobalSearchController.SearchScore("Agent route profile", new[] { "agent", "missing" }, priority: 50));
    }
}
