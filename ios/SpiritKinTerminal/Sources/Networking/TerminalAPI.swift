import Foundation

enum SpiritJobStatus {
    case pending
    case completed(String)
    case failed(String)
}

struct SpiritSubmission {
    let jobID: String
    let status: SpiritJobStatus
}

struct TerminalAPI {
    var baseURL: URL
    var token: String
    var workspaceID: String = ""

    private var session: URLSession { .shared }

    func snapshot(forceRefresh: Bool = false) async throws -> TerminalSnapshot {
        var components = URLComponents(url: endpoint("ios/native/snapshot"), resolvingAgainstBaseURL: false)!
        if forceRefresh {
            components.queryItems = [URLQueryItem(name: "refresh", value: "1")]
        }
        let (data, response) = try await session.data(for: request(url: components.url!, method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(TerminalSnapshot.self, from: data)
    }

    func action(_ payload: [String: DynamicJSON]) async throws -> TerminalActionResponse {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(for: request(url: endpoint("ios/native/action"), method: "POST", json: true), from: data)
        try validate(response: response, data: body)
        return try JSONDecoder().decode(TerminalActionResponse.self, from: body)
    }

    func pairControl(pairingToken: String, terminalID: String = "ios-native-controller") async throws -> DynamicJSON {
        let payload = DynamicJSON.object([
            "pairing_token": .string(pairingToken),
            "device_id": .string(terminalID),
            "terminal_id": .string(terminalID),
            "workspace_id": .string(workspaceID)
        ])
        let data = try JSONEncoder().encode(payload)
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/control/pair"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)
    }

    func createPairing(deviceRole: String, ttlMinutes: Int = 10) async throws -> DynamicJSON {
        var components = URLComponents(url: endpoint("ios/control/pairing"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "workspace_id", value: workspaceID),
            URLQueryItem(name: "device_role", value: deviceRole),
            URLQueryItem(name: "requested_by", value: "ios_native_controller"),
            URLQueryItem(name: "ttl_minutes", value: String(max(1, min(60, ttlMinutes)))),
            URLQueryItem(name: "format", value: "json")
        ]
        let (data, response) = try await session.data(for: request(url: components.url!, method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["pairing"]
    }

    func askSpirit(_ text: String, sessionID: String) async throws -> SpiritSubmission {
        let payload = DynamicJSON.object([
            "shortcut_name": .string("Ask Spirit"),
            "action": .string("ask_spirit"),
            "input_text": .string(text),
            "async": .bool(true),
            "metadata": .object([
                "frontend": .string("ios_native_terminal"),
                "client_type": .string("ios_controller"),
                "session_id": .string(sessionID)
            ])
        ])
        let data = try JSONEncoder().encode(payload)
        var shortcutRequest = request(url: endpoint("ios/shortcut"), method: "POST", json: true)
        // The receiver acknowledges first; model work is polled separately so the
        // iOS UI never waits on a stalled desktop model connection.
        shortcutRequest.timeoutInterval = 8
        let (body, response) = try await session.upload(
            for: shortcutRequest,
            from: data
        )
        try validate(response: response, data: body)
        guard !body.isEmpty else {
            return SpiritSubmission(jobID: "", status: .failed("桌面 Runtime 返回了空响应。"))
        }
        let decoded = try JSONDecoder().decode(DynamicJSON.self, from: body)
        let jobID = decoded["job_id"].stringValue
        if !jobID.isEmpty {
            // Keep the foreground wait short. A queued job continues in a
            // cancellable background task owned by TerminalStore.
            for _ in 0..<8 {
                try Task.checkCancellation()
                try await Task.sleep(nanoseconds: 400_000_000)
                let status = try await spiritJobStatus(jobID)
                if case .pending = status { continue }
                return SpiritSubmission(jobID: jobID, status: status)
            }
            return SpiritSubmission(jobID: jobID, status: .pending)
        }
        let shortcutResult = decoded["shortcut_output"]["result"].stringValue
        if !shortcutResult.isEmpty {
            return SpiritSubmission(jobID: "", status: .completed(shortcutResult))
        }
        let reply = decoded["reply"]["text"].stringValue
        return SpiritSubmission(
            jobID: "",
            status: reply.isEmpty ? .failed("桌面 Runtime 未返回有效回复。") : .completed(reply)
        )
    }

    func spiritJobStatus(_ jobID: String) async throws -> SpiritJobStatus {
        let (jobBody, jobResponse) = try await session.data(
            for: request(url: endpoint("ios/jobs/\(jobID)"), method: "GET")
        )
        try validate(response: jobResponse, data: jobBody)
        let job = try JSONDecoder().decode(DynamicJSON.self, from: jobBody)["job"]
        let status = job["status"].stringValue
        if status == "queued" || status == "running" || status.isEmpty {
            return .pending
        }
        if status == "failed" {
            let error = job["error"].stringValue
            return .failed(error.isEmpty ? "桌面 Runtime 任务失败，请查看监控。" : error)
        }
        let result = job["result"]
        let shortcut = result["shortcut_output"]["result"].stringValue
        if !shortcut.isEmpty { return .completed(shortcut) }
        let reply = result["reply"]["text"].stringValue
        if !reply.isEmpty { return .completed(reply) }
        let error = result["error"].stringValue
        return .failed(error.isEmpty ? "桌面 Runtime 未返回有效回复。" : error)
    }

    func sessions() async throws -> DesktopSessionState {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/sessions"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DesktopSessionState.self, from: data)
    }

    func shortcutCatalog() async throws -> [DynamicJSON] {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/schemas/shortcuts.json"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["shortcuts"].arrayValue
    }

    func health() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("health"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)
    }

    func heartbeat(terminalID: String) async throws {
        let payload = DynamicJSON.object([
            "terminal_id": .string(terminalID),
            "workspace_id": .string(workspaceID)
        ])
        let data = try JSONEncoder().encode(payload)
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/heartbeat"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
    }

    func capabilities() async throws -> IOSCapabilitySnapshot {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/capabilities"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(IOSCapabilitySnapshot.self, from: data)
    }

    func domains() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/domains"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)
    }

    func domainsAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(for: request(url: endpoint("ios/domains"), method: "POST", json: true), from: data)
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)
    }

