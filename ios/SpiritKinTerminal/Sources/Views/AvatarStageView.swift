import SwiftUI
import WebKit

struct AvatarStageView: View {
    let url: URL?
    var isActive: Bool = true

    var body: some View {
        Group {
            if let url {
                AvatarWebView(url: url, isActive: isActive)
            } else {
                ContentUnavailableView(
                    "Avatar 暂不可用",
                    systemImage: "person.crop.circle.badge.exclamationmark",
                    description: Text("请先在设置中填写有效的主控地址。")
                )
            }
        }
        .background(FantasyTheme.surface2)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("SpiritKin 3D Avatar 主控舞台")
    }
}

private struct AvatarWebView: UIViewRepresentable {
    let url: URL
    let isActive: Bool

    final class Coordinator {
        var lastPaused: Bool?
        var lastURL: URL?
    }

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
        if isActive {
            load(url, in: webView)
            context.coordinator.lastURL = url
        }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard isActive else {
            if context.coordinator.lastPaused == true {
                webView.evaluateJavaScript("window.postMessage({type:'spiritkin.avatar.pause',paused:true},'*')")
            }
            context.coordinator.lastPaused = false
            return
        }
        if context.coordinator.lastURL != url {
            load(url, in: webView)
            context.coordinator.lastURL = url
        }
        if context.coordinator.lastPaused != true {
            webView.evaluateJavaScript("window.postMessage({type:'spiritkin.avatar.pause',paused:false},'*')")
            context.coordinator.lastPaused = true
        }
    }

    private func load(_ url: URL, in webView: WKWebView) {
        if url.isFileURL {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        } else {
            webView.load(URLRequest(url: url))
        }
    }
}
