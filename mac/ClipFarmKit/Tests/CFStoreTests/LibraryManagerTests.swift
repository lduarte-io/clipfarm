import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// The close→swap→reopen path: undo stack cleared on every transition,
/// stores isolated per library, data intact on return, change hook fired —
/// including on a FAILED open (the outgoing store is already dead by then).

@MainActor @Test func swapClearsTheUndoStackAndIsolatesLibraries() throws {
    let folderA = try makeScratchFolder()
    let folderB = try makeScratchFolder()
    defer {
        try? FileManager.default.removeItem(at: folderA)
        try? FileManager.default.removeItem(at: folderB)
    }
    let undoManager = UndoManager()
    let manager = LibraryManager(undoManager: undoManager)

    let storeA = try manager.open(at: folderA)
    try storeA.addSource(Fixtures.source(filename: "a.mov"))
    #expect(undoManager.canUndo)

    let storeB = try manager.swap(to: folderB)
    #expect(!undoManager.canUndo, "swap must clear the undo stack")
    #expect(!undoManager.canRedo)
    #expect(try storeB.fetchState().sources.isEmpty, "libraries are isolated")

    try manager.close()
}

@MainActor @Test func swappingBackReopensTheOriginalData() throws {
    let folderA = try makeScratchFolder()
    let folderB = try makeScratchFolder()
    defer {
        try? FileManager.default.removeItem(at: folderA)
        try? FileManager.default.removeItem(at: folderB)
    }
    let manager = LibraryManager(undoManager: UndoManager())

    let storeA = try manager.open(at: folderA)
    try storeA.addSource(Fixtures.source(filename: "kept.mov"), id: "1")
    try manager.swap(to: folderB)
    let reopenedA = try manager.swap(to: folderA)
    #expect(try reopenedA.fetchState().sources["1"]?.filename == "kept.mov")
    try manager.close()
}

@MainActor @Test func closeClearsUndoAndNilsTheStore() throws {
    let folder = try makeScratchFolder()
    defer { try? FileManager.default.removeItem(at: folder) }
    let undoManager = UndoManager()
    let manager = LibraryManager(undoManager: undoManager)

    let store = try manager.open(at: folder)
    try store.addSource(Fixtures.source())
    try manager.close()
    #expect(manager.store == nil)
    #expect(!undoManager.canUndo)
}

@MainActor @Test func failedOpenFiresStoreDidChangeNilAndClearsState() throws {
    // By the time open(at:) can fail, the previous store is closed and the
    // undo stack cleared — observers MUST hear about the dead store so
    // ValueObservations tear down (cold-review finding 4).
    let goodFolder = try makeScratchFolder()
    let supersededFolder = try makeScratchFolder()
    defer {
        try? FileManager.default.removeItem(at: goodFolder)
        try? FileManager.default.removeItem(at: supersededFolder)
    }
    // Make the second folder refuse to open (written by a "newer app").
    let seed = try LibraryStore.open(at: supersededFolder)
    try seed.dbPool.write { db in
        try db.execute(sql: "INSERT INTO grdb_migrations (identifier) VALUES ('v99')")
    }
    try seed.close()

    let undoManager = UndoManager()
    let manager = LibraryManager(undoManager: undoManager)
    var events: [Bool] = []  // true = a store, false = nil
    manager.storeDidChange = { events.append($0 != nil) }

    let goodStore = try manager.open(at: goodFolder)
    try goodStore.addSource(Fixtures.source())
    #expect(throws: LibraryStoreError.self) {
        try manager.open(at: supersededFolder)
    }
    #expect(manager.store == nil)
    #expect(!undoManager.canUndo)
    #expect(events == [true, false])
}

@MainActor @Test func storeDidChangeFiresOnEveryTransition() throws {
    let folderA = try makeScratchFolder()
    let folderB = try makeScratchFolder()
    defer {
        try? FileManager.default.removeItem(at: folderA)
        try? FileManager.default.removeItem(at: folderB)
    }
    let manager = LibraryManager()
    var events: [Bool] = []  // true = a store, false = nil
    manager.storeDidChange = { events.append($0 != nil) }

    try manager.open(at: folderA)
    try manager.swap(to: folderB)
    try manager.close()
    #expect(events == [true, true, false])
}
