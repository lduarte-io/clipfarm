import CFTestSupport
import Testing
@testable import CFDomain

/// Port of `tests/test_resolver.py` (14 tests). Divergences from the
/// reference, both recorded in the N1 phase entry:
/// - transcripts arrive via the injected `transcriptProvider` instead of
///   disk sidecars (CFDomain purity);
/// - `KeyError`/`ValueError` become typed thrown errors, and log-warning
///   assertions become `onWarning` captures.

private func noTranscript(_: Source) -> WhisperTranscript? { nil }

private func ranges(_ items: [ResolvedItem]) -> [ResolvedRange] {
    items.compactMap {
        if case .range(let r) = $0 { r } else { nil }
    }
}

// MARK: - Happy path + trim

@Test func singleClipNoTrimEmitsOneRange() throws {
    let state = Fixtures.state(
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0")]
    )
    let items = try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)
    #expect(items.count == 1)
    let r = try #require(ranges(items).first)
    #expect(r.clipID == "c0")
    #expect(r.sourceID == "s1")
    #expect(r.effectiveStartSec == 5.0)
    #expect(r.effectiveEndSec == 15.0)
}

@Test func trimStartOffsetAdvancesEffectiveStart() throws {
    // Positive trimStartOffset moves the start FORWARD.
    let state = Fixtures.state(
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimStartOffset: 2.0)]
    )
    let r = try #require(ranges(try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)).first)
    #expect(r.effectiveStartSec == 7.0)
    #expect(r.effectiveEndSec == 15.0)
}

@Test func trimEndOffsetRetractsEffectiveEnd() throws {
    // Positive trimEndOffset moves the end BACKWARD.
    let state = Fixtures.state(
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimEndOffset: 3.0)]
    )
    let r = try #require(ranges(try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)).first)
    #expect(r.effectiveEndSec == 12.0)
}

// MARK: - Source-bounds clamping

@Test func negativeEffectiveStartClampedToZero() throws {
    // trimStartOffset = -10 → raw start -5 → clamped to 0.
    let state = Fixtures.state(
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimStartOffset: -10.0)]
    )
    let r = try #require(ranges(try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)).first)
    #expect(r.effectiveStartSec == 0.0)
}

@Test func effectiveEndPastSourceDurationClampedWithWarning() throws {
    // trimEndOffset = -10 → raw end 25 → clamped to source duration 20.
    let state = Fixtures.state(
        sources: [("s1", 20.0)],
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimEndOffset: -10.0)]
    )
    var warnings: [ResolverWarning] = []
    let items = try resolveAttempt(
        "a1", in: state, transcriptProvider: noTranscript, onWarning: { warnings.append($0) }
    )
    let r = try #require(ranges(items).first)
    #expect(r.effectiveEndSec == 20.0)
    #expect(warnings.contains(.endClamped(clipID: "c0", rawEndSec: 25.0, clampedEndSec: 20.0)))
}

@Test func unknownSourceDurationTreatedAsInfinity() throws {
    // durationSec nil (probe failed) → no end clamp fires.
    let state = Fixtures.state(
        sources: [("s1", nil)],
        clips: [("c0", "s1", 5.0, 15.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimEndOffset: -100.0)]
    )
    let r = try #require(ranges(try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)).first)
    #expect(r.effectiveEndSec == 115.0)
}

@Test func zeroDurationAfterClampThrows() throws {
    // 1-second clip, trim 2s from start → effectiveStart (7) > end (6).
    let state = Fixtures.state(
        clips: [("c0", "s1", 5.0, 6.0)],
        attemptClips: [AttemptClip(clipID: "c0", trimStartOffset: 2.0)]
    )
    #expect(throws: ResolverError.nonPositiveEffectiveDuration(
        clipID: "c0", effectiveStartSec: 7.0, effectiveEndSec: 6.0
    )) {
        try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)
    }
}

// MARK: - Tombstone (dangling clip)

@Test func danglingClipEmitsTombstone() throws {
    let state = Fixtures.state(
        clips: [("c0", "s1", 0, 5)],
        attemptClips: [
            AttemptClip(clipID: "c0"),
            AttemptClip(clipID: "c_deleted"),  // missing from state.clips
            AttemptClip(clipID: "c0"),
        ]
    )
    let items = try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)
    #expect(items.count == 3)
    guard case .range = items[0], case .tombstone(let t) = items[1], case .range = items[2] else {
        Issue.record("expected [range, tombstone, range], got \(items)")
        return
    }
    #expect(t.clipID == "c_deleted")
}

