import Foundation
import GRDB

/// Per-library settings — stored in the library database's `settings`
/// key/value table so they travel with the library (plan §2.3 / finding 12).
/// App-level prefs live in `UserDefaults` (CFLLM's `TaggingPreferences`);
/// the API key lives in the Keychain (D23) — neither ever lands here.
///
/// Missing keys read as defaults; unknown keys in the table are ignored
/// (tolerance); unparseable values fall back to defaults rather than fail.

/// D18: how a clip's `end_sec` extends past its last word.
public enum SegmentationTailPolicy: String, Sendable, CaseIterable, Codable {
    /// Default: each clip's end extends to the next word's start; the last
    /// clip extends to the source duration.
    case extendToNextWordStart = "extend-to-next-word-start"
    /// Fixed padding of `tailPaddingSec` beyond the last word's end.
    case fixedPadding = "fixed-padding"
    /// The raw Whisper `word.end` (the web implementation's behavior —
    /// clips routinely felt cut short; kept for golden-master comparison).
    case wordEnd = "word-end"
}

public struct LibrarySettings: Equatable, Sendable {
    /// Silence gap that opens a new clip (segmentation splits when gap
    /// `>=` this — the load-bearing comparison, tested by name at N3).
    public var silenceThresholdSec: Double
    public var tailPolicy: SegmentationTailPolicy
    /// Padding used by `.fixedPadding`. Default 0.25s (Lillian,
    /// 2026-07-06 — resolved the N1 PROVISIONAL); N3's segmentation UI can
    /// revisit once results are audible.
    public var tailPaddingSec: Double
    /// D31 "smooth cut audio": ~10ms audio micro-fades at cut boundaries,
    /// default ON. WYSIWYG rule — the SAME setting governs preview (N2
    /// CompositionBuilder audio mix) and export (N12 hybrid writer);
    /// preview and file always match.
    public var smoothCutAudio: Bool

    public init(
        silenceThresholdSec: Double = 2.0,
        tailPolicy: SegmentationTailPolicy = .extendToNextWordStart,
        tailPaddingSec: Double = 0.25,
        smoothCutAudio: Bool = true
    ) {
        self.silenceThresholdSec = silenceThresholdSec
        self.tailPolicy = tailPolicy
        self.tailPaddingSec = tailPaddingSec
        self.smoothCutAudio = smoothCutAudio
    }

    enum Keys {
        static let silenceThresholdSec = "segmentation.silence_threshold_sec"
        static let tailPolicy = "segmentation.tail_policy"
        static let tailPaddingSec = "segmentation.tail_padding_sec"
        static let smoothCutAudio = "playback.smooth_cut_audio"
    }
}

extension LibraryStore {
    public func librarySettings() throws -> LibrarySettings {
        try dbPool.read { db in
            var settings = LibrarySettings()
            let rows = try SettingRecord.fetchAll(db)
            let values = Dictionary(uniqueKeysWithValues: rows.map { ($0.key, $0.value) })
            if let raw = values[LibrarySettings.Keys.silenceThresholdSec],
               let parsed = Double(raw) {
                settings.silenceThresholdSec = parsed
            }
            if let raw = values[LibrarySettings.Keys.tailPolicy],
               let parsed = SegmentationTailPolicy(rawValue: raw) {
                settings.tailPolicy = parsed
            }
            if let raw = values[LibrarySettings.Keys.tailPaddingSec],
               let parsed = Double(raw) {
                settings.tailPaddingSec = parsed
            }
            if let raw = values[LibrarySettings.Keys.smoothCutAudio],
               let parsed = Bool(raw) {
                settings.smoothCutAudio = parsed
            }
            return settings
        }
    }

    /// Settings writes are deliberately NOT undo-registered — configuration
    /// changes don't sit on the document undo stack (platform convention;
    /// undo coverage is scoped to library-content mutations — resolved by
    /// Lillian 2026-07-06, see QUESTIONS.md → Answered).
    public func updateLibrarySettings(_ settings: LibrarySettings) throws {
        try dbPool.write { db in
            try SettingRecord(
                key: LibrarySettings.Keys.silenceThresholdSec,
                value: String(settings.silenceThresholdSec)
            ).save(db)
            try SettingRecord(
                key: LibrarySettings.Keys.tailPolicy,
                value: settings.tailPolicy.rawValue
            ).save(db)
            try SettingRecord(
                key: LibrarySettings.Keys.tailPaddingSec,
                value: String(settings.tailPaddingSec)
            ).save(db)
            try SettingRecord(
                key: LibrarySettings.Keys.smoothCutAudio,
                value: String(settings.smoothCutAudio)
            ).save(db)
        }
    }
}
