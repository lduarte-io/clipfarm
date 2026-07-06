/// Attempt → playback-range resolver (port of the reference `resolver.py`).
/// Pure orchestration: given an attempt, return the ordered list of playable
/// spans and tombstone placeholders. Preview (N2's PlayerEngine) and export
/// (N12) consume the SAME resolved output — the trim / gap-drop / clamp
/// rules live here and only here.
///
/// Contract (locked in the reference Phase 9 plan; ported intact):
/// 1. Item order matches `Attempt.clips` order.
/// 2. Dangling clip → exactly one `.tombstone` (delete keeps the attempt
///    slot so the user can pick a replacement).
/// 3. Live clip → ≥1 `.range` items; multiple only when
///    `internalPauseMaxSec` splits the trimmed span on inter-word gaps.
/// 4. Trim offsets are clamped twice: base-bounds at boundary-correction
///    time (N5), source-bounds HERE (`max(0, start)` /
///    `min(duration ?? ∞, end)`) with a warning when the clamp fires.
/// 5. `internalPauseMaxSec` set but no transcript available → fall back to a
///    single un-expanded range, with a warning.
///
/// `internalPauseMaxSec` semantics: inter-word gaps **strictly greater
/// than** the max split the span; the gap itself is **dropped entirely**
/// between sub-ranges (not collapsed-to-max). The word filter is
/// `w.start >= start && w.end <= end` — words straddling a trim boundary are
/// excluded from gap detection, ported as-is (fix scheduled N15).
///
/// Port adaptations (CFDomain is pure — zero dependencies, no I/O):
/// - The reference loaded transcripts from disk inside the resolver; here a
///   `transcriptProvider` closure is injected. Same fallback semantics.
/// - The reference logged warnings; here they surface through `onWarning`.

public struct ResolvedRange: Equatable, Sendable {
    /// Informational — correlates back to the AttemptClip slot for UI
    /// highlighting.
    public let clipID: String
    public let sourceID: String
    public let effectiveStartSec: Double
    public let effectiveEndSec: Double

    public init(clipID: String, sourceID: String, effectiveStartSec: Double, effectiveEndSec: Double) {
        self.clipID = clipID
        self.sourceID = sourceID
        self.effectiveStartSec = effectiveStartSec
        self.effectiveEndSec = effectiveEndSec
    }
}

public struct TombstoneRange: Equatable, Sendable {
    /// The deleted clip's ID, preserved on the AttemptClip.
    public let clipID: String

    public init(clipID: String) {
        self.clipID = clipID
    }
}

public enum ResolvedItem: Equatable, Sendable {
    case range(ResolvedRange)
    case tombstone(TombstoneRange)

    public var clipID: String {
        switch self {
        case .range(let r): r.clipID
        case .tombstone(let t): t.clipID
        }
    }
}

public enum ResolverError: Error, Equatable {
    case unknownAttempt(attemptID: String)
    /// Trim collapsed the range to zero or negative effective duration —
    /// defense-in-depth; well-formed state never reaches this.
    case nonPositiveEffectiveDuration(clipID: String, effectiveStartSec: Double, effectiveEndSec: Double)
}

public enum ResolverWarning: Equatable, Sendable {
    /// Clip references a source missing from state — the range is emitted
    /// anyway; playback surfaces the error downstream.
    case missingSource(clipID: String, sourceID: String)
    case startClamped(clipID: String, rawStartSec: Double, clampedStartSec: Double)
    case endClamped(clipID: String, rawEndSec: Double, clampedEndSec: Double)
    /// `internalPauseMaxSec` was set but the source is missing or has no
    /// loadable transcript — fell back to a single un-expanded range.
    case transcriptUnavailable(clipID: String, sourceID: String)
}

