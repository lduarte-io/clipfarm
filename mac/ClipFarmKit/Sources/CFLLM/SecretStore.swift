import Foundation
import Security

/// Secrets live in the macOS Keychain (D23) — never in UserDefaults, never
/// in the library database, never in a file. The protocol seam exists so
/// tests exercise the contract without touching the login keychain
/// (`InMemorySecretStore`); the live Keychain path is verified at N7 with
/// the Settings page.
public enum SecretKey: String, Sendable, CaseIterable {
    case anthropicAPIKey = "anthropic-api-key"
}

public protocol SecretStore {
    /// nil when no secret is stored for `key`.
    func secret(for key: SecretKey) throws -> String?
    func setSecret(_ value: String, for key: SecretKey) throws
    /// Removing an absent secret is a no-op, not an error.
    func removeSecret(for key: SecretKey) throws
}

public enum SecretStoreError: Error, Equatable {
    case keychainFailure(status: OSStatus)
}

/// Generic-password Keychain items under the app's service name.
public final class KeychainSecretStore: SecretStore {
    public static let service = "org.duartes.clipfarm"

    public init() {}

    public func secret(for key: SecretKey) throws -> String? {
        var query = baseQuery(for: key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            guard let data = result as? Data else { return nil }
            return String(decoding: data, as: UTF8.self)
        case errSecItemNotFound:
            return nil
        default:
            throw SecretStoreError.keychainFailure(status: status)
        }
    }

    public func setSecret(_ value: String, for key: SecretKey) throws {
        let data = Data(value.utf8)
        let query = baseQuery(for: key)
        let update: [String: Any] = [kSecValueData as String: data]

        let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        switch updateStatus {
        case errSecSuccess:
            return
        case errSecItemNotFound:
            var add = query
            add[kSecValueData as String] = data
            let addStatus = SecItemAdd(add as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw SecretStoreError.keychainFailure(status: addStatus)
            }
        default:
            throw SecretStoreError.keychainFailure(status: updateStatus)
        }
    }

    public func removeSecret(for key: SecretKey) throws {
        let status = SecItemDelete(baseQuery(for: key) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw SecretStoreError.keychainFailure(status: status)
        }
    }

    private func baseQuery(for key: SecretKey) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: key.rawValue,
        ]
    }
}

/// Test double — dictionary-backed, same contract.
public final class InMemorySecretStore: SecretStore {
    private var storage: [SecretKey: String] = [:]

    public init() {}

    public func secret(for key: SecretKey) throws -> String? {
        storage[key]
    }

    public func setSecret(_ value: String, for key: SecretKey) throws {
        storage[key] = value
    }

    public func removeSecret(for key: SecretKey) throws {
        storage.removeValue(forKey: key)
    }
}
