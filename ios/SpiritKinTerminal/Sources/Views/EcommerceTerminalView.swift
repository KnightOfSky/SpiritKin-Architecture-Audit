import SwiftUI
import WebKit

struct EcommerceTerminalView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var taskTitle = ""

    private var ecommerce: DynamicJSON { store.ecommerceSnapshot }
    private var tasks: [DynamicJSON] { ecommerce["queue"]["items"].arrayValue }
    private var incidents: [DynamicJSON] { store.monitorSnapshot["incidents"].arrayValue }
    private var resources: [DynamicJSON] { ecommerce["resources"]["items"].arrayValue }

    var body: some View {
        List {
            Section("运营概况") {
                HStack {
                    MetricCell(value: ecommerce["queue"]["count"].stringValue, label: "任务")
                    MetricCell(value: ecommerce["remote_workers"]["count"].stringValue, label: "Worker")
                    MetricCell(value: "\(incidents.count)", label: "风险")
                    MetricCell(value: ecommerce["resources"]["count"].stringValue, label: "资源")
                }
            }

            Section("新增任务") {
                TextField("任务名称", text: $taskTitle)
                Button("创建电商任务", systemImage: "plus") {
                    let title = taskTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !title.isEmpty else { return }
                    taskTitle = ""
                    Task {
                        await store.ecommerceAction([
                            "action": .string("create_task"),
                            "title": .string(title),
                            "task_type": .string("ecommerce_operation")
                        ], successMessage: "电商任务已创建")
                    }
                }
            }

            Section("运营任务") {
                if tasks.isEmpty {
                    Text("暂无任务").foregroundStyle(.secondary)
                } else {
                    ForEach(tasks.indices, id: \.self) { index in
                        let item = tasks[index]
                        RowLine(
                            title: item["title"].stringValue.isEmpty ? item["id"].stringValue : item["title"].stringValue,
                            subtitle: item["task_type"].stringValue,
                            status: item["status"].stringValue
                        )
                        .swipeActions {
                            Button("删除", role: .destructive) {
                                Task {
                                    await store.ecommerceAction([
                                        "action": .string("delete_task"),
                                        "task_id": .string(item["id"].stringValue)
                                    ], successMessage: "任务已删除")
                                }
                            }
                        }
                    }
                }
            }

            Section("监控与自愈") {
                if incidents.isEmpty {
                    Label("工作区、设备和 Remote Worker 正常", systemImage: "checkmark.circle")
                        .foregroundStyle(.green)
                } else {
                    ForEach(incidents.indices, id: \.self) { index in
                        let item = incidents[index]
                        RowLine(title: item["title"].stringValue, subtitle: item["detail"].stringValue, status: item["severity"].stringValue)
                    }
                }
                NavigationLink {
                    RuntimeMonitorView()
                } label: {
                    Label("打开运行监控", systemImage: "waveform.path.ecg")
                }
            }

            Section("Resource 资源") {
                if resources.isEmpty {
                    Text("暂无电商资源；可在桌面端 Resource Registry 登记。")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(resources.indices, id: \.self) { index in
                        let item = resources[index]
                        RowLine(title: item["label"].stringValue, subtitle: item["resource_type"].stringValue, status: item["status"].stringValue)
                    }
                }
                NavigationLink {
                    EcommerceResourcesView()
                } label: {
                    Label("管理店铺与商品", systemImage: "storefront")
                }
                NavigationLink {
                    ResourceManagementView()
                } label: {
                    Label("管理 Resource", systemImage: "externaldrive.connected.to.line.below")
                }
            }

            Section("高级") {
                if let url = store.ecommerceTerminalURL {
                    NavigationLink("打开原始 Terminal") {
                        RawEcommerceTerminalView(url: url, token: store.token)
                    }
                }
            }
        }
        .fantasyCanvas()
        .navigationTitle("电商运营")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button { Task { await store.refreshEcommerce(force: true) } } label: { Image(systemName: "arrow.clockwise") }
                .accessibilityLabel("刷新电商运营")
        }
        .task { await store.refreshEcommerce() }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }
}

private struct MetricCell: View {
    let value: String
    let label: String

    var body: some View {
        VStack(spacing: 3) {
            Text(value.isEmpty ? "0" : value).font(.headline)
            Text(label).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct RawEcommerceTerminalView: View {
    let url: URL
    let token: String

    var body: some View {
        EcommerceTerminalWebView(url: url, token: token)
            .navigationTitle("原始 Terminal")
            .navigationBarTitleDisplayMode(.inline)
    }
}

private struct EcommerceTerminalWebView: UIViewRepresentable {
    let url: URL
    let token: String

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        if let encoded = try? JSONEncoder().encode(token), let tokenLiteral = String(data: encoded, encoding: .utf8) {
            let source = """
            (() => { const token = \(tokenLiteral); const originalFetch = window.fetch.bind(window); window.fetch = (input, init = {}) => { const headers = new Headers(init.headers || {}); if (token) headers.set('X-SpiritKin-iOS-Token', token); return originalFetch(input, { ...init, headers }); }; })();
            """
            configuration.userContentController.addUserScript(WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: false))
        }
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = true
        webView.load(request())
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url != url else { return }
        webView.load(request())
    }

    private func request() -> URLRequest {
        var request = URLRequest(url: url)
        if !token.isEmpty { request.setValue(token, forHTTPHeaderField: "X-SpiritKin-iOS-Token") }
        return request
    }
}
