import Foundation

/// App-level LLM preferences — `UserDefaults`-backed per D22 (per-library
/// settings live in the library DB; the API key lives in the Keychain, D23,
/// never here).
///
/// Provider choice never leaks past the CFLLM dispatcher (N7); these
/// preferences are the dispatcher's configuration input.
public enum TaggingProvider: String, Sendable, CaseIterable {
    case ollama
    case anthropic
}

public struct TaggingPreferences {
    public static let defaultOllamaModel = "llama3.1:8b"
    public static let defaultAnthropicModel = "claude-sonnet-4-6"
    /// Known-good models surfaced in the Settings UI; free-text entry stays
    /// allowed so new models need no code change. Canonical aliases
    /// throughout (the reference used the dated Haiku ID; the alias form is
    /// the recommended one and consistent with the other entries).
    public static let anthropicModelOptions = [
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5",
    ]

    enum Keys {
        static let provider = "tagging.provider"
        static let ollamaModel = "tagging.ollama_model"
        static let anthropicModel = "tagging.anthropic_model"
    }

    private let defaults: UserDefaults

    /// Inject a suite in tests; the app uses `.standard`.
    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// Unknown stored values fall back to `.ollama` (the zero-dependency
    /// default) rather than fail.
    public var provider: TaggingProvider {
        get {
            defaults.string(forKey: Keys.provider)
                .flatMap(TaggingProvider.init(rawValue:)) ?? .ollama
        }
        nonmutating set {
            defaults.set(newValue.rawValue, forKey: Keys.provider)
        }
    }

    public var ollamaModel: String {
        get { defaults.string(forKey: Keys.ollamaModel) ?? Self.defaultOllamaModel }
        nonmutating set { defaults.set(newValue, forKey: Keys.ollamaModel) }
    }

    public var anthropicModel: String {
        get { defaults.string(forKey: Keys.anthropicModel) ?? Self.defaultAnthropicModel }
        nonmutating set { defaults.set(newValue, forKey: Keys.anthropicModel) }
    }
}
