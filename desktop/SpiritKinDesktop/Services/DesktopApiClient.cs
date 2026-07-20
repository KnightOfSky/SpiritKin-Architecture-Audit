using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace SpiritKinDesktop;

internal sealed class DesktopApiClient
{
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly Func<string> _apiBaseProvider;
    private readonly Func<string> _tokenProvider;

    public DesktopApiClient(
        HttpClient http,
        JsonSerializerOptions jsonOptions,
        Func<string> apiBaseProvider,
        Func<string> tokenProvider)
    {
        _http = http;
        _jsonOptions = jsonOptions;
        _apiBaseProvider = apiBaseProvider;
        _tokenProvider = tokenProvider;
    }

    public Task<JsonDocument> GetModuleManagementAsync(CancellationToken cancellationToken = default)
    {
        return GetDesktopAsync("module-management", cancellationToken);
    }

    public Task<JsonDocument> ScanModuleManagementAsync(CancellationToken cancellationToken = default)
    {
        return PostDesktopAsync("module-management", new { action = "scan" }, cancellationToken);
    }

    public async Task<JsonDocument> GetDesktopAsync(string endpoint, CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, DesktopUrl(endpoint));
        ApplyAuth(request);
        using var response = await _http.SendAsync(request, cancellationToken);
        var text = await response.Content.ReadAsStringAsync(cancellationToken);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(text);
    }

    public async Task<JsonDocument> PostDesktopAsync(string endpoint, object payload, CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, DesktopUrl(endpoint));
        ApplyAuth(request);
        request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
        using var response = await _http.SendAsync(request, cancellationToken);
        var text = await response.Content.ReadAsStringAsync(cancellationToken);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(text);
    }

    private string DesktopUrl(string endpoint)
    {
        return $"{ApiBase()}/desktop/{endpoint.Trim().TrimStart('/')}";
    }

    private string ApiBase()
    {
        return (_apiBaseProvider() ?? $"http://127.0.0.1:{RealtimeContract.DefaultPorts.CommandGateway}").Trim().TrimEnd('/').Replace("/command", "", StringComparison.OrdinalIgnoreCase);
    }

    private void ApplyAuth(HttpRequestMessage request)
    {
        var token = (_tokenProvider() ?? "").Trim();
        if (string.IsNullOrWhiteSpace(token))
        {
            return;
        }
        request.Headers.Add("X-SpiritKin-Token", token);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }
}
