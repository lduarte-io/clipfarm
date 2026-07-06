import CFTestSupport
import Testing
@testable import CFDomain

/// Port of `tests/test_continuity_refresh.py` (5 tests) — the
/// recompute-after-every-clip-list-mutation helper. Degenerate cases set
/// nil instead of throwing.

private func attempt(_ clipIDs: [String], initialScore: Double?) -> Attempt {
    Fixtures.attempt(
        continuityScore: initialScore,
        clips: clipIDs.map { AttemptClip(clipID: $0) }
    )
}

@Test func emptyClipsSetsScoreToNil() {
    // Freshly-created hand-built draft, or user dragged all clips out.
    let state = Fixtures.stateWithClips([])
    var a = attempt([], initialScore: 0.9)
    refreshContinuityScore(of: &a, in: state)
    #expect(a.continuityScore == nil)
}

@Test func singleClipYieldsScoreOne() {
    let state = Fixtures.stateWithClips([("c0", "s1", 0.0, 5.0)])
    var a = attempt(["c0"], initialScore: 0.5)
    refreshContinuityScore(of: &a, in: state)
    #expect(a.continuityScore == 1.0)
}

@Test func allOrphanClipsFallsBackToNil() {
    // Every referenced clip missing → the underlying compute throws
    // allClipsMissing; the helper catches and sets nil.
    let state = Fixtures.stateWithClips([])
    var a = attempt(["c_missing", "c_also_missing"], initialScore: 0.9)
    refreshContinuityScore(of: &a, in: state)
    #expect(a.continuityScore == nil)
}

@Test func mixedLiveAndOrphanClipsStillComputes() {
    // One live (10s) + one orphan (0s run break) → 10/10 = 1.0.
    let state = Fixtures.stateWithClips([("c0", "s1", 0.0, 10.0)])
    var a = attempt(["c0", "c_orphan"], initialScore: nil)
    refreshContinuityScore(of: &a, in: state)
    #expect(a.continuityScore == 1.0)
}

@Test func zeroRuntimeClipYieldsNil() {
    // start == end → zeroTotalRuntime underneath → nil.
    let state = Fixtures.stateWithClips([("c0", "s1", 5.0, 5.0)])
    var a = attempt(["c0"], initialScore: 0.7)
    refreshContinuityScore(of: &a, in: state)
    #expect(a.continuityScore == nil)
}
