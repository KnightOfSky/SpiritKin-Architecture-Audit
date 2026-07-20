using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class SearchManagementControllerTests
{
    [Fact]
    public void BuildRuntimeStatusKeepsEmbeddingAndRerankerDegradationText()
    {
        using var stateDoc = JsonDocument.Parse("""{"schema_version":"search.v1"}""");
        using var webDoc = JsonDocument.Parse(
            """
            {
              "provider": "brave",
              "preferred": "brave,duckduckgo",
              "brave_configured": false
            }
            """);
        using var retrievalDoc = JsonDocument.Parse(
            """
            {
              "backend": "keyword",
              "knowledge_base_count": 3,
              "embedding_provider": "hashing",
              "embedding_model": "hashing-64",
              "embedding_configured": false,
              "reranker": "token_overlap",
              "reranker_model": "token-overlap",
              "reranker_configured": false
            }
            """);
        using var jobsDoc = JsonDocument.Parse(
            """
            {
              "count": 5,
              "failed_count": 1,
              "last_status": "failed",
              "last_error": "embedding provider unavailable"
            }
            """);

        var status = SearchManagementController.BuildRuntimeStatus(
            stateDoc.RootElement,
            webDoc.RootElement,
            retrievalDoc.RootElement,
            jobsDoc.RootElement,
            "Master 2");

        Assert.Contains("Brave Key 未配置", status);
        Assert.Contains("向量召回", status);
        Assert.Contains("占位或未完整配置", status);
        Assert.Contains("最近错误", status);
        Assert.Contains("模型目录角色：Master 2", status);
    }
}
