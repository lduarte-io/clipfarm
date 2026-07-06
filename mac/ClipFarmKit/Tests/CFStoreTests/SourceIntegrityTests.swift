import CFDomain
import CFTestSupport
import Foundation
import Testing
@testable import CFStore

/// Port of `tests/test_source_integrity.py` (3 tests): missing source
/// files flip `unavailable` (never crash the load); reappearing files flip
/// it back.

@MainActor @Test func missingSourceFileFlipsUnavailable() throws {
    try withScratchStore { store in
        var state = ClipFarmState()
        state.sources["1"] = Fixtures.source(
            filename: "ghost.mov",
            path: "/definitely/not/here/ghost.mov",
            unavailable: false
        )
        try store.importState(state)
        try store.runSourceIntegrityCheck()
        #expect(try store.fetchState().sources["1"]?.unavailable == true)
    }
}

@MainActor @Test func existingSourceFileFlipsBackAvailable() throws {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let realMovie = folder.appendingPathComponent("real.mov")
    try Data([0x00, 0x00]).write(to: realMovie)

    let store = try LibraryStore.open(at: folder)
    defer { try? store.close() }
    var state = ClipFarmState()
    state.sources["1"] = Fixtures.source(
        filename: "real.mov",
        path: realMovie.path,
        unavailable: true  // pretend a previous run flagged it missing
    )
    try store.importState(state)
    try store.runSourceIntegrityCheck()
    #expect(try store.fetchState().sources["1"]?.unavailable == false)
}

@MainActor @Test func openRunsTheIntegrityCheck() throws {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }

    let store = try LibraryStore.open(at: folder)
    var state = ClipFarmState()
    state.sources["1"] = Fixtures.source(
        filename: "ghost.mov",
        path: folder.appendingPathComponent("ghost.mov").path,  // never written
        unavailable: false
    )
    try store.importState(state)
    try store.close()

    let reopened = try LibraryStore.open(at: folder)
    defer { try? reopened.close() }
    #expect(try reopened.fetchState().sources["1"]?.unavailable == true)
}
