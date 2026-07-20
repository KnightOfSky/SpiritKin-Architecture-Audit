using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class SafetyControllerTests
{
    [Theory]
    [InlineData("""{"value":true}""", true)]
    [InlineData("""{"value":false}""", false)]
    [InlineData("""{"value":"true"}""", true)]
    [InlineData("""{"value":"false"}""", false)]
    [InlineData("""{"other":true}""", true)]
    public void SafetyJsonHelpersReadBoolMatchesDesktopJsonSemantics(string json, bool expected)
    {
        using var doc = JsonDocument.Parse(json);

        var actual = SafetyController.JsonHelpers.ReadBool(doc.RootElement, "value", fallback: true);

        Assert.Equal(expected, actual);
    }

    [Fact]
    public void SafetyJsonHelpersReadStringSupportsPrimitiveValuesAndFallback()
    {
        using var doc = JsonDocument.Parse("""{"text":"ready","number":42,"flag":true,"empty":""}""");

        Assert.Equal("ready", SafetyController.JsonHelpers.ReadString(doc.RootElement, "text", "--"));
        Assert.Equal("42", SafetyController.JsonHelpers.ReadString(doc.RootElement, "number", "--"));
        Assert.Equal("true", SafetyController.JsonHelpers.ReadString(doc.RootElement, "flag", "--"));
        Assert.Equal("--", SafetyController.JsonHelpers.ReadString(doc.RootElement, "empty", "--"));
        Assert.Equal("--", SafetyController.JsonHelpers.ReadString(doc.RootElement, "missing", "--"));
    }
}
