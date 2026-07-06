import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 5 — composition rebuild < 10 ms @ 50 clips (warm cache) + the
/// end-to-end number that matters: edit → new item first frame delivered.
/// Two rebuild legs when the inbox allows: uniform (single source — bare
/// composition, the §6 budget's shape) and mixed geometry (two differing
/// sources — every rebuild also constructs the D32 videoComposition).
@MainActor
func runRebuild(env: HarnessEnv) async throws {
    let probed = await env.probedRealFiles()
    let a: URL, b: URL
    let da: Double, db: Double
    let materialNote: String
    if probed.count >= 2 {
        let byLength = probed.sorted { $0.meta.duration.seconds > $1.meta.duration.seconds }
        (a, da) = (byLength[0].url, byLength[0].meta.duration.seconds)
        (b, db) = (byLength[1].url, byLength[1].meta.duration.seconds)
        materialNote = "- material: real \(a.lastPathComponent) + \(b.lastPathComponent)"
    } else if let only = probed.first {
        (a, da) = (only.url, only.meta.duration.seconds)
        (b, db) = (a, da)
        materialNote = "- material: real \(a.lastPathComponent) only (single-file inbox; mixed leg = uniform leg)"
    } else {
        a = try await env.ensureFixture(FixtureSet.h264A)
        b = try await env.ensureFixture(FixtureSet.h264B)
        (da, db) = (FixtureSet.h264A.durationSec, FixtureSet.h264B.durationSec)
        materialNote = "- material: synthetic h264A/h264B (inbox empty) — re-run when populated"
    }

    func makeRanges(_ edit: Int, mixed: Bool) -> [PlayableRange] {
        (0..<50).map { i in
            let (file, dur) = mixed && i % 2 == 1 ? (b, db) : (a, da)
            let usable = dur - 2.6
            let start = 1.0 + Double(i) / 50.0 * (usable - 1.0) + 0.113
            let nudge = i == 25 ? Double(edit % 2) * (1.0 / 30.0) : 0
            return PlayableRange(url: file, startSec: start, endSec: start + 1.3 + nudge)
        }
    }

    let cache = AssetCache()
    let builder = CompositionBuilder(assetCache: cache)
    _ = try await builder.build(ranges: makeRanges(0, mixed: false))  // warm the cache
    _ = try await builder.build(ranges: makeRanges(0, mixed: true))

    var rebuildMs: [Double] = []
    var rebuildMixedMs: [Double] = []
    for i in 0..<100 {
        let t0 = CACurrentMediaTime()
        _ = try await builder.build(ranges: makeRanges(i, mixed: false))
        rebuildMs.append(CACurrentMediaTime() - t0)
        let t1 = CACurrentMediaTime()
        _ = try await builder.build(ranges: makeRanges(i, mixed: true))
        rebuildMixedMs.append(CACurrentMediaTime() - t1)
    }

    // End-to-end: edit → first frame delivered from the swapped item.
    let engine = PlayerEngine(assetCache: cache)
    var currentTap: FrameTap?
    engine.itemConfigurator = { item in
        currentTap?.stop()
        let tap = FrameTap(item: item, decode: false)
        tap.start()
        currentTap = tap
    }
    try await engine.load(ranges: makeRanges(0, mixed: true))
    engine.player.isMuted = true
    await engine.seek(toCompositionSeconds: 8.0)
    engine.pause()

    var editToFrameMs: [Double] = []
    var editToReadyMs: [Double] = []
    for i in 0..<30 {
        let t0 = CACurrentMediaTime()
        try await engine.load(ranges: makeRanges(i + 1, mixed: true), at: 8.0)
        let ready = CACurrentMediaTime()
        if let tap = currentTap,
           let first = await awaitSample(tap, timeoutSec: 3.0, where: { $0.hostTime > t0 }) {
            editToFrameMs.append(first.hostTime - t0)
        }
        editToReadyMs.append(ready - t0)
        try await Task.sleep(for: .milliseconds(30))
    }
    currentTap?.stop()

    var report: [String] = []
    report.append("**rebuild** — 50-clip composition, warm asset cache")
    report.append(materialNote)
    report.append("- rebuild only, uniform (bare composition): \(Stats.summary(rebuildMs))")
    report.append("- rebuild only, mixed geometry (+ videoComposition): \(Stats.summary(rebuildMixedMs))")
    let uniformPass = Stats.percentile(rebuildMs.map { $0 * 1000 }, 95) < 10
    let mixedPass = Stats.percentile(rebuildMixedMs.map { $0 * 1000 }, 95) < 10
    report.append("- GATE (rebuild p95 < 10ms): uniform \(uniformPass ? "PASS" : "FAIL"), mixed \(mixedPass ? "PASS" : "FAIL")")
    report.append("- edit → load() returned (mixed comp; incl. build + pre-seek await + swap): \(Stats.summary(editToReadyMs))")
    report.append("- edit → first frame delivered (paused): \(Stats.summary(editToFrameMs))")
    env.report("rebuild", report)
}

