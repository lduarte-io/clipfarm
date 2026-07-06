import CFDomain
import CFTestSupport
import Foundation
import Testing
@testable import CFStore

/// Register→undo→redo per store mutation (mac/CLAUDE.md testing rule),
/// driving a Foundation `UndoManager` directly against store methods —
/// asserting domain state and the DB round-trip in both directions.

@MainActor @Test func addSourceRegistersUndoAndRedo() throws {
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        let id = try store.addSource(Fixtures.source(filename: "a.mov"))
        #expect(id == "1")
        #expect(try store.source(id: "1")?.filename == "a.mov")
        #expect(undoManager.canUndo)

        undoManager.undo()
        #expect(try store.source(id: "1") == nil)
        #expect(undoManager.canRedo)

        undoManager.redo()
        #expect(try store.source(id: "1")?.filename == "a.mov")
        // The chain keeps flipping: undo works again after redo.
        undoManager.undo()
        #expect(try store.source(id: "1") == nil)
    }
}

@MainActor @Test func addSourceSetsANamedUndoAction() throws {
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        try store.addSource(Fixtures.source())
        #expect(undoManager.undoActionName == "Add Source")
    }
}

@MainActor @Test func addClipsUndoesAndRedoesAsOneAction() throws {
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        try store.addSource(Fixtures.source(), id: "s1")
        // Fresh group so the clips undo separately from the source
        // (UndoManager groups by run-loop turn, and tests have none).
        undoManager.removeAllActions()

        try store.addClips([
            (id: "c1", clip: Fixtures.clip(sourceID: "s1", startSec: 0, endSec: 1)),
            (id: "c2", clip: Fixtures.clip(sourceID: "s1", startSec: 1, endSec: 2)),
        ])
        #expect(try store.fetchState().clips.count == 2)
        #expect(undoManager.undoActionName == "Add Clips")

        undoManager.undo()
        #expect(try store.fetchState().clips.isEmpty)

        undoManager.redo()
        let clips = try store.fetchState().clips
        #expect(clips.count == 2)
        #expect(clips["c1"]?.startSec == 0)
    }
}

@MainActor @Test func addClipsRejectsDuplicateIDs() throws {
    // Clip IDs are allocated once at creation; a colliding insert is a
    // programmer error upstream and must not half-land.
    try withScratchStore { store in
        try store.addSource(Fixtures.source(), id: "s1")
        try store.addClips([(id: "c1", clip: Fixtures.clip(sourceID: "s1", startSec: 0, endSec: 1))])
        #expect(throws: LibraryStoreError.duplicateClipID("c1")) {
            try store.addClips([
                (id: "c9", clip: Fixtures.clip(sourceID: "s1", startSec: 5, endSec: 6)),
                (id: "c1", clip: Fixtures.clip(sourceID: "s1", startSec: 2, endSec: 3)),
            ])
        }
        // The failed bulk insert rolled back entirely — c9 is absent too.
        #expect(try store.fetchState().clips.count == 1)
    }
}

@MainActor @Test func addClipProjectTagRegistersUndoAndRedo() throws {
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        try store.importState(makeState(clipCount: 1))
        let clipID = try #require(try store.fetchState().clips.keys.first)
        let tag = ClipProjectTag(clipID: clipID, projectID: "p2", projectTagID: "t1", category: .onScript)

        try store.addClipProjectTag(tag)
        #expect(try store.fetchState().clipProjectTags.contains(tag))
        #expect(undoManager.undoActionName == "Tag Clip")

        undoManager.undo()
        #expect(!(try store.fetchState().clipProjectTags.contains(tag)))
        // The pre-existing tag row from the fixture is untouched.
        #expect(try store.fetchState().clipProjectTags.count == 1)

        undoManager.redo()
        #expect(try store.fetchState().clipProjectTags.contains(tag))
    }
}

@MainActor @Test func sourceIDAllocationIsMonotonicOverAllKeys() throws {
    // Explicit `throws`: the closure's only visible `try`s sit inside
    // #expect macros, which throws-inference can't see.
    try withScratchStore { (store) throws in
        #expect(try store.addSource(Fixtures.source()) == "1")
        #expect(try store.addSource(Fixtures.source()) == "2")
        #expect(try store.addSource(Fixtures.source(), id: "7") == "7")
        // max+1 over ALL existing keys — jumps past the explicit "7".
        #expect(try store.addSource(Fixtures.source()) == "8")
    }
}

@MainActor @Test func importStateClearsTheUndoStack() throws {
    // A whole-library replace invalidates every registered inverse —
    // stale undo closures must never fire against replaced state.
    let undoManager = UndoManager()
    try withScratchStore(undoManager: undoManager) { store in
        try store.addSource(Fixtures.source())
        #expect(undoManager.canUndo)
        try store.importState(makeState(clipCount: 1))
        #expect(!undoManager.canUndo)
        #expect(!undoManager.canRedo)
    }
}
