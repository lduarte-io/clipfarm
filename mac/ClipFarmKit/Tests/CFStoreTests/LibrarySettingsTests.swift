import Foundation
import GRDB
import Testing
@testable import CFStore

/// Per-library settings over the `settings` table — the surviving slice of
/// the ported `test_settings.py` (the provider/model/API-key slices moved
/// to CFLLMTests per D22/D23; the file-based atomicity/chmod tests died
/// with the file).

@Test func defaultsWhenNoSettingsRowsExist() throws {
    try withScratchStoreNonisolated { store in
        let settings = try store.librarySettings()
        #expect(settings == LibrarySettings())
        #expect(settings.silenceThresholdSec == 2.0)
        #expect(settings.tailPolicy == .extendToNextWordStart)
        #expect(settings.tailPaddingSec == 0.25)  // Lillian, 2026-07-06
    }
}

@Test func settingsRoundTrip() throws {
    try withScratchStoreNonisolated { store in
        var settings = LibrarySettings()
        settings.silenceThresholdSec = 1.5
        settings.tailPolicy = .fixedPadding
        settings.tailPaddingSec = 0.4  // non-default, so the round-trip is meaningful
        try store.updateLibrarySettings(settings)
        #expect(try store.librarySettings() == settings)
    }
}

@Test func settingsPersistAcrossReopen() throws {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder)
    var settings = LibrarySettings()
    settings.silenceThresholdSec = 3.0
    settings.tailPolicy = .wordEnd
    try store.updateLibrarySettings(settings)
    try store.close()

    let reopened = try LibraryStore.open(at: folder)
    defer { try? reopened.close() }
    #expect(try reopened.librarySettings() == settings)
}

@Test func unparseableValuesFallBackToDefaults() throws {
    try withScratchStoreNonisolated { store in
        try store.dbPool.write { db in
            try db.execute(sql: """
                INSERT INTO settings(key, value) VALUES
                    ('segmentation.silence_threshold_sec', 'not-a-number'),
                    ('segmentation.tail_policy', 'no-such-policy')
                """)
        }
        let settings = try store.librarySettings()
        #expect(settings.silenceThresholdSec == 2.0)
        #expect(settings.tailPolicy == .extendToNextWordStart)
    }
}

@Test func unknownSettingsKeysAreIgnored() throws {
    try withScratchStoreNonisolated { store in
        try store.dbPool.write { db in
            try db.execute(
                sql: "INSERT INTO settings(key, value) VALUES ('future.someone_elses_key', 'x')"
            )
        }
        #expect(try store.librarySettings() == LibrarySettings())
    }
}
