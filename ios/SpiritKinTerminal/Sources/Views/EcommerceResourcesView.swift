import SwiftUI

private enum CommerceResourceKind: String {
    case store = "commerce_store"
    case product = "commerce_product"

    var title: String { self == .store ? "店铺" : "商品" }
    var symbol: String { self == .store ? "storefront" : "shippingbox" }
}

private struct CommerceResourceEditor: Identifiable {
    let id = UUID()
    let kind: CommerceResourceKind
    let resource: DynamicJSON?
}

struct EcommerceResourcesView: View {
    @EnvironmentObject private var store: TerminalStore
    @State private var editor: CommerceResourceEditor?

    private var stores: [DynamicJSON] { store.ecommerceSnapshot["stores"]["items"].arrayValue }
    private var products: [DynamicJSON] { store.ecommerceSnapshot["products"]["items"].arrayValue }

    var body: some View {
        List {
            Section("店铺") {
                if stores.isEmpty {
                    ContentUnavailableView(
                        "尚未登记店铺",
                        systemImage: "storefront",
                        description: Text("先登记店铺和本机凭据引用，再创建属于该店铺的商品。")
                    )
                } else {
                    ForEach(stores.indices, id: \.self) { index in
                        resourceButton(stores[index], kind: .store)
                    }
                }
            }

            Section("商品") {
                if products.isEmpty {
                    Text(stores.isEmpty ? "登记店铺后即可创建商品。" : "当前店铺还没有商品。")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(products.indices, id: \.self) { index in
                        resourceButton(products[index], kind: .product)
                    }
                }
            }

            Section {
                Text("店铺和商品按 workspace 隔离。登录 Cookie、密码和 Token 不上传，只保存 Keychain/Vault 凭据引用。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .fantasyCanvas()
        .navigationTitle("店铺与商品")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("新增店铺", systemImage: "storefront") { editor = CommerceResourceEditor(kind: .store, resource: nil) }
                    Button("新增商品", systemImage: "shippingbox") { editor = CommerceResourceEditor(kind: .product, resource: nil) }
                        .disabled(stores.isEmpty)
                } label: {
                    Image(systemName: "plus")
                }
                .accessibilityLabel("新增店铺或商品")
            }
        }
        .sheet(item: $editor) { context in
            CommerceResourceEditorView(context: context, stores: stores) { payload in
                await store.resourceAction(payload, successMessage: "\(context.kind.title)已保存")
                await store.refreshEcommerce(force: true)
            }
        }
        .task { await store.refreshEcommerce() }
        .safeAreaInset(edge: .bottom) { StatusBar(text: store.statusMessage) }
    }

    private func resourceButton(_ resource: DynamicJSON, kind: CommerceResourceKind) -> some View {
        Button {
            editor = CommerceResourceEditor(kind: kind, resource: resource)
        } label: {
            RowLine(
                title: resource["label"].stringValue,
                subtitle: subtitle(resource, kind: kind),
                status: resource["health_status"].stringValue
            )
        }
        .buttonStyle(.plain)
        .swipeActions {
            if resource["deletable"].boolValue {
                Button("删除", role: .destructive) {
                    Task {
                        await store.resourceAction([
                            "action": .string("delete"),
                            "resource_id": .string(resource["resource_id"].stringValue)
                        ], successMessage: "\(kind.title)已删除")
                        await store.refreshEcommerce(force: true)
                    }
                }
            }
        }
    }

