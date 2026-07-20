using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class LearningController
{
    internal void FillReviewCommitteeModelsFromEnabled()
    {
        var enabled = _assistModels
            .Where(item => item.Enabled)
            .OrderByDescending(item => item.Priority)
            .ToArray();
        WorkbenchShell.ManagementPanels.ReviewCommitteeModelsBox.Text = string.Join(Environment.NewLine, enabled.Select(item => item.ModelId));
        var requiredRoles = enabled
            .Select(item => item.Role)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Take(3)
            .ToArray();
        if (requiredRoles.Length > 0)
        {
            WorkbenchShell.ManagementPanels.ReviewCommitteeRequiredRolesBox.Text = string.Join(Environment.NewLine, requiredRoles);
        }
        WorkbenchShell.ManagementPanels.ReviewCommitteeSummaryText.Text = $"已填入 {enabled.Length} 个启用协助模型。";
    }

    internal void ToggleOllamaService()
    {
        if (IsOllamaRunning())
        {
            StopOllama();
            return;
        }
        StartOllama();
    }

    internal void ToggleSelectedProviderService()
    {
        var provider = SelectedProviderId();
        if (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase))
        {
            ToggleOllamaService();
            return;
        }
        if (string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase))
        {
            ToggleLmStudioService();
            return;
        }
        if (IsLlamaCppProvider(provider))
        {
            ToggleLlamaCppService();
            return;
        }
        WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"{SelectedProviderDefinition().DisplayName} 不需要由桌面启动服务。";
    }

    internal bool IsOllamaRunning()
    {
        return _ollamaProcess is { HasExited: false } || Process.GetProcessesByName("ollama").Any();
    }

    internal void SyncProviderServiceButtonState()
    {
        var provider = SelectedProviderId();
        if (string.Equals(provider, "ollama", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.ProviderServiceButton.Content = IsOllamaRunning() ? "停止服务" : "启动服务";
            WorkbenchShell.ManagementPanels.ProviderServiceButton.IsEnabled = true;
            return;
        }
        if (string.Equals(provider, "lmstudio", StringComparison.OrdinalIgnoreCase))
        {
            WorkbenchShell.ManagementPanels.ProviderServiceButton.Content = IsLmStudioRunning() ? "停止服务" : "启动服务";
            WorkbenchShell.ManagementPanels.ProviderServiceButton.IsEnabled = true;
            return;
        }
        if (IsLlamaCppProvider(provider))
        {
            WorkbenchShell.ManagementPanels.ProviderServiceButton.Content = IsLlamaCppRunning() ? "停止服务" : "启动服务";
            WorkbenchShell.ManagementPanels.ProviderServiceButton.IsEnabled = true;
            return;
        }
        WorkbenchShell.ManagementPanels.ProviderServiceButton.Content = "服务";
        WorkbenchShell.ManagementPanels.ProviderServiceButton.IsEnabled = false;
    }

    internal void StartOllama()
    {
        try
        {
            if (_ollamaProcess is not null && !_ollamaProcess.HasExited)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "Ollama 已在运行。";
                SyncProviderServiceButtonState();
                return;
            }
            if (Process.GetProcessesByName("ollama").Any())
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "Ollama 进程已在运行：http://127.0.0.1:11434";
                SyncProviderServiceButtonState();
                return;
            }
            var exe = ResolveCommandPath("ollama") ?? FindExistingPath(new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Ollama", "ollama.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Ollama", "ollama.exe"),
            });
            if (exe is null)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "未找到 ollama.exe。请先安装 Ollama，或把 ollama 加入 PATH。";
                return;
            }
            _ollamaProcess = Process.Start(new ProcessStartInfo(exe, "serve")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已尝试启动 Ollama 服务：http://127.0.0.1:11434";
            SyncProviderServiceButtonState();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"启动 Ollama 失败：{ex.Message}";
        }
    }

    internal void StopOllama()
    {
        try
        {
            if (_ollamaProcess is { HasExited: false })
            {
                _ollamaProcess.Kill(entireProcessTree: true);
                _ollamaProcess.Dispose();
                _ollamaProcess = null;
            }
            else
            {
                foreach (var process in Process.GetProcessesByName("ollama"))
                {
                    process.Kill(entireProcessTree: true);
                    process.Dispose();
                }
            }
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已停止 Ollama。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"停止 Ollama 失败：{ex.Message}";
        }
        finally
        {
            SyncProviderServiceButtonState();
        }
    }

    internal void ToggleLmStudioService()
    {
        if (IsLmStudioRunning())
        {
            StopLmStudio();
            return;
        }
        StartLmStudio();
    }

    internal bool IsLmStudioRunning()
    {
        return _lmStudioProcess is { HasExited: false }
            || Process.GetProcessesByName("lms").Any()
            || Process.GetProcessesByName("LM Studio").Any()
            || Process.GetProcessesByName("LM Studio Helper").Any();
    }

    internal void StartLmStudio()
    {
        try
        {
            var lms = ResolveCommandPath("lms") ?? FindExistingPath(new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".lmstudio", "bin", "lms.exe"),
            });
            if (lms is not null)
            {
                _lmStudioProcess = Process.Start(new ProcessStartInfo(lms, "server start")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                });
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已尝试启动 LM Studio 本地服务：http://127.0.0.1:1234/v1";
                SyncProviderServiceButtonState();
                return;
            }
            var app = FindExistingPath(new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "LM Studio", "LM Studio.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "LM Studio", "LM Studio.exe"),
            });
            if (app is null)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "未找到 LM Studio 或 lms.exe。请先安装 LM Studio，并启用本地服务器。";
                return;
            }
            _lmStudioProcess = Process.Start(new ProcessStartInfo(app) { UseShellExecute = true });
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已打开 LM Studio。请在 LM Studio 内启动本地服务器。";
            SyncProviderServiceButtonState();
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"启动 LM Studio 失败：{ex.Message}";
        }
    }

    internal void StopLmStudio()
    {
        try
        {
            var lms = ResolveCommandPath("lms") ?? FindExistingPath(new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".lmstudio", "bin", "lms.exe"),
            });
            if (lms is not null)
            {
                using var process = Process.Start(new ProcessStartInfo(lms, "server stop")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                });
                process?.WaitForExit(3000);
            }
            if (_lmStudioProcess is { HasExited: false })
            {
                _lmStudioProcess.Kill(entireProcessTree: true);
                _lmStudioProcess.Dispose();
                _lmStudioProcess = null;
            }
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已请求停止 LM Studio 本地服务。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"停止 LM Studio 失败：{ex.Message}";
        }
        finally
        {
            SyncProviderServiceButtonState();
        }
    }

    internal void ToggleLlamaCppService()
    {
        if (IsLlamaCppRunning())
        {
            StopLlamaCpp();
            return;
        }
        StartLlamaCpp();
    }

    internal bool IsLlamaCppRunning()
    {
        if (IsLlamaCppPortListening(8080) && IsLlamaCppPortListening(8081))
        {
            return true;
        }
        var root = ResolveSpiritKinRoot(AppContext.BaseDirectory);
        var executable = ResolveLlamaCppServerPath(root, Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_SERVER"));
        return IsManagedProcessRunning(_llamaCppProcess, executable)
            || IsManagedProcessRunning(_llamaCppEmbeddingProcess, executable)
            || IsPidFileProcessRunning(Path.Combine(root, "state", "llama.cpp", "chat.pid"), executable)
            || IsPidFileProcessRunning(Path.Combine(root, "state", "llama.cpp", "embedding.pid"), executable);
    }

    internal async Task<bool> IsLlamaCppChatHealthyAsync()
    {
        var configured = Environment.GetEnvironmentVariable("LLAMACPP_BASE_URL");
        var baseUrl = string.IsNullOrWhiteSpace(configured) ? "http://127.0.0.1:8080/v1" : configured.Trim().TrimEnd('/');
        var modelsUrl = baseUrl.EndsWith("/v1", StringComparison.OrdinalIgnoreCase)
            ? $"{baseUrl}/models"
            : $"{baseUrl}/v1/models";
        using var request = new HttpRequestMessage(HttpMethod.Get, modelsUrl);
        var apiKey = Environment.GetEnvironmentVariable("LLAMACPP_API_KEY")?.Trim();
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            apiKey = WorkbenchShell.ManagementPanels.CloudApiKeyBox.Password.Trim();
        }
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
        }
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        try
        {
            using var response = await _http.SendAsync(request, timeout.Token);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }
            using var models = JsonDocument.Parse(await response.Content.ReadAsStringAsync(timeout.Token));
            if (!models.RootElement.TryGetProperty("data", out var items)
                || items.ValueKind != JsonValueKind.Array
                || items.GetArrayLength() == 0)
            {
                return false;
            }
            var model = items[0].TryGetProperty("id", out var idValue) ? idValue.GetString()?.Trim() ?? "" : "";
            if (model.Length == 0)
            {
                return false;
            }

            var completionsUrl = baseUrl.EndsWith("/v1", StringComparison.OrdinalIgnoreCase)
                ? $"{baseUrl}/chat/completions"
                : $"{baseUrl}/v1/chat/completions";
            using var probe = new HttpRequestMessage(HttpMethod.Post, completionsUrl)
            {
                Content = new StringContent(BuildLlamaCppHealthProbePayload(model), System.Text.Encoding.UTF8, "application/json"),
            };
            if (!string.IsNullOrWhiteSpace(apiKey))
            {
                probe.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            }
            using var probeResponse = await _http.SendAsync(probe, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
            return probeResponse.IsSuccessStatusCode;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
        catch (HttpRequestException)
        {
            return false;
        }
    }

    internal static string BuildLlamaCppHealthProbePayload(string model) => JsonSerializer.Serialize(new
    {
        model,
        messages = new[] { new { role = "user", content = "Reply OK." } },
        max_tokens = 1,
        temperature = 0,
        stream = false,
        chat_template_kwargs = new { enable_thinking = false },
    });

    internal void RestartLlamaCppAfterHealthFailure()
    {
        StopLlamaCpp();
        StartLlamaCpp();
    }

    internal void StartLlamaCpp()
    {
        try
        {
            var root = ResolveSpiritKinRoot(AppContext.BaseDirectory);
            var executable = ResolveLlamaCppServerPath(root, Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_SERVER"));
            var chatPidPath = Path.Combine(root, "state", "llama.cpp", "chat.pid");
            var embeddingPidPath = Path.Combine(root, "state", "llama.cpp", "embedding.pid");
            var chatRunning = IsLlamaCppPortListening(8080)
                || IsManagedProcessRunning(_llamaCppProcess, executable)
                || IsPidFileProcessRunning(chatPidPath, executable);
            var embeddingRunning = IsLlamaCppPortListening(8081)
                || IsManagedProcessRunning(_llamaCppEmbeddingProcess, executable)
                || IsPidFileProcessRunning(embeddingPidPath, executable);
            if (chatRunning && embeddingRunning)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "llama.cpp 已在运行：8080 / 8081。";
                return;
            }
            if (executable is null)
            {
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "未找到 llama-server.exe。请先安装 llama.cpp 到 runtime/llama.cpp。";
                return;
            }

            var libraryRoot = WorkbenchShell.ManagementPanels.ModelLibraryPathBox.Text.Trim();
            var preferredModel = WorkbenchShell.ManagementPanels.CloudModelBox.Text.Trim();
            var textModel = ResolveLlamaCppModelPath(
                root,
                Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_TEXT_MODEL"),
                libraryRoot,
                preferredModel,
                embedding: false);
            var embeddingModel = ResolveLlamaCppModelPath(
                root,
                Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_EMBEDDING_MODEL"),
                Path.Combine(root, "runtime", "llama.cpp", "models"),
                "nomic-embed-text-v1.5",
                embedding: true);
            if ((!chatRunning && textModel is null) || (!embeddingRunning && embeddingModel is null))
            {
                var missing = !chatRunning && textModel is null ? "聊天 GGUF" : "embedding GGUF";
                WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"未找到 {missing}。请检查模型库路径或 SPIRITKIN_LLAMA_CPP_*_MODEL。";
                return;
            }

            StopLmStudioServerSilently();
            var stateRoot = Path.Combine(root, "state", "llama.cpp");
            Directory.CreateDirectory(stateRoot);
            var apiKey = WorkbenchShell.ManagementPanels.CloudApiKeyBox.Password.Trim();
            var contextSize = ReadPositiveIntEnvironment("SPIRITKIN_LLAMA_CPP_CONTEXT", 8192);
            var parallel = ReadPositiveIntEnvironment("SPIRITKIN_LLAMA_CPP_PARALLEL", 2);
            var projector = textModel is null
                ? null
                : ResolveLlamaCppProjector(root, textModel, Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_MMPROJ"));

            if (!embeddingRunning)
            {
                _llamaCppEmbeddingProcess = StartLlamaCppProcess(
                    executable,
                    BuildLlamaCppArguments(
                        embeddingModel!,
                        "text-embedding-nomic-embed-text-v1.5",
                        8081,
                        Path.Combine(stateRoot, "embedding.log"),
                        embedding: true,
                        contextSize: 2048,
                        parallel: 1,
                        apiKey: apiKey),
                    embeddingPidPath);
            }
            if (!chatRunning)
            {
                _llamaCppProcess = StartLlamaCppProcess(
                    executable,
                    BuildLlamaCppArguments(
                        textModel!,
                        string.IsNullOrWhiteSpace(preferredModel) ? "qwen/qwen3.6-35b-a3b" : preferredModel,
                        8080,
                        Path.Combine(stateRoot, "chat.log"),
                        embedding: false,
                        contextSize: contextSize,
                        parallel: parallel,
                        projectorPath: projector,
                        apiKey: apiKey),
                    chatPidPath);
            }

            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "llama.cpp 正在加载聊天与向量模型：8080 / 8081。首次启动可能需要约一分钟。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"启动 llama.cpp 失败：{ex.Message}";
        }
        finally
        {
            SyncProviderServiceButtonState();
        }
    }

    internal void AutoStartLlamaCpp()
    {
        var configured = Environment.GetEnvironmentVariable("SPIRITKIN_AUTO_START_LLAMACPP");
        if (string.Equals(configured, "0", StringComparison.OrdinalIgnoreCase)
            || string.Equals(configured, "false", StringComparison.OrdinalIgnoreCase)
            || string.Equals(configured, "off", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        StartLlamaCpp();
    }

    internal void StopLlamaCpp()
    {
        try
        {
            var root = ResolveSpiritKinRoot(AppContext.BaseDirectory);
            var executable = ResolveLlamaCppServerPath(root, Environment.GetEnvironmentVariable("SPIRITKIN_LLAMA_CPP_SERVER"));
            StopManagedProcess(ref _llamaCppProcess, Path.Combine(root, "state", "llama.cpp", "chat.pid"), executable);
            StopManagedProcess(ref _llamaCppEmbeddingProcess, Path.Combine(root, "state", "llama.cpp", "embedding.pid"), executable);
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = "已停止 llama.cpp 聊天与向量服务。";
        }
        catch (Exception ex)
        {
            WorkbenchShell.ManagementPanels.CloudProviderStatusText.Text = $"停止 llama.cpp 失败：{ex.Message}";
        }
        finally
        {
            SyncProviderServiceButtonState();
        }
    }

    internal static string ResolveSpiritKinRoot(string startPath)
    {
        var current = new DirectoryInfo(Path.GetFullPath(startPath));
        while (current is not null)
        {
            if (File.Exists(Path.Combine(current.FullName, "config", "config.yaml"))
                && Directory.Exists(Path.Combine(current.FullName, "desktop")))
            {
                return current.FullName;
            }
            current = current.Parent;
        }
        return Directory.GetCurrentDirectory();
    }

    internal static string? ResolveLlamaCppServerPath(string root, string? configuredPath)
    {
        var configured = ResolveConfiguredPath(root, configuredPath);
        if (configured is not null && File.Exists(configured))
        {
            return ResolveExecutableIdentity(configured);
        }
        var runtimeRoot = Path.Combine(root, "runtime", "llama.cpp");
        var current = Path.Combine(runtimeRoot, "current", "llama-server.exe");
        if (File.Exists(current))
        {
            return ResolveExecutableIdentity(current);
        }
        if (!Directory.Exists(runtimeRoot))
        {
            return null;
        }
        return Directory.EnumerateFiles(runtimeRoot, "llama-server.exe", SearchOption.AllDirectories)
            .OrderByDescending(path => path, StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault();
    }

    internal static string? ResolveLlamaCppModelPath(
        string root,
        string? configuredPath,
        string libraryRoot,
        string preferredModel,
        bool embedding)
    {
        var configured = ResolveConfiguredPath(root, configuredPath);
        if (configured is not null && File.Exists(configured))
        {
            return configured;
        }
        var searchRoot = ResolveConfiguredPath(root, libraryRoot);
        if (searchRoot is null || !Directory.Exists(searchRoot))
        {
            return null;
        }
        var preferred = NormalizeModelSearchText(preferredModel);
        var candidates = Directory.EnumerateFiles(searchRoot, "*.gguf", SearchOption.AllDirectories)
            .Where(path => !Path.GetFileName(path).StartsWith("mmproj-", StringComparison.OrdinalIgnoreCase))
            .Where(path => embedding == Path.GetFileName(path).Contains("embed", StringComparison.OrdinalIgnoreCase))
            .Select(path => new FileInfo(path))
            .OrderByDescending(file => !string.IsNullOrWhiteSpace(preferred)
                && NormalizeModelSearchText(file.FullName).Contains(preferred, StringComparison.OrdinalIgnoreCase))
            .ThenByDescending(file => file.Length)
            .ToArray();
        return candidates.FirstOrDefault()?.FullName;
    }

    internal static string? ResolveLlamaCppProjector(string root, string textModel, string? configuredPath)
    {
        var configured = ResolveConfiguredPath(root, configuredPath);
        if (configured is not null && File.Exists(configured))
        {
            return configured;
        }
        var modelDirectory = Path.GetDirectoryName(textModel);
        return modelDirectory is null
            ? null
            : Directory.EnumerateFiles(modelDirectory, "mmproj-*.gguf", SearchOption.TopDirectoryOnly).FirstOrDefault();
    }

    internal static IReadOnlyList<string> BuildLlamaCppArguments(
        string modelPath,
        string alias,
        int port,
        string logPath,
        bool embedding,
        int contextSize,
        int parallel,
        string? projectorPath = null,
        string? apiKey = null)
    {
        var arguments = new List<string>
        {
            "-m", modelPath,
            "--host", "127.0.0.1",
            "--port", port.ToString(CultureInfo.InvariantCulture),
            "--alias", alias,
            "-ngl", "auto",
            "-c", contextSize.ToString(CultureInfo.InvariantCulture),
            "-np", parallel.ToString(CultureInfo.InvariantCulture),
            "--metrics",
            "--no-webui",
            "--log-file", logPath,
            "--log-timestamps",
        };
        if (embedding)
        {
            arguments.Add("--embedding");
            arguments.Add("--pooling");
            arguments.Add("mean");
        }
        else if (!string.IsNullOrWhiteSpace(projectorPath))
        {
            arguments.Add("--mmproj");
            arguments.Add(projectorPath);
            arguments.Add("--no-mmproj-offload");
        }
        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            arguments.Add("--api-key");
            arguments.Add(apiKey);
        }
        return arguments;
    }

    internal static string ResolveExecutableIdentity(string executablePath)
    {
        var fullPath = Path.GetFullPath(executablePath);
        var directoryPath = Path.GetDirectoryName(fullPath);
        if (directoryPath is null)
        {
            return fullPath;
        }
        try
        {
            var target = new DirectoryInfo(directoryPath).ResolveLinkTarget(returnFinalTarget: true);
            return target is null ? fullPath : Path.Combine(target.FullName, Path.GetFileName(fullPath));
        }
        catch (IOException)
        {
            return fullPath;
        }
        catch (UnauthorizedAccessException)
        {
            return fullPath;
        }
    }

    internal static bool IsLlamaCppPortListening(int port)
    {
        try
        {
            using var client = new TcpClient();
            var connect = client.ConnectAsync("127.0.0.1", port);
            return connect.Wait(TimeSpan.FromMilliseconds(250)) && client.Connected;
        }
        catch
        {
            return false;
        }
    }

    private static Process StartLlamaCppProcess(string executable, IReadOnlyList<string> arguments, string pidPath)
    {
        var startInfo = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            WorkingDirectory = Path.GetDirectoryName(executable) ?? Directory.GetCurrentDirectory(),
        };
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }
        var process = Process.Start(startInfo) ?? throw new InvalidOperationException("llama-server.exe 未能创建进程");
        Directory.CreateDirectory(Path.GetDirectoryName(pidPath) ?? ".");
        File.WriteAllText(pidPath, process.Id.ToString(CultureInfo.InvariantCulture));
        return process;
    }

    private static bool IsManagedProcessRunning(Process? tracked, string? expectedExecutable)
    {
        if (tracked is null || tracked.HasExited || expectedExecutable is null)
        {
            return false;
        }
        return ProcessMatchesExecutable(tracked, expectedExecutable);
    }

    private static bool IsPidFileProcessRunning(string pidPath, string? expectedExecutable)
    {
        if (expectedExecutable is null || !File.Exists(pidPath)
            || !int.TryParse(File.ReadAllText(pidPath).Trim(), out var pid))
        {
            return false;
        }
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited && ProcessMatchesExecutable(process, expectedExecutable);
        }
        catch
        {
            return false;
        }
    }

    private static bool ProcessMatchesExecutable(Process process, string expectedExecutable)
    {
        try
        {
            return string.Equals(process.MainModule?.FileName, expectedExecutable, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static void StopManagedProcess(ref Process? tracked, string pidPath, string? expectedExecutable)
    {
        Process? process = tracked;
        tracked = null;
        if (process is null && expectedExecutable is not null && File.Exists(pidPath)
            && int.TryParse(File.ReadAllText(pidPath).Trim(), out var pid))
        {
            try
            {
                process = Process.GetProcessById(pid);
            }
            catch
            {
                process = null;
            }
        }
        if (process is not null)
        {
            try
            {
                if (!process.HasExited && expectedExecutable is not null && ProcessMatchesExecutable(process, expectedExecutable))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(5000);
                }
            }
            finally
            {
                process.Dispose();
            }
        }
        if (File.Exists(pidPath))
        {
            File.Delete(pidPath);
        }
    }

    private static string? ResolveConfiguredPath(string root, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }
        var expanded = Environment.ExpandEnvironmentVariables(value.Trim());
        return Path.GetFullPath(Path.IsPathRooted(expanded) ? expanded : Path.Combine(root, expanded));
    }

    private static string NormalizeModelSearchText(string value)
    {
        return new string((value ?? "").ToLowerInvariant().Where(char.IsLetterOrDigit).ToArray());
    }

    private static int ReadPositiveIntEnvironment(string name, int fallback)
    {
        return int.TryParse(Environment.GetEnvironmentVariable(name), out var value) && value > 0 ? value : fallback;
    }

    private static void StopLmStudioServerSilently()
    {
        try
        {
            var lms = FindExistingPath(new[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".lmstudio", "bin", "lms.exe"),
            });
            if (lms is null)
            {
                return;
            }
            using var process = Process.Start(new ProcessStartInfo(lms, "server stop")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            process?.WaitForExit(3000);
        }
        catch
        {
            // A stale LM Studio server must not block llama.cpp startup.
        }
    }

}