// MARK: - internalPauseMaxSec gap-drop expansion

/// Port of `_state_with_transcript`: one source whose transcript holds the
/// given words; one clip spanning them; one attempt with max pause 0.5s.
private func stateAndTranscript(
    words: [(start: Double, end: Double, word: String)]
) -> (ClipFarmState, WhisperTranscript) {
    let transcript = Fixtures.transcript(words: words)
    let state = Fixtures.state(
        sources: [("s1", (words.last?.end ?? 0) + 1.0)],
        clips: [("c0", "s1", words.first?.start ?? 0, words.last?.end ?? 0)],
        attemptClips: [AttemptClip(clipID: "c0", internalPauseMaxSec: 0.5)]
    )
    return (state, transcript)
}

@Test func internalPauseNoGapsReturnsSingleRange() throws {
    let (state, transcript) = stateAndTranscript(words: [
        (0.0, 0.5, " hello"),
        (0.6, 1.0, " world"),
        (1.1, 1.5, " again"),
    ])
    let items = try resolveAttempt("a1", in: state, transcriptProvider: { _ in transcript })
    #expect(items.count == 1)
    #expect(ranges(items).count == 1)
}

@Test func internalPauseOneGapOverMaxSplitsInTwo() throws {
    let (state, transcript) = stateAndTranscript(words: [
        (0.0, 0.5, " hello"),
        (0.6, 1.0, " world"),
        (3.0, 3.5, " again"),  // 2-second gap before "again"
        (3.6, 4.0, " more"),
    ])
    let items = try resolveAttempt("a1", in: state, transcriptProvider: { _ in transcript })
    let rs = ranges(items)
    #expect(rs.count == 2)
    // First sub-range ends at the previous word's end; the gap is GONE.
    #expect(abs(rs[0].effectiveEndSec - 1.0) < 1e-6)
    #expect(abs(rs[1].effectiveStartSec - 3.0) < 1e-6)
}

@Test func internalPauseGapExactlyAtMaxDoesNotSplit() throws {
    // Gap of EXACTLY 0.5 with max 0.5 → no split (strict `>` — the
    // load-bearing comparison, tested by name).
    let (state, transcript) = stateAndTranscript(words: [
        (0.0, 0.5, " hello"),
        (1.0, 1.5, " world"),
    ])
    let items = try resolveAttempt("a1", in: state, transcriptProvider: { _ in transcript })
    #expect(items.count == 1)
}

@Test func internalPauseWithMissingTranscriptFallsBack() throws {
    // Footage-only source (provider returns nil) → single un-expanded
    // range + a transcriptUnavailable warning.
    let state = Fixtures.state(
        sources: [("s1", 20.0)],
        clips: [("c0", "s1", 0, 10)],
        attemptClips: [AttemptClip(clipID: "c0", internalPauseMaxSec: 0.5)]
    )
    var warnings: [ResolverWarning] = []
    let items = try resolveAttempt(
        "a1", in: state, transcriptProvider: noTranscript, onWarning: { warnings.append($0) }
    )
    #expect(items.count == 1)
    #expect(ranges(items).count == 1)
    #expect(warnings.contains(.transcriptUnavailable(clipID: "c0", sourceID: "s1")))
}

// MARK: - Multi-clip ordering + unknown attempt

@Test func multiClipAttemptPreservesOrder() throws {
    let state = Fixtures.state(
        clips: [
            ("c0", "s1", 0, 5),
            ("c1", "s1", 10, 15),
            ("c2", "s1", 20, 25),
        ],
        attemptClips: [
            AttemptClip(clipID: "c2"),
            AttemptClip(clipID: "c0"),
            AttemptClip(clipID: "c1"),
        ]
    )
    let items = try resolveAttempt("a1", in: state, transcriptProvider: noTranscript)
    #expect(items.map(\.clipID) == ["c2", "c0", "c1"])
}

@Test func unknownAttemptThrows() {
    let state = Fixtures.state(attemptClips: [])
    #expect(throws: ResolverError.unknownAttempt(attemptID: "missing")) {
        try resolveAttempt("missing", in: state, transcriptProvider: noTranscript)
    }
}
