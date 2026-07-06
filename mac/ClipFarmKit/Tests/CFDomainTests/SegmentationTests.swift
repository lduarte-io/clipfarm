import Testing
@testable import CFDomain

/// Port of `tests/test_segmentation.py` (11 tests) + the D18 tail-policy
/// suite (native — spec amendment 7) + `transcript_text_for_range` port
/// tests.

private func w(_ start: Double, _ end: Double, _ text: String = " x") -> WhisperWord {
    WhisperWord(start: start, end: end, word: text)
}

private func ranges(
    _ words: [WhisperWord], threshold: Double = Segmentation.defaultGapThresholdSec
) throws -> [ClipRange] {
    try Segmentation.rangesBySilence(words: words, gapThresholdSec: threshold)
}

// MARK: - segment_words_by_silence port (11)

@Test func emptyInputReturnsEmptyList() throws {
    #expect(try ranges([]) == [])
}

@Test func singleWordReturnsOneRange() throws {
    #expect(try ranges([w(0.0, 0.5)]) == [ClipRange(0.0, 0.5)])
}

@Test func contiguousWordsBecomeOneRange() throws {
    let words = [w(0.0, 0.5), w(0.6, 1.0), w(1.1, 1.5), w(1.7, 2.0)]
    #expect(try ranges(words) == [ClipRange(0.0, 2.0)])
}

@Test func aboveThresholdGapStartsNewRange() throws {
    // gap = 3.0 - 0.5 = 2.5s -> above the 2.0 default -> split.
    #expect(try ranges([w(0.0, 0.5), w(3.0, 3.5)]) == [ClipRange(0.0, 0.5), ClipRange(3.0, 3.5)])
}

@Test func exactThresholdGapSplits() throws {
    // THE load-bearing comparison: a gap of exactly the threshold splits
    // (>= boundary — mac/CLAUDE.md invariant, tested by name).
    let words = [w(0.0, 0.5), w(2.5, 3.0)]
    #expect(try ranges(words, threshold: 2.0) == [ClipRange(0.0, 0.5), ClipRange(2.5, 3.0)])
}

@Test func justUnderThresholdDoesNotSplit() throws {
    // gap = 1.99 -> below 2.0 -> no split.
    #expect(try ranges([w(0.0, 0.5), w(2.49, 3.0)], threshold: 2.0) == [ClipRange(0.0, 3.0)])
}

@Test func multipleSegments() throws {
    let words = [
        w(0.0, 0.5), w(1.0, 1.5),
        // gap of 3.0 -> split
        w(4.5, 5.0), w(5.5, 6.0),
        // gap of 5.0 -> split
        w(11.0, 11.5),
    ]
    #expect(try ranges(words) == [ClipRange(0.0, 1.5), ClipRange(4.5, 6.0), ClipRange(11.0, 11.5)])
}

@Test func customThreshold() throws {
    // gap = 0.5 -> split with threshold 0.4.
    #expect(try ranges([w(0.0, 0.5), w(1.0, 1.5)], threshold: 0.4)
        == [ClipRange(0.0, 0.5), ClipRange(1.0, 1.5)])
}

@Test func negativeThresholdThrows() {
    #expect(throws: SegmentationError.negativeGapThreshold(-0.1)) {
        try Segmentation.rangesBySilence(words: [w(0.0, 0.5)], gapThresholdSec: -0.1)
    }
}

@Test func zeroThresholdSplitsEveryWord() throws {
    // gap == 0 between each pair -> all ">=" 0 -> every word its own range.
    let words = [w(0.0, 0.5), w(0.5, 1.0), w(1.0, 1.5)]
    #expect(try ranges(words, threshold: 0.0)
        == [ClipRange(0.0, 0.5), ClipRange(0.5, 1.0), ClipRange(1.0, 1.5)])
}

@Test func defaultThresholdMatchesSpec() {
    // Lock the spec's 2.0s default so a typo can't silently drift it.
    #expect(Segmentation.defaultGapThresholdSec == 2.0)
}

// MARK: - D18 tail policy

@Test func wordEndPolicyLeavesRangesUntouched() {
    let raw = [ClipRange(0.0, 1.0), ClipRange(5.0, 6.0)]
    let out = Segmentation.applyTailPolicy(.wordEnd, to: raw, tailPaddingSec: 0.25, sourceDurationSec: 100)
    #expect(out == raw)
}

@Test func extendToNextWordStartExtendsEachEndToTheNextClipsFirstWord() {
    // Ranges partition consecutive words, so the next range's start IS the
    // next word's start — the full silence tail joins the preceding clip.
    let raw = [ClipRange(0.0, 1.0), ClipRange(5.0, 6.0), ClipRange(9.0, 9.5)]
    let out = Segmentation.applyTailPolicy(
        .extendToNextWordStart, to: raw, tailPaddingSec: 0.25, sourceDurationSec: 20.0)
    #expect(out == [ClipRange(0.0, 5.0), ClipRange(5.0, 9.0), ClipRange(9.0, 20.0)])
}

