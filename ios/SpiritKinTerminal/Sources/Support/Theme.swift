import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Generated from design/tokens.json v4. Dynamic system appearance is resolved by UIKit.
enum FantasyTheme {
    static let primary = Color(light: 0x126DB6, dark: 0xE28E3A)
    static let secondary = Color(light: 0xB44A00, dark: 0xE99541)
    static let success = Color(light: 0x187A43, dark: 0x5BCC80)
    static let warning = Color(light: 0x875900, dark: 0xEFBC4B)
    static let danger = Color(light: 0xB52A2E, dark: 0xFF6B68)
    static let info = Color(light: 0x126DB6, dark: 0x75AFFF)
    static let onPrimary = Color(light: 0xFFFFFF, dark: 0x1F1914)
    static let text = Color(light: 0x273A4C, dark: 0xF0EAE4)
    static let muted = Color(light: 0x4D667B, dark: 0xACA39B)
    static let faint = Color(light: 0x5F7689, dark: 0x8B837B)
    static let surface = Color(light: 0xF5FBFF, dark: 0x1F1914)
    static let canvas = Color(light: 0xEEF5FA, dark: 0x15110C)
    static let surface2 = Color(light: 0xE9F2F7, dark: 0x2A221C)
    static let surface3 = Color(light: 0xDEEAF1, dark: 0x372E27)
    static let line = Color(light: 0xC4D4DF, dark: 0x4E463F)
    static let lineStrong = Color(light: 0x70869A, dark: 0x73665D)
    static let hover = surface3
    static let selected = surface3
    static let goldWash = Color(light: 0xFFF3D6, dark: 0x3A2B10)
    static let successBackground = Color(light: 0xE7F5EC, dark: 0x173523)
    static let warningBackground = Color(light: 0xFFF3D6, dark: 0x3A2B10)
    static let dangerBackground = Color(light: 0xFCEBEC, dark: 0x3B1D1C)
    static let infoBackground = Color(light: 0xE8F2FB, dark: 0x172A43)

    /// 把后端状态字串映射到品牌语义色，行为与 `RowLine` 历史分类一致，
    /// 仅把通用系统色替换为 Fantasy 色板。
    static func statusColor(for status: String) -> Color {
        switch status.lowercased() {
        case "running", "ready", "online", "completed", "succeeded", "available", "normal", "可用":
            return success
        case "waiting", "queued", "pending", "delivered":
            return warning
        case "failed", "blocked", "offline", "stop", "stopped", "canceled":
            return danger
        default:
            return muted
        }
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        let r = Double((hex >> 16) & 0xFF) / 255
        let g = Double((hex >> 8) & 0xFF) / 255
        let b = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: r, green: g, blue: b, opacity: opacity)
    }

    /// 跟随系统外观解析的动态色：浅色 / 深色两套 hex，交由 UIKit traitCollection 切换。
    init(light: UInt32, dark: UInt32, opacity: Double = 1) {
        #if canImport(UIKit)
        let resolved = UIColor { trait in
            let hex = trait.userInterfaceStyle == .dark ? dark : light
            let r = CGFloat((hex >> 16) & 0xFF) / 255
            let g = CGFloat((hex >> 8) & 0xFF) / 255
            let b = CGFloat(hex & 0xFF) / 255
            return UIColor(red: r, green: g, blue: b, alpha: CGFloat(opacity))
        }
        self.init(resolved)
        #else
        self.init(hex: light, opacity: opacity)
        #endif
    }
}

enum SpiritKinAppearance: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: return "系统"
        case .light: return "白天"
        case .dark: return "黑夜"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}