/// Gate 7 — worst-case trim-loop restart on long-GOP 4K HEVC with a
/// non-keyframe-aligned window: boundary-fire → first frame at window
/// start ≤ 50 ms (§6 budget). The gate leg is synthetic (no 4K HEVC in
/// the inbox); a corroboration leg runs on real inbox material.
@MainActor
func runLoop(env: HarnessEnv, loops: Int) async throws {
    // Leg 1 (the GATE): source keyframes every 4s (0,4,8,…). Composition
    // = source [12,22]; window comp [5.37, 6.87] → source [17.37, 18.87]:
    // mid-GOP both ends.
    let hevcURL = try await env.ensureFixture(FixtureSet.hevc4K)
    try await measureLoopRestart(
        env: env,
        label: "synthetic 4K HEVC long-GOP (keyframes 4s apart) [GATE leg]",
        url: hevcURL, compositionRange: (12.0, 22.0), window: (5.37, 6.87),
        loops: loops, isGateLeg: true)

    // Leg 2 (corroboration): the longest real inbox file; window start
    // carries an odd phase so it sits off recorder keyframe grids.
    let probed = await env.probedRealFiles()
    if let real = probed.max(by: { $0.meta.duration.seconds < $1.meta.duration.seconds }) {
        let d = real.meta.duration.seconds
        let lo = min(1.0, d * 0.02)
        let windowStart = d * 0.25 - lo + 0.37  // composition time
        try await measureLoopRestart(
            env: env,
            label: "real \(real.url.lastPathComponent) (\(fmt(d, 1))s) [corroboration leg]",
            url: real.url, compositionRange: (lo, d - 0.5),
            window: (windowStart, windowStart + 1.5),
            loops: min(loops, 30), isGateLeg: false)
    } else {
        env.report("looptest", [
            "**looptest(real)** — DEFERRED: footage inbox empty; corroboration leg re-runs once populated",
        ])
    }
}

@MainActor
private func measureLoopRestart(
    env: HarnessEnv, label: String, url: URL,
    compositionRange: (start: Double, end: Double),
    window: (start: Double, end: Double),
    loops: Int, isGateLeg: Bool
) async throws {
    let engine = PlayerEngine()
    var tap: FrameTap?
    engine.itemConfigurator = { item in
        tap = FrameTap(item: item, decode: false)
        tap?.start()
    }
    try await engine.load(ranges: [
        PlayableRange(url: url, startSec: compositionRange.start, endSec: compositionRange.end)
    ])
    engine.player.isMuted = true

    // Harness-side boundary observer timestamps the fire; the engine's own
    // observer does the re-seek (both at the same boundary time).
    let fireBox = FireTimeBox()
    let observer = engine.player.addBoundaryTimeObserver(
        forTimes: [NSValue(time: MediaTime.time(window.end))], queue: .main
    ) {
        fireBox.record(CACurrentMediaTime())
    }
    defer { engine.player.removeTimeObserver(observer) }

    engine.loop(windowStartSec: window.start, windowEndSec: window.end)
    await engine.seek(toCompositionSeconds: window.start)
    engine.play()

    var restartMs: [Double] = []
    var missedFires = 0
    for _ in 0..<loops {
        guard let fire = await fireBox.next(timeoutSec: (window.end - window.start) + 5) else {
            missedFires += 1
            continue
        }
        guard let tap else { break }
        let restart = await awaitSample(tap, timeoutSec: 3.0) {
            $0.hostTime > fire && abs($0.itemTimeSec - window.start) < 0.05
        }
        if let restart {
            restartMs.append(restart.hostTime - fire)
        } else {
            missedFires += 1
        }
    }
    tap?.stop()
    engine.pause()

    var report: [String] = []
    report.append("**looptest** — \(label), non-keyframe window [\(fmt(window.start)), \(fmt(window.end))] comp-time, \(loops) loops")
    report.append("- boundary-fire → first frame at window start: \(Stats.summary(restartMs))")
    report.append("- missed/unmeasured fires: \(missedFires)")
    let verdict = Stats.percentile(restartMs.map { $0 * 1000 }, 95) <= 50 ? "PASS" : "FAIL"
    report.append(isGateLeg
        ? "- GATE (≤ 50ms p95): \(verdict)"
        : "- corroboration (≤ 50ms p95, informational): \(verdict)")
    env.report("looptest", report)
}

/// Serializes boundary-fire host times to the measuring task.
final class FireTimeBox: @unchecked Sendable {
    private let lock = NSLock()
    private var pending: [Double] = []

    func record(_ hostTime: Double) {
        lock.lock()
        pending.append(hostTime)
        lock.unlock()
    }

