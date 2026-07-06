import Foundation
import Testing
@testable import CFLLM

/// App-level LLM preferences (UserDefaults, D22) + the Keychain secret seam
/// (D23) — the surviving slices of the ported `test_settings.py`. The
/// on-disk-file tests (atomic write, chmod 0o600, plaintext-at-rest) died
/// with the settings file: the key lives in the Keychain now, which is the
/// point.

private func makeSuite() -> (UserDefaults, String) {
    let name = "clipfarm-tests-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: name)!
    return (defaults, name)
}

@Test func defaultsWhenNothingStored() {
    let (defaults, name) = makeSuite()
    defer { defaults.removePersistentDomain(forName: name) }
    let prefs = TaggingPreferences(defaults: defaults)
    #expect(prefs.provider == .ollama)
    #expect(prefs.ollamaModel == TaggingPreferences.defaultOllamaModel)
    #expect(prefs.anthropicModel == TaggingPreferences.defaultAnthropicModel)
    #expect(prefs.ollamaModel == "llama3.1:8b")
    #expect(prefs.anthropicModel == "claude-sonnet-4-6")
}

@Test func preferencesRoundTrip() {
    let (defaults, name) = makeSuite()
    defer { defaults.removePersistentDomain(forName: name) }
    let prefs = TaggingPreferences(defaults: defaults)
    prefs.provider = .anthropic
    prefs.anthropicModel = "claude-haiku-4-5-20251001"
    prefs.ollamaModel = "qwen2.5:14b"

    let reread = TaggingPreferences(defaults: defaults)
    #expect(reread.provider == .anthropic)
    #expect(reread.anthropicModel == "claude-haiku-4-5-20251001")
    #expect(reread.ollamaModel == "qwen2.5:14b")
}

@Test func garbageProviderValueFallsBackToOllama() {
    let (defaults, name) = makeSuite()
    defer { defaults.removePersistentDomain(forName: name) }
    defaults.set("chatgpt", forKey: "tagging.provider")
    #expect(TaggingPreferences(defaults: defaults).provider == .ollama)
}

@Test func inMemorySecretStoreContract() throws {
    let store = InMemorySecretStore()
    #expect(try store.secret(for: .anthropicAPIKey) == nil)

    try store.setSecret("sk-ant-test-key", for: .anthropicAPIKey)
    #expect(try store.secret(for: .anthropicAPIKey) == "sk-ant-test-key")

    // Overwrite replaces.
    try store.setSecret("sk-ant-rotated", for: .anthropicAPIKey)
    #expect(try store.secret(for: .anthropicAPIKey) == "sk-ant-rotated")

    try store.removeSecret(for: .anthropicAPIKey)
    #expect(try store.secret(for: .anthropicAPIKey) == nil)
    // Removing an absent secret is a no-op, not an error.
    try store.removeSecret(for: .anthropicAPIKey)
}

@Test func apiKeyNeverLandsInUserDefaults() throws {
    // D23: the key's only home is the Keychain. Exercise the preferences
    // API surface, then assert nothing key-shaped reached the suite.
    let (defaults, name) = makeSuite()
    defer { defaults.removePersistentDomain(forName: name) }
    let prefs = TaggingPreferences(defaults: defaults)
    prefs.provider = .anthropic
    prefs.anthropicModel = "claude-sonnet-4-6"

    let secrets = InMemorySecretStore()
    try secrets.setSecret("sk-ant-super-secret", for: .anthropicAPIKey)

    let stored = defaults.persistentDomain(forName: name) ?? [:]
    #expect(!stored.keys.contains { $0.lowercased().contains("key") })
    #expect(!stored.values.contains { ($0 as? String)?.contains("sk-ant") == true })
}

@Test func anthropicModelOptionsExposeTheKnownGoodSet() {
    #expect(TaggingPreferences.anthropicModelOptions.contains("claude-sonnet-4-6"))
    #expect(TaggingPreferences.anthropicModelOptions.contains("claude-opus-4-7"))
    #expect(TaggingPreferences.anthropicModelOptions.contains("claude-haiku-4-5-20251001"))
}
