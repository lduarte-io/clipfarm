import CFDomain
import CFTestSupport
import Foundation
import GRDB
@testable import CFStore

/// Shared helpers for the CFStore suite. Each test opens its own scratch
/// library in a unique temp folder (tests run in parallel).

func makeScratchFolder() throws -> URL {
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("clipfarm-kit-tests", isDirectory: true)
        .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

/// Opens a scratch library, runs `body`, closes, and removes the folder.
/// `@MainActor` because most store mutations are (UndoManager is
/// `NS_SWIFT_UI_ACTOR` in the macOS 26 SDK) — store tests run as
/// `@MainActor @Test` functions.
@MainActor
@discardableResult
func withScratchStore<T>(
    undoManager: UndoManager? = nil,
    now: @escaping @Sendable () -> Date = Date.init,
    _ body: @MainActor (LibraryStore) throws -> T
) throws -> T {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let store = try LibraryStore.open(at: folder, undoManager: undoManager, now: now)
    defer { try? store.close() }
    return try body(store)
}

/// Port of `test_store._make_state`: one (missing-path) source, N clips,
/// one bucket-category tag row per clip. Adaptation recorded in the phase
/// entry: `clip_project_tags.clip_id` IS an FK in the native schema, so the
/// referenced clips must exist — which they do here.
func makeState(clipCount: Int = 1) -> ClipFarmState {
    var state = ClipFarmState()
    state.sources["1"] = Fixtures.source(
        filename: "fake.mov",
        path: "/nonexistent/fake.mov",
        unavailable: true
    )
    for i in 0..<clipCount {
        let clipID = "fake__00-00-\(String(format: "%02d", i))__00-00-\(String(format: "%02d", i + 1))"
        state.clips[clipID] = Clip(
            sourceID: "1",
            startSec: Double(i),
            endSec: Double(i + 1),
            transcriptText: "line \(i)",
            createdAt: Fixtures.timestamp
        )
        state.clipProjectTags.append(ClipProjectTag(
            clipID: clipID,
            projectID: "p1",
            projectTagID: nil,
            category: .standaloneIdea,
            source: .user
        ))
    }
    return state
}