/// Resolve `attemptID` into ordered playback items.
///
/// - Parameters:
///   - transcriptProvider: supplies a source's word-level transcript when
///     `internalPauseMaxSec` expansion needs one; return nil for
///     footage-only sources (triggers the single-range fallback).
///   - onWarning: receives resolver diagnostics (clamps, fallbacks).
public func resolveAttempt(
    _ attemptID: String,
    in state: ClipFarmState,
    transcriptProvider: (Source) -> WhisperTranscript?,
    onWarning: (ResolverWarning) -> Void = { _ in }
) throws -> [ResolvedItem] {
    guard let attempt = state.attempts[attemptID] else {
        throw ResolverError.unknownAttempt(attemptID: attemptID)
    }
    var items: [ResolvedItem] = []
    for attemptClip in attempt.clips {
        items.append(contentsOf: try resolveOne(
            attemptClip, in: state, transcriptProvider: transcriptProvider, onWarning: onWarning
        ))
    }
    return items
}

private func resolveOne(
    _ attemptClip: AttemptClip,
    in state: ClipFarmState,
    transcriptProvider: (Source) -> WhisperTranscript?,
    onWarning: (ResolverWarning) -> Void
) throws -> [ResolvedItem] {
    guard let clip = state.clips[attemptClip.clipID] else {
        return [.tombstone(TombstoneRange(clipID: attemptClip.clipID))]
    }

    let source = state.sources[clip.sourceID]
    if source == nil {
        onWarning(.missingSource(clipID: attemptClip.clipID, sourceID: clip.sourceID))
    }

    // Raw trimmed span, then the source-bounds clamp (base-bounds clamping
    // is boundary-correction's job).
    let rawStart = clip.startSec + attemptClip.trimStartOffset
    let rawEnd = clip.endSec - attemptClip.trimEndOffset
    let sourceDuration = source?.durationSec ?? .infinity

    let effectiveStart = max(0.0, rawStart)
    let effectiveEnd = min(sourceDuration, rawEnd)
    if effectiveStart != rawStart {
        onWarning(.startClamped(
            clipID: attemptClip.clipID, rawStartSec: rawStart, clampedStartSec: effectiveStart
        ))
    }
    if effectiveEnd != rawEnd {
        onWarning(.endClamped(
            clipID: attemptClip.clipID, rawEndSec: rawEnd, clampedEndSec: effectiveEnd
        ))
    }

    guard effectiveEnd > effectiveStart else {
        throw ResolverError.nonPositiveEffectiveDuration(
            clipID: attemptClip.clipID,
            effectiveStartSec: effectiveStart,
            effectiveEndSec: effectiveEnd
        )
    }

    let singleRange = ResolvedItem.range(ResolvedRange(
        clipID: attemptClip.clipID,
        sourceID: clip.sourceID,
        effectiveStartSec: effectiveStart,
        effectiveEndSec: effectiveEnd
    ))

    guard let maxPause = attemptClip.internalPauseMaxSec else {
        return [singleRange]
    }

    // Expansion needs a transcript; missing source or transcript → fallback.
    guard let source, let transcript = transcriptProvider(source) else {
        onWarning(.transcriptUnavailable(clipID: attemptClip.clipID, sourceID: clip.sourceID))
        return [singleRange]
    }

    // Words fully inside [effectiveStart, effectiveEnd] — straddlers
    // excluded (ported as-is; N15 fixes the straddle case).
    let wordsInRange = transcript.allWords.filter {
        $0.start >= effectiveStart && $0.end <= effectiveEnd
    }
    guard wordsInRange.count >= 2 else {
        return [singleRange]
    }

    var subRanges: [ResolvedItem] = []
    var subStart = effectiveStart
    var previousWordEnd = wordsInRange[0].end
    for word in wordsInRange.dropFirst() {
        let gap = word.start - previousWordEnd
        if gap > maxPause {
            // Split: emit the sub-range ending at the previous word's end;
            // the gap is dropped entirely.
            subRanges.append(.range(ResolvedRange(
                clipID: attemptClip.clipID,
                sourceID: clip.sourceID,
                effectiveStartSec: subStart,
                effectiveEndSec: previousWordEnd
            )))
            subStart = word.start
        }
        previousWordEnd = word.end
    }
    subRanges.append(.range(ResolvedRange(
        clipID: attemptClip.clipID,
        sourceID: clip.sourceID,
        effectiveStartSec: subStart,
        effectiveEndSec: effectiveEnd
    )))
    return subRanges
}
