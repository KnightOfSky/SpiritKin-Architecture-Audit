import Foundation

struct TerminalConfig: Equatable {
    var baseURL: String
    var token: String
    var workspaceID: String
    var pairingToken: String = ""

    static func parse(_ text: String) throws -> TerminalConfig {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(), scheme == "spiritkin-terminal" || scheme == "spiritkin" {
            return parse(url: url)
        }
        if let data = trimmed.data(using: .utf8),
           let object = try? JSONDecoder().decode(DynamicJSON.self, from: data).objectValue {
            return TerminalConfig(
                baseURL: object["base_url"]?.stringValue ?? object["baseURL"]?.stringValue ?? object["ios_base_url"]?.stringValue ?? "",
                token: object["token"]?.stringValue ?? object["ios_token"]?.stringValue ?? "",
                workspaceID: object["workspace_id"]?.stringValue ?? object["workspaceID"]?.stringValue ?? "",
                pairingToken: object["pairing_token"]?.stringValue ?? object["pairingToken"]?.stringValue ?? ""
            )
        }
        if let url = URL(string: trimmed), url.scheme?.hasPrefix("http") == true {
            return TerminalConfig(baseURL: trimmed, token: "", workspaceID: "")
        }
        throw TerminalConfigError.unsupportedFormat
    }

    static func parse(url: URL) -> TerminalConfig {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let items = components?.queryItems ?? []
        func value(_ names: String...) -> String {
            for name in names {
                if let item = items.first(where: { $0.name == name })?.value, !item.isEmpty {
                    return item
                }
            }
            return ""
        }
        return TerminalConfig(
            baseURL: value("base_url", "baseURL", "ios_base_url", "server_url"),
            token: value("token", "ios_token"),
            workspaceID: value("workspace_id", "workspaceID"),
            pairingToken: value("pairing_token", "pairingToken")
        )
    }
}

enum TerminalConfigError: LocalizedError {
    case unsupportedFormat
    case missingBaseURL

    var errorDescription: String? {
        switch self {
        case .unsupportedFormat:
            return "Unsupported config format."
        case .missingBaseURL:
            return "Config is missing base_url."
        }
    }
}
