import Testing
@testable import CFDomain

/// Clip-ID encoding + allocator rules (mac/CLAUDE.md invariants; encoding
/// semantics from the reference `_hms`/`_make_clip_id`).

// MARK: - hms encoding

@Test func hmsFormatsHoursMinutesSecondsMillis() {
    #expect(ClipID.hms(72.345) == "00-01-12.345")
    #expect(ClipID.hms(0) == "00-00-00.000")
    #expect(ClipID.hms(3661.001) == "01-01-01.001")
}

@Test func hmsClampsNegativeTimesToZero() {
    #expect(ClipID.hms(-5.0) == "00-00-00.000")
}

@Test func hmsRoundsHalfEvenLikePythonRound() {
    // Python 3 `int(round(t * 1000))` is banker's rounding — clip IDs must
    // golden-master-match the reference segmentation output at N3.
    #expect(ClipID.hms(0.0005) == "00-00-00.000")  // 0.5ms → 0 (even)
    #expect(ClipID.hms(0.0015) == "00-00-00.002")  // 1.5ms → 2 (even)
    #expect(ClipID.hms(0.0025) == "00-00-00.002")  // 2.5ms → 2 (even)
}

@Test func hmsRollsOverAtUnitBoundaries() {
    #expect(ClipID.hms(59.9995) == "00-01-00.000")  // 59999.5ms → 60000 (even)
    #expect(ClipID.hms(3599.999) == "00-59-59.999")
    #expect(ClipID.hms(3600.0) == "01-00-00.000")
}

// MARK: - Clip ID shape + stem rules

@Test func makeClipIDEncodesSourceStartEnd() {
    #expect(
        ClipID.make(sourceStem: "btc.0.4", start: 72.345, end: 78.220)
            == "btc.0.4__00-01-12.345__00-01-18.220"
    )
}

@Test func stemContainingReservedSeparatorIsInvalid() {
    #expect(!ClipID.stemIsValid("my__file"))
    #expect(ClipID.stemIsValid("my_file"))
    #expect(ClipID.stemIsValid("btc.0.4"))
}

@Test func sanitizedStemCollapsesSeparatorRuns() {
    #expect(ClipID.sanitizedStem("my__file") == "my_file")
    #expect(ClipID.sanitizedStem("a____b") == "a_b")
    #expect(ClipID.stemIsValid(ClipID.sanitizedStem("a______b")))
}

// MARK: - Numeric ID allocation

@Test func allocatorStartsAtOne() {
    #expect(nextNumericID(over: [String]()) == "1")
}

@Test func allocatorIsMaxPlusOne() {
    #expect(nextNumericID(over: ["1", "2"]) == "3")
}

@Test func allocatorNeverFillsFreedGaps() {
    // Deleting "2" from {1,2,3} leaves {1,3}; the next ID is still "4" —
    // freed slots below the max are never reused.
    #expect(nextNumericID(over: ["1", "3"]) == "4")
}

@Test func allocatorIgnoresNonNumericKeys() {
    // Python parity (`k.isdigit()`): "-3", "", and word keys don't count.
    #expect(nextNumericID(over: ["2", "x", ""]) == "3")
    #expect(nextNumericID(over: ["-3"]) == "1")
    #expect(nextNumericID(over: ["btc.0.4__00-00-00.000__00-00-01.000"]) == "1")
}
