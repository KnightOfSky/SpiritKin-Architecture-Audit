using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class ServicesControllerTests
{
    [Theory]
    [InlineData("Project A / Dev", "project_a_dev")]
    [InlineData("项目-01", "项目_01")]
    [InlineData("  commerce.shop#blue  ", "commerce_shop_blue")]
    public void NormalizeLocalProfileIdKeepsStableSegmentedIds(string value, string expected)
    {
        Assert.Equal(expected, ServicesController.NormalizeLocalProfileId(value));
    }

    [Fact]
    public void TryGetProjectPortProfileReadsConfiguredProfiles()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "config": {
                "profiles": {
                  "project_a": {
                    "label": "Project A",
                    "overrides": {
                      "frontend": 8790
                    }
                  }
                }
              }
            }
            """);

        Assert.True(ServicesController.TryGetProjectPortProfile(doc.RootElement, "project_a", out var profile));
        Assert.Equal("Project A", JsonResponseHelpers.ReadJsonString(profile, "label"));
        Assert.False(ServicesController.TryGetProjectPortProfile(doc.RootElement, "missing", out _));
    }

    [Fact]
    public void ProjectPortProfileMatchesCurrentComparesOverrides()
    {
        using var current = JsonDocument.Parse(
            """
            {
              "config": {
                "overrides": {
                  "frontend": 8790,
                  "command_gateway": 8788
                }
              }
            }
            """);
        using var matching = JsonDocument.Parse(
            """
            {
              "overrides": {
                "command_gateway": 8788,
                "frontend": 8790
              }
            }
            """);
        using var mismatched = JsonDocument.Parse(
            """
            {
              "overrides": {
                "frontend": 8791
              }
            }
            """);

        Assert.True(ServicesController.ProjectPortProfileMatchesCurrent(current.RootElement, matching.RootElement));
        Assert.False(ServicesController.ProjectPortProfileMatchesCurrent(current.RootElement, mismatched.RootElement));
    }

    [Fact]
    public void EnsureOkResponseThrowsReadableError()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "ok": false,
              "error": "port conflict",
              "detail": "frontend uses 8790"
            }
            """);

        var ex = Assert.Throws<InvalidOperationException>(() => JsonResponseHelpers.EnsureOkResponse(doc.RootElement, "failed"));

        Assert.Contains("port conflict", ex.Message);
        Assert.Contains("frontend uses 8790", ex.Message);
    }
}
