namespace SpiritKinDesktop.Tests;

public sealed class RuntimeControllerTests
{
    [Theory]
    [InlineData("确认执行", true)]
    [InlineData("可以执行", true)]
    [InlineData("继续执行", true)]
    [InlineData("取消执行", true)]
    [InlineData("不要执行", true)]
    [InlineData("停止执行", true)]
    [InlineData("中止执行", true)]
    [InlineData("确认", true)]
    [InlineData("取消", true)]
    [InlineData("普通聊天内容", false)]
    [InlineData("", false)]
    public void IsConfirmationControlTextDetectsExecutionControlMessages(string text, bool expected)
    {
        Assert.Equal(expected, RuntimeController.IsConfirmationControlText(text));
    }
}
