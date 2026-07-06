/// ID rules (mac/CLAUDE.md → Invariants):
/// - All IDs are strings.
/// - Clip IDs encode `source__start__end` at creation for human readability
///   and are opaque afterward — boundary edits mutate `startSec`/`endSec`
///   without changing the ID; the encoded form is never re-derived.
/// - Source filename stems cannot contain `__` (the reserved separator).
/// - Numeric ID allocators are monotonic max+1 over ALL existing keys;
///   freed slots are never reused.

public enum ClipID {
    /// Reserved as the clip-ID separator; source filename stems containing
    /// it are rejected at ingest with a sanitized-rename offer.
    public static let reservedSeparator = "__"

    /// `HH-MM-SS.mmm` — dashes, not colons, so the ID is filename/URL-safe.
    ///
    /// Millisecond rounding is **half-even** (`.toNearestOrEven`) to match
    /// Python 3's `int(round(t * 1000))` exactly — clip IDs must
    /// golden-master-match the reference segmentation output at N3.
    /// Negative times clamp to zero, as in the reference.
    public static func hms(_ t: Double) -> String {
        let totalMS = Int((max(0.0, t) * 1000).rounded(.toNearestOrEven))
        let h = totalMS / 3_600_000
        let m = (totalMS % 3_600_000) / 60_000
        let s = (totalMS % 60_000) / 1000
        let ms = totalMS % 1000
        return "\(padded(h, 2))-\(padded(m, 2))-\(padded(s, 2)).\(padded(ms, 3))"
    }

    private static func padded(_ n: Int, _ width: Int) -> String {
        let digits = String(n)
        return digits.count >= width
            ? digits
            : String(repeating: "0", count: width - digits.count) + digits
    }

    /// The encoded-at-birth clip ID: `<stem>__<hms(start)>__<hms(end)>`.
    public static func make(sourceStem: String, start: Double, end: Double) -> String {
        "\(sourceStem)\(reservedSeparator)\(hms(start))\(reservedSeparator)\(hms(end))"
    }

    /// False when the stem contains the reserved `__` separator.
    public static func stemIsValid(_ stem: String) -> Bool {
        !stem.contains(reservedSeparator)
    }

    /// The rename suggestion offered at ingest rejection: collapse every run
    /// of `__` down to a single `_` (`my__file` → `my_file`).
    public static func sanitizedStem(_ stem: String) -> String {
        var out = stem
        while out.contains(reservedSeparator) {
            out = out.replacing(reservedSeparator, with: "_")
        }
        return out
    }
}

/// Next monotonic stringified-integer ID over `existing` keys: max+1, `"1"`
/// when no numeric keys exist. Non-numeric keys are ignored (Python parity:
/// `k.isdigit()` — ASCII digits only, so `"-3"` and `""` don't count).
/// Freed slots are never reused: deleting `"3"` from `{1,2,3}` still
/// allocates `"4"` next, keeping snapshot diffs readable across
/// delete-then-create sequences.
public func nextNumericID(over existing: some Sequence<String>) -> String {
    let used = existing.compactMap { key -> Int? in
        guard !key.isEmpty, key.allSatisfy({ $0.isASCII && $0.isWholeNumber }) else { return nil }
        return Int(key)
    }
    return String((used.max() ?? 0) + 1)
}