    func next(timeoutSec: Double) async -> Double? {
        let deadline = CACurrentMediaTime() + timeoutSec
        while CACurrentMediaTime() < deadline {
            // Scoped locking — bare lock()/unlock() is (rightly) unavailable
            // in async contexts; the critical section never suspends.
            let value = lock.withLock { pending.isEmpty ? nil : pending.removeFirst() }
            if let value { return value }
            try? await Task.sleep(for: .milliseconds(2))
        }
        return nil
    }
}

/// Gate 6 — frame accuracy at cut boundaries + `step(byCount:)` across a
/// composition (fixture content is self-identifying, so "which source
/// frame is showing" is exact).
@MainActor
func runFrameAccuracy(env: HarnessEnv) async throws {
    let a = try await env.ensureFixture(FixtureSet.h264A)
    let b = try await env.ensureFixture(FixtureSet.hevc1080)
    // Same 1920×1080 / identity geometry → bare composition; H.264 + HEVC
    // long-GOP; cut points deliberately off the keyframe grid AND off
    // whole-frame times.
    let ranges = [
        PlayableRange(url: a, startSec: 3.777, endSec: 6.101),
        PlayableRange(url: b, startSec: 10.313, endSec: 12.529),
        PlayableRange(url: a, startSec: 20.111, endSec: 22.997),
        PlayableRange(url: b, startSec: 30.007, endSec: 31.703),
    ]
    let engine = PlayerEngine()
    var tap: FrameTap?
    engine.itemConfigurator = { item in
        tap = FrameTap(item: item, decode: true)
        tap?.start()
    }
    try await engine.load(ranges: ranges)
    engine.player.isMuted = true
    engine.pause()
    guard let tap, let built = engine.built else {
        throw HarnessError.internalFailure("tap/built missing")
    }

    var report: [String] = []
    var seekChecks = 0
    var seekCorrect = 0
    for segment in built.segments {
        // Expected first frame: the source frame whose display interval
        // contains the (CMTime-exact) clamped in-point.
        let sourceStart = segment.sourceStart.seconds
        let expected = Int((sourceStart * 30.0 + 1e-6).rounded(.down))
        tap.clear()
        await engine.seek(toCompositionSeconds: MediaTime.seconds(segment.compositionStart))
        let sample = await awaitSample(tap, timeoutSec: 3.0) { $0.frameIndex != nil }
        seekChecks += 1
        if let sample, sample.frameIndex == expected {
            seekCorrect += 1
        } else {
            report.append(
                "- seek to seam @\(fmt(MediaTime.seconds(segment.compositionStart))): expected frame \(expected), got \(String(describing: sample?.frameIndex))")
        }
    }

    // step(byCount:) forward across the first seam and backward again.
    let seam = MediaTime.seconds(built.segments[1].compositionStart)
    let frameDur = 1.0 / 30.0
    await engine.seek(toCompositionSeconds: seam - 3 * frameDur)
    var stepChecks = 0
    var stepCorrect = 0
    var lastIndex: Int?
    for stepNumber in 0..<6 {
        tap.clear()
        engine.step(frames: 1)
        let sample = await awaitSample(tap, timeoutSec: 2.0) { $0.frameIndex != nil }
        if let index = sample?.frameIndex {
            if let last = lastIndex {
                stepChecks += 1
                // Within a segment: exactly +1. Across the seam the index
                // jumps to the next segment's expected first frame.
                let crossesSeam = stepNumber == 2  // 3 frames before seam, stepping into it
                if crossesSeam {
                    let expected = Int((built.segments[1].sourceStart.seconds * 30.0 + 1e-6).rounded(.down))
                    if index == expected { stepCorrect += 1 } else {
                        report.append("- step across seam: expected \(expected), got \(index)")
                    }
                } else if index == last + 1 {
                    stepCorrect += 1
                } else {
                    report.append("- step +1: expected \(last + 1), got \(index)")
                }
            }
            lastIndex = index
        }
    }
    // Backward steps.
    for _ in 0..<3 {
        tap.clear()
        engine.step(frames: -1)
        let sample = await awaitSample(tap, timeoutSec: 2.0) { $0.frameIndex != nil }
        if let index = sample?.frameIndex, let last = lastIndex {
            stepChecks += 1
            if index == last - 1 { stepCorrect += 1 } else {
                report.append("- step -1: expected \(last - 1), got \(index)")
            }
            lastIndex = index
        }
    }
    tap.stop()

    report.insert("**frameacc** — H.264 + HEVC long-GOP fixtures, non-keyframe cuts", at: 0)
    report.insert("- seam seeks frame-exact: \(seekCorrect)/\(seekChecks)", at: 1)
    report.insert("- step(byCount:) exact (±1 frame incl. across seam): \(stepCorrect)/\(stepChecks)", at: 2)
    report.insert("- GATE: \(seekCorrect == seekChecks && stepCorrect == stepChecks ? "PASS" : "FAIL")", at: 3)
    env.report("frameacc", report)
}
