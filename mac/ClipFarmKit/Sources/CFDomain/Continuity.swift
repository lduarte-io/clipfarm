/// Continuity score (port of the reference `continuity.py` +
/// `attempts_ops.refresh_attempt_continuity`) — the indicator that
/// distinguishes a one-take straight-through from a heavily-Frankensteined
/// assembly.
///
/// Formula (locked): walk the attempt's clips in order, grouping consecutive
/// clips into runs where each run satisfies BOTH: same source, AND forward
/// progression in source time (next clip's `startSec >= ` previous clip's
/// `endSec`). Score = max-run-runtime / total-runtime.
///
/// Cache invariant: `Attempt.continuityScore` is derived — recomputed on
/// every clip-list write; readers may recompute defensively.

public enum ContinuityError: Error, Equatable {
    /// No meaningful "continuity of nothing" — the orchestrator never
    /// passes an empty list; defense-in-depth for future callers.
    case emptyAttempt
    /// Every referenced clip is missing from state.
    case allClipsMissing
    /// Every clip has zero duration — broken data upstream.
    case zeroTotalRuntime
}

/// Wall-clock runtime of one attempt clip: base duration minus per-attempt
/// trim offsets (positive shrinks, negative extends), floored at zero.
/// Orphan references contribute zero.
private func attemptClipRuntime(_ attemptClip: AttemptClip, in state: ClipFarmState) -> Double {
    guard let base = state.clips[attemptClip.clipID] else { return 0.0 }
    let duration = base.endSec - base.startSec
    let trimmed = duration - attemptClip.trimStartOffset - attemptClip.trimEndOffset
    return max(0.0, trimmed)
}

/// Contiguous-in-source runs of the attempt's clip list, as
/// `(runtime, clips)` pairs. Run boundary: source change, backward jump in
/// source time, or an orphan reference (an orphan can't be contiguous with
/// anything — it breaks the run and joins none).
private func runs(
    of attemptClips: [AttemptClip], in state: ClipFarmState
) -> [(runtime: Double, clips: [AttemptClip])] {
    var result: [(runtime: Double, clips: [AttemptClip])] = []
    var currentRun: [AttemptClip] = []
    var currentRuntime = 0.0
    var previousSource: String?
    var previousEnd = -1.0

    func flush() {
        if !currentRun.isEmpty {
            result.append((currentRuntime, currentRun))
            currentRun = []
            currentRuntime = 0.0
        }
    }

    for attemptClip in attemptClips {
        guard let base = state.clips[attemptClip.clipID] else {
            flush()
            previousSource = nil
            previousEnd = -1.0
            continue
        }
        let startsNewRun = previousSource == nil
            || base.sourceID != previousSource
            || base.startSec < previousEnd
        if startsNewRun {
            flush()
        }
        currentRun.append(attemptClip)
        currentRuntime += attemptClipRuntime(attemptClip, in: state)
        previousSource = base.sourceID
        previousEnd = base.endSec
    }
    flush()
    return result
}

/// The continuity score for `attemptClips` against `state`, in [0, 1].
public func continuityScore(
    of attemptClips: [AttemptClip], in state: ClipFarmState
) throws -> Double {
    guard !attemptClips.isEmpty else {
        throw ContinuityError.emptyAttempt
    }
    let allRuns = runs(of: attemptClips, in: state)
    guard !allRuns.isEmpty else {
        throw ContinuityError.allClipsMissing
    }
    let totalRuntime = allRuns.reduce(0.0) { $0 + $1.runtime }
    guard totalRuntime > 0.0 else {
        throw ContinuityError.zeroTotalRuntime
    }
    let maxRun = allRuns.map(\.runtime).max() ?? 0.0
    return maxRun / totalRuntime
}

/// Recompute `attempt.continuityScore` from its current clip list — called
/// after every clip-list mutation. Degenerate cases (empty list, all
/// orphans, zero runtime) set nil instead of throwing: the cache simply has
/// no meaningful value.
public func refreshContinuityScore(of attempt: inout Attempt, in state: ClipFarmState) {
    guard !attempt.clips.isEmpty else {
        attempt.continuityScore = nil
        return
    }
    attempt.continuityScore = try? continuityScore(of: attempt.clips, in: state)
}