    private func subtitle(_ resource: DynamicJSON, kind: CommerceResourceKind) -> String {
        let metadata = resource["metadata"]
        if kind == .store {
            return [resource["platform"].stringValue, metadata["external_store_id"].stringValue]
                .filter { !$0.isEmpty }.joined(separator: " · ")
        }
        return [metadata["sku"].stringValue, metadata["price"].stringValue]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

private struct CommerceResourceEditorView: View {
    let context: CommerceResourceEditor
    let stores: [DynamicJSON]
    let onSave: ([String: DynamicJSON]) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var label: String
    @State private var platform: String
    @State private var externalStoreID: String
    @State private var credentialRef: String
    @State private var selectedStoreID: String
    @State private var sku: String
    @State private var price: String
    @State private var notes: String
    @State private var health: String

    init(context: CommerceResourceEditor, stores: [DynamicJSON], onSave: @escaping ([String: DynamicJSON]) async -> Void) {
        self.context = context
        self.stores = stores
        self.onSave = onSave
        let resource = context.resource ?? .object([:])
        let metadata = resource["metadata"]
        _label = State(initialValue: resource["label"].stringValue)
        _platform = State(initialValue: resource["platform"].stringValue.isEmpty ? "pdd" : resource["platform"].stringValue)
        _externalStoreID = State(initialValue: metadata["external_store_id"].stringValue)
        _credentialRef = State(initialValue: resource["credential_ref"].stringValue)
        _selectedStoreID = State(initialValue: metadata["store_resource_id"].stringValue.isEmpty ? stores.first?["resource_id"].stringValue ?? "" : metadata["store_resource_id"].stringValue)
        _sku = State(initialValue: metadata["sku"].stringValue)
        _price = State(initialValue: metadata["price"].stringValue)
        _notes = State(initialValue: metadata["notes"].stringValue)
        _health = State(initialValue: resource["health_status"].stringValue.isEmpty ? "ready" : resource["health_status"].stringValue)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("基本信息") {
                    TextField("\(context.kind.title)名称", text: $label)
                    if context.kind == .store {
                        Picker("平台", selection: $platform) {
                            Text("拼多多").tag("pdd")
                            Text("淘宝 / 天猫").tag("taobao")
                            Text("抖音电商").tag("douyin")
                            Text("京东").tag("jd")
                            Text("微信小店").tag("wechat_store")
                        }
                        TextField("平台店铺 ID（可选）", text: $externalStoreID)
                            .textInputAutocapitalization(.never)
                    } else {
                        Picker("所属店铺", selection: $selectedStoreID) {
                            ForEach(stores.indices, id: \.self) { index in
                                Text(stores[index]["label"].stringValue).tag(stores[index]["resource_id"].stringValue)
                            }
                        }
                        TextField("SKU", text: $sku)
                            .textInputAutocapitalization(.never)
                        TextField("价格", text: $price)
                            .keyboardType(.decimalPad)
                    }
                    TextField("备注", text: $notes, axis: .vertical)
                        .lineLimit(2...5)
                }
                if context.kind == .store {
                    Section("本机登录凭据") {
                        TextField("Keychain/Vault 引用", text: $credentialRef)
                            .textInputAutocapitalization(.never)
                        Text("例如 keychain:commerce/pdd/main。这里只登记引用，不上传 Cookie、密码或 Token。")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Section("状态") {
                    Picker("健康状态", selection: $health) {
                        Text("Ready").tag("ready")
                        Text("Degraded").tag("degraded")
                        Text("Offline").tag("offline")
                    }
                }
            }
            .navigationTitle(context.resource == nil ? "新增\(context.kind.title)" : "编辑\(context.kind.title)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await save() } }
                        .disabled(label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (context.kind == .product && selectedStoreID.isEmpty))
                }
            }
        }
    }

    private func save() async {
        let resourceID = context.resource?["resource_id"].stringValue
            ?? "\(context.kind.rawValue):\(UUID().uuidString.lowercased())"
        let selectedStore = stores.first { $0["resource_id"].stringValue == selectedStoreID }
        let resolvedPlatform = context.kind == .store ? platform : selectedStore?["platform"].stringValue ?? "commerce"
        var metadata: [String: DynamicJSON] = ["notes": .string(notes)]
        if context.kind == .store {
            metadata["external_store_id"] = .string(externalStoreID)
        } else {
            metadata["store_resource_id"] = .string(selectedStoreID)
            metadata["sku"] = .string(sku)
            metadata["price"] = .string(price)
        }
        let resource: [String: DynamicJSON] = [
            "resource_id": .string(resourceID),
            "label": .string(label.trimmingCharacters(in: .whitespacesAndNewlines)),
            "resource_type": .string(context.kind.rawValue),
            "platform": .string(resolvedPlatform),
            "owner_agent": .string("ecommerce"),
            "credential_ref": .string(context.kind == .store ? credentialRef.trimmingCharacters(in: .whitespacesAndNewlines) : ""),
            "health_status": .string(health),
            "supported_capabilities": .array((context.kind == .store
                ? ["commerce.product.publish", "commerce.price.update"]
                : ["commerce.product.review", "commerce.product.publish"]).map(DynamicJSON.string)),
            "tags": .array([.string("ecommerce"), .string(context.kind == .store ? "store" : "product")]),
            "metadata": .object(metadata)
        ]
        await onSave(["action": .string(context.resource == nil ? "create" : "update"), "resource": .object(resource)])
        dismiss()
    }
}
