using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal async Task<JsonDocument> GetJsonAsync(string url)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, url);
        _workspaceControllerValue.ApplyAuth(request);
        using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(60));
        using var response = await _http.SendAsync(request, cts.Token);
        var text = await response.Content.ReadAsStringAsync(cts.Token);
        ServiceHealthSignals.RecordHttpStatus(response.StatusCode);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(text);
    }

    internal async Task<JsonDocument> PostJsonAsync(string url, object payload)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, url);
        _workspaceControllerValue.ApplyAuth(request);
        request.Content = new StringContent(JsonSerializer.Serialize(payload, _jsonOptions), Encoding.UTF8, "application/json");
        using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(60));
        using var response = await _http.SendAsync(request, cts.Token);
        var text = await response.Content.ReadAsStringAsync(cts.Token);
        ServiceHealthSignals.RecordHttpStatus(response.StatusCode);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(text);
    }

    private static void EnsureOkResponse(JsonElement root, string actionLabel) => JsonResponseHelpers.EnsureOkResponse(root, actionLabel);

}
