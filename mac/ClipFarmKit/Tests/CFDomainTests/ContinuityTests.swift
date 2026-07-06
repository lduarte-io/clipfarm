import CFTestSupport
import Testing
@testable import CFDomain

/// Port of `tests/test_continuity.py` (9 tests). `ValueError` variants
/// become typed `ContinuityError` cases.

private func ac(_ clipID: String) -> AttemptClip {
    AttemptClip(clipID: clipID)
}

@Test func singleClipAttemptScoreIsOne() throws {
    // A single clip is its own run covering 100% of the attempt.
    let state = Fixtures.stateWithClips([("c0", "1", 0.0, 10.0)])
    #expect(try continuityScore(of: [ac("c0")], in: state) == 1.0)
}

@Test func twoConsecutiveSameSourceClipsScoreIsOne() throws {
    // Two clips from the same source progressing forward = one run.
    let state = Fixtures.stateWithClips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "1", 5.0, 10.0),
    ])
    #expect(try continuityScore(of: [ac("c0"), ac("c1")], in: state) == 1.0)
}

@Test func twoClipsDifferentSourcesMaxOverTotal() throws {
    // Different sources → two runs of 5s each → 5 / 10 = 0.5.
    let state = Fixtures.stateWithClips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "2", 0.0, 5.0),
    ])
    #expect(try continuityScore(of: [ac("c0"), ac("c1")], in: state) == 0.5)
}

@Test func backwardJumpInSourceBreaksRun() throws {
    // Same source but the second clip starts before the first ends →
    // run break. max 10 / total 15.
    let state = Fixtures.stateWithClips([
        ("c0", "1", 10.0, 20.0),
        ("c1", "1", 0.0, 5.0),
    ])
    let score = try continuityScore(of: [ac("c0"), ac("c1")], in: state)
    #expect(abs(score - (10.0 / 15.0)) < 1e-9)
}

@Test func threeClipsTwoInOneRunOneSeparate() throws {
    // c0+c1 contiguous in source 1 (10s); c2 in source 2 (4s) → 10/14.
    let state = Fixtures.stateWithClips([
        ("c0", "1", 0.0, 5.0),
        ("c1", "1", 5.0, 10.0),
        ("c2", "2", 0.0, 4.0),
    ])
    let score = try continuityScore(of: [ac("c0"), ac("c1"), ac("c2")], in: state)
    #expect(abs(score - (10.0 / 14.0)) < 1e-9)
}

@Test func emptyAttemptClipsThrows() {
    let state = Fixtures.stateWithClips([])
    #expect(throws: ContinuityError.emptyAttempt) {
        try continuityScore(of: [], in: state)
    }
}

@Test func zeroRuntimeAttemptThrows() {
    // Every clip has start == end — no meaningful value.
    let state = Fixtures.stateWithClips([("c0", "1", 5.0, 5.0)])
    #expect(throws: ContinuityError.zeroTotalRuntime) {
        try continuityScore(of: [ac("c0")], in: state)
    }
}

@Test func orphanClipIDsHandled() throws {
    let state = Fixtures.stateWithClips([("c0", "1", 0.0, 5.0)])
    // All orphans → throws.
    #expect(throws: ContinuityError.allClipsMissing) {
        try continuityScore(of: [ac("missing1"), ac("missing2")], in: state)
    }
    // Mixed: the orphan breaks the run AND contributes 0s → 5/5 = 1.0.
    #expect(try continuityScore(of: [ac("c0"), ac("missing")], in: state) == 1.0)
}

@Test func trimOffsetsShrinkRuntime() throws {
    // Trim c0 from 10s down to 4s; c1 stays 10s → 10 / 14.
    let state = Fixtures.stateWithClips([
        ("c0", "1", 0.0, 10.0),
        ("c1", "2", 0.0, 10.0),
    ])
    let trimmed = AttemptClip(clipID: "c0", trimStartOffset: 3.0, trimEndOffset: 3.0)
    let score = try continuityScore(of: [trimmed, ac("c1")], in: state)
    #expect(abs(score - (10.0 / 14.0)) < 1e-9)
}
