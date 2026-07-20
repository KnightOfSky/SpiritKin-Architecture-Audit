using System.IO;
using System.Text.Json;

namespace SpiritKinDesktop.Tests;

public sealed class LearningControllerTests
{
    [Fact]
    public void BuildLearningStatusSummaryIncludesEvolutionAndDatasetState()
    {
        using var doc = JsonDocument.Parse(
            """
            {
              "records": [{ "id": "r1" }],
              "dataset": {
                "path": "D:/SpiritKinAI/data/training.jsonl",
                "count": 12
              },
              "self_improvement_summary": {
                "status": "collecting",
                "counts": {
                  "learning_records": 3,
                  "dataset_examples": 12,
                  "self_training_examples": 2,
                  "improvement_actions": 4,
                  "eval_cases": 5
                },
                "loop": {
                  "auto_code_apply_enabled": false,
                  "human_review_required": true,
                  "runtime_feedback_collected": true,
                  "training_dataset_exported": false
                },
                "next_steps": [
                  { "title": "export dataset" },
                  { "title": "run eval" }
                ]
              },
              "review_committee_summary": {
                "status": "blocked",
                "configured_count": 1,
                "selected_count": 2,
                "min_success_count": 1,
                "pass_threshold": "0.7"
              }
            }
            """);

        var summary = LearningController.BuildLearningStatusSummary(doc.RootElement);

        Assert.Contains("collecting", summary);
        Assert.Contains("需人工审核", summary);
        Assert.Contains("训练集 未导出", summary);
        Assert.Contains("dataset 12", summary);
        Assert.Contains("export dataset", summary);
    }

    [Fact]
    public void ReadProviderModelNamesSortsAndDeduplicatesResponses()
    {
        using var openAi = JsonDocument.Parse(
            """
            {
              "data": [
                { "id": "qwen" },
                { "id": "QWEN" },
                { "id": "deepseek" }
              ]
            }
            """);
        using var ollama = JsonDocument.Parse(
            """
            {
              "models": [
                { "name": "llama3:8b" },
                { "name": "LLAMA3:8B" },
                { "name": "qwen2.5:7b" }
              ]
            }
            """);

        Assert.Equal(new[] { "deepseek", "qwen" }, LearningController.ReadOpenAiCompatibleModelNames(openAi.RootElement));
        Assert.Equal(new[] { "llama3:8b", "qwen2.5:7b" }, LearningController.ReadOllamaModelNames(ollama.RootElement));
    }

    [Theory]
    [InlineData("lmstudio", "Qwen 2.5 / Coder 7B", "lmstudio_qwen_2_5_coder_7b")]
    [InlineData("llamacpp", "Qwen 3.6 / 35B A3B", "llamacpp_qwen_3_6_35b_a3b")]
    [InlineData("ollama", "qwen2.5-coder:7b", "ollama_qwen2_5_coder_7b")]
    public void StableModelIdNormalizesProviderModelNames(string prefix, string value, string expected)
    {
        Assert.Equal(expected, LearningController.StableModelId(prefix, value));
    }

    [Fact]
    public void ResolveLlamaCppArtifactsUsesProjectRuntimeAndSeparatesEmbeddingModel()
    {
        var root = Path.Combine(Path.GetTempPath(), $"spiritkin-llama-{Guid.NewGuid():N}");
        try
        {
            var runtime = Path.Combine(root, "runtime", "llama.cpp", "b10058");
            var models = Path.Combine(root, "runtime", "llama.cpp", "models");
            Directory.CreateDirectory(runtime);
            Directory.CreateDirectory(models);
            Directory.CreateDirectory(Path.Combine(root, "config"));
            Directory.CreateDirectory(Path.Combine(root, "desktop"));
            File.WriteAllText(Path.Combine(root, "config", "config.yaml"), "models: {}\n");
            var server = Path.Combine(runtime, "llama-server.exe");
            var textModel = Path.Combine(models, "Qwen3.6-35B-A3B-Q4_K_M.gguf");
            var projector = Path.Combine(models, "mmproj-Qwen3.6.gguf");
            var embedding = Path.Combine(models, "nomic-embed-text-v1.5.Q4_K_M.gguf");
            File.WriteAllBytes(server, new byte[] { 1 });
            File.WriteAllBytes(textModel, new byte[] { 1, 2, 3 });
            File.WriteAllBytes(projector, new byte[] { 1, 2 });
            File.WriteAllBytes(embedding, new byte[] { 1 });

            Assert.Equal(root, LearningController.ResolveSpiritKinRoot(Path.Combine(root, "desktop")));
            Assert.Equal(server, LearningController.ResolveLlamaCppServerPath(root, null));
            Assert.Equal(textModel, LearningController.ResolveLlamaCppModelPath(root, null, models, "qwen3.6-35b", embedding: false));
            Assert.Equal(embedding, LearningController.ResolveLlamaCppModelPath(root, null, models, "nomic", embedding: true));
            Assert.Equal(projector, LearningController.ResolveLlamaCppProjector(root, textModel, null));
        }
        finally
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, recursive: true);
            }
        }
    }

    [Fact]
    public void BuildLlamaCppArgumentsProducesDedicatedChatAndEmbeddingModes()
    {
        var chat = LearningController.BuildLlamaCppArguments(
            "chat.gguf",
            "qwen/local",
            8080,
            "chat.log",
            embedding: false,
            contextSize: 8192,
            parallel: 2,
            projectorPath: "mmproj.gguf",
            apiKey: "local-key");
        var embedding = LearningController.BuildLlamaCppArguments(
            "embed.gguf",
            "nomic",
            8081,
            "embedding.log",
            embedding: true,
            contextSize: 2048,
            parallel: 1);

        Assert.Contains("--mmproj", chat);
        Assert.Contains("--api-key", chat);
        Assert.DoesNotContain("--embedding", chat);
        Assert.Contains("--embedding", embedding);
        Assert.Contains("--pooling", embedding);
        Assert.DoesNotContain("--mmproj", embedding);
    }

    [Fact]
    public void LlamaCppHealthProbeChecksRealGenerationWithoutThinking()
    {
        using var payload = JsonDocument.Parse(LearningController.BuildLlamaCppHealthProbePayload("qwen/local"));

        Assert.Equal("qwen/local", payload.RootElement.GetProperty("model").GetString());
        Assert.Equal(1, payload.RootElement.GetProperty("max_tokens").GetInt32());
        Assert.False(payload.RootElement.GetProperty("stream").GetBoolean());
        Assert.False(payload.RootElement.GetProperty("chat_template_kwargs").GetProperty("enable_thinking").GetBoolean());
    }

    [Fact]
    public void ResolveExecutableIdentityReturnsCanonicalPathForRegularFile()
    {
        var path = Path.GetFullPath(Path.Combine("runtime", "llama.cpp", "b10058", "llama-server.exe"));

        Assert.Equal(path, LearningController.ResolveExecutableIdentity(path));
    }
}