    func updateCapability(_ capabilityID: String, enabled: Bool) async throws -> IOSCapabilitySnapshot {
        let payload = DynamicJSON.object([
            "capability_id": .string(capabilityID),
            "enabled": .bool(enabled)
        ])
        let data = try JSONEncoder().encode(payload)
        let (body, response) = try await session.upload(for: request(url: endpoint("ios/capabilities"), method: "POST", json: true), from: data)
        try validate(response: response, data: body)
        return try JSONDecoder().decode(IOSCapabilitySnapshot.self, from: body)
    }

    func pools() async throws -> IOSPoolSnapshot {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/pools"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(IOSPoolSnapshot.self, from: data)
    }

    func poolAction(_ payload: [String: DynamicJSON]) async throws -> IOSPoolSnapshot {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/pools"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(IOSPoolSnapshot.self, from: body)
    }

    func resources() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/resources"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["resource_management"]
    }

    func resourceAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/resources"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["resource_management"]
    }

    func monitor() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/monitor"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["monitor"]
    }

    func monitorAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/monitor"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["monitor"]
    }

    func growth() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/growth"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["growth"]
    }

    func growthAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/growth"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["growth"]
    }

    func runtimeHost() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/runtime-host"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["runtime_host"]
    }

    func runtimeHostAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/runtime-host"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["runtime_host"]
    }

    func worldState() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/world"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["world_state"]
    }

    func publishObservation(_ observation: DynamicJSON) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(["observation": observation]))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/observations"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["world_state"]
    }

    func ecommerce() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/ecommerce"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["ecommerce"]
    }

    func ecommerceAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/ecommerce"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["ecommerce"]
    }

    func music() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/music"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["music"]
    }

    func musicAction(_ payload: [String: DynamicJSON]) async throws -> DynamicJSON {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/music"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DynamicJSON.self, from: body)["music"]
    }

    func channels() async throws -> DynamicJSON {
        let (data, response) = try await session.data(for: request(url: endpoint("ios/channels"), method: "GET"))
        try validate(response: response, data: data)
        return try JSONDecoder().decode(DynamicJSON.self, from: data)["channels"]
    }

    func updateSessions(
        _ state: DesktopSessionState,
        deletedSessionIDs: [String] = []
    ) async throws -> DesktopSessionState {
        let stateData = try JSONEncoder().encode(state)
        let stateJSON = try JSONDecoder().decode(DynamicJSON.self, from: stateData)
        let payload = DynamicJSON.object([
            "state": stateJSON,
            "deleted_session_ids": .array(deletedSessionIDs.map(DynamicJSON.string)),
            "client_id": .string("ios_native_controller")
        ])
        let data = try JSONEncoder().encode(payload)
        let (body, response) = try await session.upload(
            for: request(url: endpoint("ios/sessions"), method: "POST", json: true),
            from: data
        )
        try validate(response: response, data: body)
        return try JSONDecoder().decode(DesktopSessionState.self, from: body)
    }

    func uploadArtifact(payload: [String: DynamicJSON]) async throws -> ArtifactUploadResponse {
        let data = try JSONEncoder().encode(DynamicJSON.object(payload))
        let (body, response) = try await session.upload(for: request(url: endpoint("mobile/artifacts"), method: "POST", json: true), from: data)
        try validate(response: response, data: body)
        return try JSONDecoder().decode(ArtifactUploadResponse.self, from: body)
    }

    func artifactURL(artifactID: String, fileIndex: Int = 0) -> URL {
        var components = URLComponents(url: endpoint("mobile/artifacts/\(artifactID)"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "file_index", value: String(fileIndex))]
        return components?.url ?? endpoint("mobile/artifacts/\(artifactID)")
    }

    func artifactData(artifactID: String, fileIndex: Int = 0) async throws -> Data {
        let (data, response) = try await session.data(for: request(url: artifactURL(artifactID: artifactID, fileIndex: fileIndex), method: "GET"))
        try validate(response: response, data: data)
        return data
    }

    private func endpoint(_ path: String) -> URL {
        let cleanPath = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return cleanPath
            .split(separator: "/")
            .reduce(baseURL) { partial, component in
                partial.appendingPathComponent(String(component))
            }
    }

    private func request(url: URL, method: String, json: Bool = false) -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if json {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            request.setValue(token, forHTTPHeaderField: "X-SpiritKin-iOS-Token")
        }
        if !workspaceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            request.setValue(workspaceID, forHTTPHeaderField: "X-SpiritKin-Workspace")
        }
        return request
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw TerminalAPIError.http(http.statusCode, text)
        }
    }
}

enum TerminalAPIError: LocalizedError {
    case invalidBaseURL
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "Base URL is invalid."
        case .http(let status, let body):
            return "HTTP \(status): \(body)"
        }
    }
}
