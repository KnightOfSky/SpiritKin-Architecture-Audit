import AppIntents
import Foundation
import UIKit
import UserNotifications

struct AskSpiritAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Spirit"
    static var description = IntentDescription("Send a question to the paired SpiritKin desktop Runtime.")
    static var openAppWhenRun = false

    @Parameter(title: "Question")
    var question: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw SpiritKinIntentError.missingQuestion }
        let submission = try await SpiritKinIntentConfiguration.api().askSpirit(
            text,
            sessionID: "ios-app-intent"
        )
        let reply: String
        switch submission.status {
        case .completed(let result):
            reply = result
        case .pending:
            reply = "The request is running on the desktop Runtime. Open SpiritKin to view the result."
        case .failed(let error):
            throw SpiritKinIntentError.runtime(error)
        }
        return .result(value: reply, dialog: "\(reply)")
    }
}

struct CheckSpiritStatusAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Check Spirit Status"
    static var description = IntentDescription("Check whether the configured SpiritKin control plane is reachable.")
    static var openAppWhenRun = false

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let health = try await SpiritKinIntentConfiguration.api().health()
        let service = health["service"].stringValue.isEmpty ? "SpiritKin" : health["service"].stringValue
        let result = health["ok"].boolValue ? "\(service) is online." : "\(service) reported an unhealthy state."
        return .result(value: result, dialog: "\(result)")
    }
}

struct ReadClipboardAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Read Clipboard"
    static var description = IntentDescription("Read the iPhone clipboard after an explicit user invocation.")
    static var openAppWhenRun = false

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let value = await MainActor.run { UIPasteboard.general.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        guard !value.isEmpty else { throw SpiritKinIntentError.emptyClipboard }
        return .result(value: value, dialog: "Clipboard content is ready.")
    }
}

struct WriteClipboardAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Write Clipboard"
    static var description = IntentDescription("Write text to the iPhone clipboard after an explicit user invocation.")
    static var openAppWhenRun = false

    @Parameter(title: "Text")
    var text: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { throw SpiritKinIntentError.missingText }
        await MainActor.run { UIPasteboard.general.string = value }
        return .result(value: value, dialog: "Copied to the iPhone clipboard.")
    }
}

struct SendLocalNotificationAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Send Spirit Notification"
    static var description = IntentDescription("Post a local SpiritKin notification on this iPhone.")
    static var openAppWhenRun = false

    @Parameter(title: "Message")
    var message: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let value = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { throw SpiritKinIntentError.missingText }
        let center = UNUserNotificationCenter.current()
        guard try await center.requestAuthorization(options: [.alert, .sound]) else {
            throw SpiritKinIntentError.notificationDenied
        }
        let content = UNMutableNotificationContent()
        content.title = "SpiritKin"
        content.body = value
        content.sound = .default
        try await center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
        return .result(value: value, dialog: "Notification posted.")
    }
}

struct CheckBatteryAppIntent: AppIntent {
    static var title: LocalizedStringResource = "Check iPhone Battery"
    static var description = IntentDescription("Read the current iPhone battery level and charging state.")
    static var openAppWhenRun = false

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let result = await MainActor.run { () -> String in
            UIDevice.current.isBatteryMonitoringEnabled = true
            let level = UIDevice.current.batteryLevel < 0 ? "unknown" : "\(Int((UIDevice.current.batteryLevel * 100).rounded()))%"
            let state: String = switch UIDevice.current.batteryState {
            case .charging: "charging"
            case .full: "full"
            case .unplugged: "unplugged"
            default: "unknown"
            }
            return "Battery \(level), \(state)."
        }
        return .result(value: result, dialog: "\(result)")
    }
}

struct SpiritKinAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskSpiritAppIntent(),
            phrases: [
                "Ask \(.applicationName)",
                "Send a question to \(.applicationName)"
            ],
            shortTitle: "Ask Spirit",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: CheckSpiritStatusAppIntent(),
            phrases: [
                "Check \(.applicationName) status",
                "Is \(.applicationName) online"
            ],
            shortTitle: "Check Status",
            systemImageName: "heart.text.square"
        )
        AppShortcut(
            intent: ReadClipboardAppIntent(),
            phrases: ["Read clipboard with \(.applicationName)"],
            shortTitle: "Read Clipboard",
            systemImageName: "doc.on.clipboard"
        )
        AppShortcut(
            intent: WriteClipboardAppIntent(),
            phrases: ["Copy text with \(.applicationName)"],
            shortTitle: "Write Clipboard",
            systemImageName: "arrow.right.doc.on.clipboard"
        )
        AppShortcut(
            intent: SendLocalNotificationAppIntent(),
            phrases: ["Send a \(.applicationName) notification"],
            shortTitle: "Notify",
            systemImageName: "bell"
        )
        AppShortcut(
            intent: CheckBatteryAppIntent(),
            phrases: ["Check battery with \(.applicationName)"],
            shortTitle: "Battery",
            systemImageName: "battery.100percent"
        )
    }
}

private enum SpiritKinIntentConfiguration {
    static func api() throws -> TerminalAPI {
        let rawBaseURL = UserDefaults.standard.string(forKey: "spiritkin.ios.baseURL") ?? ""
        guard let baseURL = URL(string: rawBaseURL),
              let scheme = baseURL.scheme?.lowercased(),
              ["http", "https"].contains(scheme) else {
            throw SpiritKinIntentError.invalidConfiguration
        }
        let loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].contains(baseURL.host?.lowercased() ?? "")
        if scheme == "http" && !loopback {
            throw SpiritKinIntentError.insecureRemoteURL
        }
        return TerminalAPI(
            baseURL: baseURL,
            token: KeychainStore.read("ios.token") ?? "",
            workspaceID: UserDefaults.standard.string(forKey: "spiritkin.ios.workspaceID") ?? ""
        )
    }
}

private enum SpiritKinIntentError: LocalizedError {
    case invalidConfiguration
    case insecureRemoteURL
    case missingQuestion
    case missingText
    case emptyClipboard
    case notificationDenied
    case runtime(String)

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration:
            return "Open SpiritKin and configure the control plane address first."
        case .insecureRemoteURL:
            return "Remote SpiritKin connections must use HTTPS."
        case .missingQuestion:
            return "Enter a question for SpiritKin."
        case .missingText:
            return "Enter text first."
        case .emptyClipboard:
            return "The iPhone clipboard is empty."
        case .notificationDenied:
            return "Enable SpiritKin notifications in iOS Settings."
        case .runtime(let message):
            return "SpiritKin Runtime failed: \(message)"
        }
    }
}
