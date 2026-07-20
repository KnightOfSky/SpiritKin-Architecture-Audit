import Social
import UIKit
import UniformTypeIdentifiers

final class ShareViewController: SLComposeServiceViewController {
    private var importedCount = 0
    private var importError: Error?

    override func isContentValid() -> Bool {
        !(extensionContext?.inputItems.isEmpty ?? true)
    }

    override func didSelectPost() {
        navigationItem.rightBarButtonItem?.isEnabled = false
        let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
        let group = DispatchGroup()
        for provider in providers.prefix(8) {
            group.enter()
            importProvider(provider) { [weak self] result in
                DispatchQueue.main.async {
                    switch result {
                    case .success:
                        self?.importedCount += 1
                    case .failure(let error):
                        self?.importError = error
                    }
                    group.leave()
                }
            }
        }
        group.notify(queue: .main) { [weak self] in
            guard let self else { return }
            if importedCount > 0 {
                extensionContext?.completeRequest(returningItems: nil)
            } else {
                let message = importError?.localizedDescription ?? "No supported files were shared."
                let alert = UIAlertController(title: "Unable to Share", message: message, preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "Close", style: .default) { _ in
                    self.extensionContext?.cancelRequest(withError: self.importError ?? ShareExtensionError.noSupportedItems)
                })
                present(alert, animated: true)
            }
        }
    }

    override func configurationItems() -> [Any]! { [] }

    private func importProvider(_ provider: NSItemProvider, completion: @escaping (Result<Void, Error>) -> Void) {
        let preferredType = provider.registeredTypeIdentifiers.first(where: { UTType($0)?.conforms(to: .item) == true })
            ?? provider.registeredTypeIdentifiers.first
        guard let preferredType else {
            completion(.failure(ShareExtensionError.noSupportedItems))
            return
        }
        provider.loadFileRepresentation(forTypeIdentifier: preferredType) { url, error in
            if let error {
                completion(.failure(error))
                return
            }
            if let url {
                do {
                    let data = try Data(contentsOf: url, options: [.mappedIfSafe])
                    guard data.count <= 8 * 1024 * 1024 else { throw ShareExtensionError.fileTooLarge }
                    _ = try SharedArtifactInbox.store(data: data, suggestedName: url.lastPathComponent)
                    completion(.success(()))
                } catch {
                    completion(.failure(error))
                }
                return
            }
            provider.loadDataRepresentation(forTypeIdentifier: preferredType) { data, error in
                do {
                    if let error { throw error }
                    guard let data else { throw ShareExtensionError.noSupportedItems }
                    guard data.count <= 8 * 1024 * 1024 else { throw ShareExtensionError.fileTooLarge }
                    let ext = UTType(preferredType)?.preferredFilenameExtension ?? "bin"
                    _ = try SharedArtifactInbox.store(data: data, suggestedName: "shared-file.\(ext)")
                    completion(.success(()))
                } catch {
                    completion(.failure(error))
                }
            }
        }
    }
}

private enum ShareExtensionError: LocalizedError {
    case noSupportedItems
    case fileTooLarge

    var errorDescription: String? {
        switch self {
        case .noSupportedItems: "No supported files were shared."
        case .fileTooLarge: "Each shared file must be 8 MB or smaller."
        }
    }
}
