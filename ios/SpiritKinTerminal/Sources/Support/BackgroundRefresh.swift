import BackgroundTasks
import Foundation

enum BackgroundRefresh {
    static let identifier = "com.spiritkin.terminal.refresh"

    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            handle(task: task as? BGAppRefreshTask)
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(task: BGAppRefreshTask?) {
        schedule()
        task?.expirationHandler = {
            task?.setTaskCompleted(success: false)
        }
        task?.setTaskCompleted(success: true)
    }
}
