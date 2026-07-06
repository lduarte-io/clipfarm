import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 5 — composition rebuild < 10 ms @ 50 clips (warm cache) + the
/// end-to-end number that matters: edit → new item first frame delivered.
@MainActor
func runRebuild(env: HarnessEnv) async throws {
    let a = env.footageFile("btc.0.0.mov")
    let b = env.footageFile("btc.0.2.mov")
    func makeRanges(_ edit: Int) -> [PlayableRange] {
        (0..<50).map { i in
            let file = i % 2 == 0 ? a : b
            let start = 3.113 + Double(i) * 1.777
            let nudge = i == 25 ? Double(edit % 2) * (1.0 / 30.0) : 0
            return PlayableRange(url: file, startSec: start, endSec: start + 1.5 + nudge)
        }
    }

    let cache = AssetCache()
    let builder = CompositionBuilder(assetCache: cache)
    _ = try await builder.build(ranges: makeRanges(0))  // warm the cache

    var rebuildMs: [Double] = []
    for i in 0..<100 {
        let t0 = CACurrentMediaTime()
        _ = try await builder.build(ranges: makeRanges(i))
        rebuildMs.append(CACurrentMediaTime() - t0)
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
    try await engine.load(ranges: makeRanges(0))
    engine.player.isMuted = true
    await engine.seek(toCompositionSeconds: 8.0)
    engine.pause()

    var editToFrameMs: [Double] = []
    var editToReadyMs: [Double] = []
    for i in 0..<30 {
        let t0 = CACurrentMediaTime()
        try await engine.load(ranges: makeRanges(i + 1), at: 8.0)
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
    report.append("**rebuild** — 50-clip composition, warm asset cache, real footage")
    report.append("- rebuild only: \(Stats.summary(rebuildMs))")
    report.append("- GATE (rebuild p95 < 10ms): \(Stats.percentile(rebuildMs.map { $0 * 1000 }, 95) < 10 ? "PASS" : "FAIL")")
    report.append("- edit → load() returned (incl. build + pre-seek await + swap): \(Stats.summary(editToReadyMs))")
    report.append("- edit → first frame delivered (paused): \(Stats.summary(editToFrameMs))")
    env.report("rebuild", report)
}

/// Gate 7 — worst-case trim-loop restart on long-GOP 4K HEVC with a
/// non-keyframe-aligned window: boundary-fire → first frame at window
/// start ≤ 50 ms (§6 budget).
@MainActor
func runLoop(env: HarnessEnv, loops: Int) async throws {
    let url = try await env.ensureFixture(FixtureSet.hevc4K)
    // Source keyframes every 4s (0,4,8,…). Composition = source [12,22];
    // window comp [5.37, 6.87] → source [17.37, 18.87]: mid-GOP both ends.
    let engine = PlayerEngine()
    var tap: FrameTap?
    engine.itemConfigurator = { item in
        tap = FrameTap(item: item, decode: true)
        tap?.start()
    }
    try await engine.load(ranges: [PlayableRange(url: url, startSec: 12.0, endSec: 22.0)])
    engine.player.isMuted = true

    let windowStart = 5.37
    let windowEnd = 6.87
    // Harness-side boundary observer timestamps the fire; the engine's own
    // observer does the re-seek (both at the same boundary time).
    let fireBox = FireTimeBox()
    let observer = engine.player.addBoundaryTimeObserver(
        forTimes: [NSValue(time: MediaTime.time(windowEnd))], queue: .main
    ) {
        fireBox.record(CACurrentMediaTime())
    }
    defer { engine.player.removeTimeObserver(observer) }

    engine.loop(windowStartSec: windowStart, windowEndSec: windowEnd)
    await engine.seek(toCompositionSeconds: windowStart)
    engine.play()

    var restartMs: [Double] = []
    var missedFires = 0
    for _ in 0..<loops {
        guard let fire = await fireBox.next(timeoutSec: (windowEnd - windowStart) + 5) else {
            missedFires += 1
            continue
        }
        guard let tap else { break }
        let restart = await awaitSample(tap, timeoutSec: 3.0) {
            $0.hostTime > fire && abs($0.itemTimeSec - windowStart) < 0.05
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
    report.append("**looptest** — 4K HEVC long-GOP (keyframes 4s apart), non-keyframe window [\(windowStart), \(windowEnd)] comp-time, \(loops) loops")
    report.append("- boundary-fire → first frame at window start: \(Stats.summary(restartMs))")
    report.append("- missed/unmeasured fires: \(missedFires)")
    report.append("- GATE (≤ 50ms): \(Stats.percentile(restartMs.map { $0 * 1000 }, 95) <= 50 ? "PASS" : "FAIL") (p95)")
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
            lock.lock()
            let value = pending.isEmpty ? nil : pending.removeFirst()
            lock.unlock()
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
