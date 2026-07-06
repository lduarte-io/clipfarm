/// Silence-gap clip segmentation + the D18 tail policy — pure functions,
/// no I/O (port of the reference `segmentation.py`; tail policy is native,
/// spec amendment 7).
///
/// Load-bearing comparison (mac/CLAUDE.md, tested by name): a new clip
/// starts when the gap from the previous word's end to the current word's
/// start is **>= threshold**.

/// D18: how a clip's `end_sec` extends past its last word. Whisper's
/// `word.end` lops the natural speech tail (breath, mouth-close, pre-silence
/// ambient) — the 2026-05-26 "clips feel cut short" dogfood finding. Tail
/// behavior varies by speaker, so it's a per-library setting, not a
/// constant.
public enum SegmentationTailPolicy: String, Sendable, CaseIterable, Codable {
    /// Default: each clip's end extends to the next word's start; the last
    /// clip extends to the source duration.
    case extendToNextWordStart = "extend-to-next-word-start"
    /// Fixed padding of `tailPaddingSec` beyond the last word's end.
    case fixedPadding = "fixed-padding"
    /// The raw Whisper `word.end` (the web implementation's behavior —
    /// kept for golden-master comparison).
    case wordEnd = "word-end"
}

/// A candidate clip's time range. (A struct, not a tuple, so arrays compare
/// with `==` in tests and callers.)
public struct ClipRange: Equatable, Sendable {
    public var startSec: Double
    public var endSec: Double

    public init(_ startSec: Double, _ endSec: Double) {
        self.startSec = startSec
        self.endSec = endSec
    }
}

public enum SegmentationError: Error, Equatable {
    case negativeGapThreshold(Double)
}

public enum Segmentation {
    /// The spec's default silence threshold. A per-library setting since D18
    /// (`LibrarySettings.silenceThresholdSec`); this constant is the default
    /// and the reference parity anchor.
    public static let defaultGapThresholdSec = 2.0

    /// Groups `words` into raw clip ranges: each range's start is its first
    /// word's start, its end the last word's end (tail policy applies
    /// afterward — see `applyTailPolicy`). A new range opens when the gap
    /// from the previous word's end is **>=** `gapThresholdSec`. Empty input
    /// returns an empty list. Pure port of `segment_words_by_silence`.
    public static func rangesBySilence(
        words: [WhisperWord],
        gapThresholdSec: Double = defaultGapThresholdSec
    ) throws -> [ClipRange] {
        guard gapThresholdSec >= 0 else {
            throw SegmentationError.negativeGapThreshold(gapThresholdSec)
        }
        guard let first = words.first else { return [] }

        var ranges: [ClipRange] = []
        var currentStart = first.start
        var currentEnd = first.end
        var previousEnd = first.end

        for word in words.dropFirst() {
            if word.start - previousEnd >= gapThresholdSec {
                ranges.append(ClipRange(currentStart, currentEnd))
                currentStart = word.start
            }
            currentEnd = word.end
            previousEnd = word.end
        }
        ranges.append(ClipRange(currentStart, currentEnd))
        return ranges
    }

    /// D18 tail policy over silence-segmented ranges. The ranges partition
    /// consecutive words, so "the next word's start" for range `i` is
    /// exactly range `i+1`'s start.
    ///
    /// Clamping (N3 PROVISIONAL 2): `.fixedPadding` never crosses the next
    /// clip's first-word start (padding must not swallow the next take's
    /// onset) and never exceeds the source duration when known. Both
    /// extending policies only ever *grow* an end — a source duration
    /// shorter than the last word's end (sidecar quirk) never shrinks it.
    public static func applyTailPolicy(
        _ policy: SegmentationTailPolicy,
        to ranges: [ClipRange],
        tailPaddingSec: Double,
        sourceDurationSec: Double?
    ) -> [ClipRange] {
        guard policy != .wordEnd, !ranges.isEmpty else { return ranges }
        var out = ranges
        for i in out.indices {
            let isLast = i == out.index(before: out.endIndex)
            let nextStart = isLast ? nil : out[out.index(after: i)].startSec
            switch policy {
            case .extendToNextWordStart:
                if let nextStart {
                    out[i].endSec = nextStart
                } else if let sourceDurationSec {
                    out[i].endSec = max(out[i].endSec, sourceDurationSec)
                }
            case .fixedPadding:
                var padded = out[i].endSec + max(0, tailPaddingSec)
                if let nextStart { padded = min(padded, nextStart) }
                if let sourceDurationSec { padded = min(padded, max(out[i].endSec, sourceDurationSec)) }
                out[i].endSec = max(out[i].endSec, padded)
            case .wordEnd:
                break
            }
        }
        return out
    }

    /// Segment + tail policy in one call — the shape ingest and the
    /// per-source re-apply action consume.
    public static func segment(
        words: [WhisperWord],
        gapThresholdSec: Double,
        tailPolicy: SegmentationTailPolicy,
        tailPaddingSec: Double,
        sourceDurationSec: Double?
    ) throws -> [ClipRange] {
        applyTailPolicy(
            tailPolicy,
            to: try rangesBySilence(words: words, gapThresholdSec: gapThresholdSec),
            tailPaddingSec: tailPaddingSec,
            sourceDurationSec: sourceDurationSec
        )
    }
}

extension WhisperTranscript {
    /// Port of `transcript_text_for_range`: every word whose timing falls
    /// inside the **half-open** `[start, end)` — a word with
    /// `start == end` belongs to the NEXT clip. Word strings carry their own
    /// leading space (faster_whisper convention): concatenate raw, never add
    /// separators; strip the ends for display.
    ///
    /// Shared by ingest segmentation (N3) and boundary correction (N5).
    public func transcriptText(from start: Double, to end: Double) -> String {
        var text = ""
        for segment in segments {
            for word in segment.words {
                if word.end <= start { continue }
                if word.start >= end {
                    // Past the range — words are time-ordered, done.
                    return Self.strippedEnds(text)
                }
                text += word.word
            }
        }
        return Self.strippedEnds(text)
    }

    /// Python `str.strip()` without Foundation (CFDomain is dependency-free).
    static func strippedEnds(_ s: String) -> String {
        var sub = Substring(s)
        while let first = sub.first, first.isWhitespace { sub.removeFirst() }
        while let last = sub.last, last.isWhitespace { sub.removeLast() }
        return String(sub)
    }
}
