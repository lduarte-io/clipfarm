import CFDomain
import CFTestSupport
import Foundation
import GRDB
import Testing
@testable import CFStore

/// Disk half of the ported `test_models_round_trip.py` + `test_store.py`
/// round-trip: domain state → SQLite rows → domain state, exact.

@MainActor @Test func importThenFetchRoundTripsExactly() throws {
    try withScratchStore { store in
        let state = makeState(clipCount: 3)
        try store.importState(state)
        #expect(try store.fetchState() == state)
    }
}

@MainActor @Test func fullyPopulatedStateRoundTripsExactly() throws {
    // One of everything, every optional populated (except `tracks` — the
    // writer invariant keeps it nil until N18).
    try withScratchStore { store in
        let state = Fixtures.fullState()
        try store.importState(state)
        #expect(try store.fetchState() == state)
    }
}

@MainActor @Test func emptyLibraryFetchesEmptyStateAtCurrentVersion() throws {
    try withScratchStore { store in
        let state = try store.fetchState()
        #expect(state == ClipFarmState(version: 1))
    }
}

@MainActor @Test func attemptOptionalFieldsRoundTripWithoutCoercion() throws {
    // Port of the parametrized reference test: nil/nil/nil, best/0.92/nil,
    // diagnostic/0.3/0.5 — no silent defaulting.
    let cases: [(PremadeBucket?, Double?, Double?)] = [
        (nil, nil, nil),
        (.best, 0.92, nil),
        (.diagnostic, 0.3, 0.5),
    ]
    for (bucket, score, pause) in cases {
        try withScratchStore { store in
            var state = ClipFarmState()
            state.attempts["1"] = Attempt(
                projectID: "p1",
                name: "t",
                premadeBucket: bucket,
                continuityScore: score,
                clips: [AttemptClip(clipID: "cid", internalPauseMaxSec: pause)],
                createdAt: Fixtures.timestamp
            )
            try store.importState(state)
            let attempt = try #require(try store.fetchState().attempts["1"])
            #expect(attempt.premadeBucket == bucket)
            #expect(attempt.continuityScore == score)
            #expect(attempt.clips.first?.internalPauseMaxSec == pause)
        }
    }
}

@MainActor @Test func clipTracksPersistsAsSQLNull() throws {
    // The on-disk contract: `tracks` is a literal NULL column value, not
    // '{}' and not absent.
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 1))
        let isNull = try store.dbPool.read { db in
            try Bool.fetchOne(db, sql: "SELECT tracks IS NULL FROM clips")
        }
        #expect(isNull == true)
    }
}

@MainActor @Test func scriptOptionalityIsPreservedDistinctly() throws {
    // script == nil (NULL column) vs Script(lines: []) ('[]') are
    // different states and must round-trip differently.
    try withScratchStore { store in
        var state = ClipFarmState()
        state.projects["1"] = Project(name: "scriptless", script: nil, createdAt: Fixtures.timestamp)
        state.projects["2"] = Project(name: "empty-script", script: Script(lines: []), createdAt: Fixtures.timestamp)
        state.projects["3"] = Project(name: "scripted", script: Script(lines: ["a", "b"]), createdAt: Fixtures.timestamp)
        try store.importState(state)
        let fetched = try store.fetchState()
        #expect(fetched.projects["1"]?.script == nil)
        #expect(fetched.projects["2"]?.script == Script(lines: []))
        #expect(fetched.projects["3"]?.script == Script(lines: ["a", "b"]))
    }
}

@MainActor @Test func attemptClipOrderSurvivesRoundTrip() throws {
    try withScratchStore { store in
        var state = makeState(clipCount: 3)
        let ids = state.clips.keys.sorted()
        state.attempts["1"] = Fixtures.attempt(
            clips: [ids[2], ids[0], ids[1]].map { AttemptClip(clipID: $0) }
        )
        try store.importState(state)
        let fetched = try #require(try store.fetchState().attempts["1"])
        #expect(fetched.clips.map(\.clipID) == [ids[2], ids[0], ids[1]])
    }
}

@MainActor @Test func importReplacesExistingContentWholesale() throws {
    try withScratchStore { store in
        try store.importState(makeState(clipCount: 5))
        let replacement = makeState(clipCount: 2)
        try store.importState(replacement)
        #expect(try store.fetchState() == replacement)
    }
}