@Test func extendToNextWordStartLastClipExtendsToSourceDuration() {
    let out = Segmentation.applyTailPolicy(
        .extendToNextWordStart, to: [ClipRange(2.0, 4.0)], tailPaddingSec: 0, sourceDurationSec: 30.0)
    #expect(out == [ClipRange(2.0, 30.0)])
}

@Test func extendToNextWordStartWithUnknownDurationKeepsLastWordEnd() {
    // Spec (backlog origin): last clip extends to duration "if known" — or
    // stays at last-word-end.
    let out = Segmentation.applyTailPolicy(
        .extendToNextWordStart, to: [ClipRange(2.0, 4.0)], tailPaddingSec: 0, sourceDurationSec: nil)
    #expect(out == [ClipRange(2.0, 4.0)])
}

@Test func extendNeverShrinksWhenSidecarDurationIsShorterThanLastWord() {
    // Degenerate sidecar (duration < last word end): extension only grows.
    let out = Segmentation.applyTailPolicy(
        .extendToNextWordStart, to: [ClipRange(2.0, 4.0)], tailPaddingSec: 0, sourceDurationSec: 3.0)
    #expect(out == [ClipRange(2.0, 4.0)])
}

@Test func fixedPaddingPadsButNeverCrossesTheNextClipsStart() {
    // PROVISIONAL 2: padding clamps to the next clip's first-word start so
    // auto-detected clips never swallow the following take's onset.
    let raw = [ClipRange(0.0, 1.0), ClipRange(1.2, 2.0), ClipRange(10.0, 11.0)]
    let out = Segmentation.applyTailPolicy(
        .fixedPadding, to: raw, tailPaddingSec: 0.5, sourceDurationSec: 100)
    #expect(out == [ClipRange(0.0, 1.2), ClipRange(1.2, 2.5), ClipRange(10.0, 11.5)])
}

@Test func fixedPaddingClampsToSourceDurationWhenKnown() {
    let out = Segmentation.applyTailPolicy(
        .fixedPadding, to: [ClipRange(0.0, 10.0)], tailPaddingSec: 1.0, sourceDurationSec: 10.4)
    #expect(out == [ClipRange(0.0, 10.4)])
    let unclamped = Segmentation.applyTailPolicy(
        .fixedPadding, to: [ClipRange(0.0, 10.0)], tailPaddingSec: 1.0, sourceDurationSec: nil)
    #expect(unclamped == [ClipRange(0.0, 11.0)])
}

@Test func fixedPaddingZeroIsIdentity() {
    let raw = [ClipRange(0.0, 1.0), ClipRange(5.0, 6.0)]
    let out = Segmentation.applyTailPolicy(.fixedPadding, to: raw, tailPaddingSec: 0.0, sourceDurationSec: 50)
    #expect(out == raw)
}

@Test func segmentCompositeAppliesThresholdThenTail() throws {
    let words = [w(0.0, 0.5), w(0.6, 1.0), w(4.0, 4.5), w(4.6, 5.0)]
    let out = try Segmentation.segment(
        words: words,
        gapThresholdSec: 2.0,
        tailPolicy: .extendToNextWordStart,
        tailPaddingSec: 0.25,
        sourceDurationSec: 60.0
    )
    #expect(out == [ClipRange(0.0, 4.0), ClipRange(4.0, 60.0)])
}

// MARK: - transcript_text_for_range port

private let sampleTranscript = WhisperTranscript(
    schemaVersion: 1,
    duration: 10.0,
    segments: [
        WhisperSegment(id: 0, start: 0.0, end: 3.0, words: [
            WhisperWord(start: 0.0, end: 0.5, word: " hello"),
            WhisperWord(start: 0.6, end: 1.0, word: " world"),
        ]),
        WhisperSegment(id: 1, start: 4.0, end: 6.0, words: [
            WhisperWord(start: 4.0, end: 4.5, word: " second"),
            WhisperWord(start: 4.6, end: 5.0, word: " clip"),
        ]),
    ]
)

@Test func transcriptTextConcatenatesRawAndStrips() {
    // Leading-space word convention: concatenate raw, strip the ends.
    #expect(sampleTranscript.transcriptText(from: 0.0, to: 1.0) == "hello world")
}

@Test func transcriptTextRangeIsHalfOpen() {
    // A word with start == end-of-range belongs to the NEXT clip; a word
    // with end == start-of-range is excluded from this one.
    #expect(sampleTranscript.transcriptText(from: 0.0, to: 4.0) == "hello world")
    #expect(sampleTranscript.transcriptText(from: 0.5, to: 4.0) == "world")
    #expect(sampleTranscript.transcriptText(from: 0.0, to: 0.6) == "hello")
}

@Test func transcriptTextSpansSegments() {
    #expect(sampleTranscript.transcriptText(from: 0.0, to: 10.0) == "hello world second clip")
}

@Test func transcriptTextEmptyWhenNoWordsInRange() {
    #expect(sampleTranscript.transcriptText(from: 1.5, to: 3.5) == "")
}
