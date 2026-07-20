using System.Windows.Input;

namespace SpiritKinDesktop.Tests;

public sealed class ShellInteractionControllerTests
{
    [Theory]
    [InlineData(Key.Enter, true)]
    [InlineData(Key.Space, false)]
    [InlineData(Key.A, false)]
    public void IsSubmitKeyOnlyAcceptsEnterKeys(Key key, bool expected)
    {
        Assert.Equal(expected, ShellInteractionController.IsSubmitKey(key));
    }
}
