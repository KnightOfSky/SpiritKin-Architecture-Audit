import Foundation
import UserNotifications

@MainActor
final class NotificationManager: ObservableObject {
    @Published private(set) var authorizationStatus = "unknown"

    func refreshStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorizationStatus = label(for: settings.authorizationStatus)
    }

    func requestAuthorization() async {
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            await refreshStatus()
        } catch {
            authorizationStatus = error.localizedDescription
        }
    }

    func sendLocalTest() {
        let content = UNMutableNotificationContent()
        content.title = "SpiritKin Terminal"
        content.body = "Local notification test"
        content.sound = .default
        let request = UNNotificationRequest(identifier: "spiritkin-local-test", content: content, trigger: UNTimeIntervalNotificationTrigger(timeInterval: 3, repeats: false))
        UNUserNotificationCenter.current().add(request)
    }

    private func label(for status: UNAuthorizationStatus) -> String {
        switch status {
        case .authorized:
            return "authorized"
        case .denied:
            return "denied"
        case .notDetermined:
            return "not_determined"
        case .provisional:
            return "provisional"
        case .ephemeral:
            return "ephemeral"
        @unknown default:
            return "unknown"
        }
    }
}
