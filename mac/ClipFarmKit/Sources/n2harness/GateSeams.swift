import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 1 — seam-drop instrumentation: 20+ deliberately non-keyframe cuts;
/// p95 inter-frame delivery gap at seams ≤ 1 incoming frame duration.
///
/// Two runs: `uniform` (real dogfood H.264, bare composition — the common
/// case) and `mixed` (H.264 + HEVC 4K + ProRes + HLG-HDR + portrait, the
/// videoComposition path).
@MainActor
func runSeams(env: HarnessEnv, variant: String) async throws {
    let ranges: [PlayableRange]
    switch variant {
    case "uniform":
        // Real footage, three files, offsets chosen off any whole second
        // (recorder keyframes land on regular grids; ffprobe-verified
        // non-keyframe in the closeout notes).
        let files = [
            env.footageFile("btc.0.0.mov"),
            env.footageFile("btc.0.2.mov"),
            env.footageFile("btc.01.mov"),
        ]
        ranges = (0..<24).map { i in
            let file = files[i % files.count]
            let start = 7.37 + Double(i) * 3.113
            return PlayableRange(url: file, startSec: start, endSec: start + 1.8)
        }
    case "mixed":
        let sources = [
            env.footageFile("btc.0.0.mov"),
            try await env.ensureFixture(FixtureSet.hevc4K),
            try await env.ensureFixture(FixtureSet.proRes),
            try await env.ensureFixture(FixtureSet.hlg),
            try await env.ensureFixture(FixtureSet.portrait),
        ]
        ranges = (0..<22).map { i in
            let source = sources[i % sources.count]
            let start = 1.777 + Double(i % 5) * 2.113
            return PlayableRange(url: source, startSec: start, endSec: start + 1.7)
        }
    default:
        throw HarnessError.usage("seams variant must be uniform|mixed")
    }

    let engine = PlayerEngine()
    var tap: FrameTap?
    engine.itemConfigurator = { item in
        tap = FrameTap(item: item, decode: false)
        tap?.start()
    }
    try await engine.load(ranges: ranges, smoothCutAudio: true)
    guard let tap, let built = engine.built else {
        throw HarnessError.internalFailure("tap/built missing after load")
    }

    // Per-seam incoming frame duration, for the ≤1-frame-duration gate.
    var incomingFrameDuration: [Double] = []
    let cache = AssetCache()
    for segment in built.segments.dropFirst() {
        let loaded = try await cache.loaded(for: segment.range.url)
        incomingFrameDuration.append(
            loaded.metadata.video.map { MediaTime.seconds($0.minFrameDuration) } ?? 1.0 / 30.0)
    }

    engine.player.isMuted = true
    engine.play()
    let total = MediaTime.seconds(built.duration)
    print("seams(\(variant)): playing \(fmt(total, 1))s across \(built.segments.count) segments…")
    _ = await awaitSample(tap, timeoutSec: total + 15) { $0.itemTimeSec >= total - 0.15 }
    engine.pause()
    tap.stop()

    let samples = tap.snapshot().sorted { $0.itemTimeSec < $1.itemTimeSec }
    var seamGapRatios: [Double] = []
    var seamGapsMs: [Double] = []
    var report: [String] = []
    for (k, segment) in built.segments.dropFirst().enumerated() {
        let boundary = MediaTime.seconds(segment.compositionStart)
        guard
            let before = samples.last(where: { $0.itemTimeSec < boundary - 0.001 }),
            let after = samples.first(where: { $0.itemTimeSec >= boundary - 0.001 })
        else {
            report.append("- seam \(k + 1) @ \(fmt(boundary)): NO SAMPLES (measurement hole)")
            continue
        }
        let gap = after.hostTime - before.hostTime
        let ratio = gap / incomingFrameDuration[k]
        seamGapRatios.append(ratio)
        seamGapsMs.append(gap)
    }

    let p95Ratio = Stats.percentile(seamGapRatios, 95)
    let pass = p95Ratio <= 1.0 + 0.15  // ±poll-resolution allowance is NOT applied to the gate; see report
    let strictPass = p95Ratio <= 1.0
    report.append("**seams(\(variant))** — \(built.segments.count - 1) seams over \(fmt(total, 1))s")
    report.append("- inter-frame delivery gap at seams: \(Stats.summary(seamGapsMs))")
    report.append("- gap / incoming-frame-duration: p50=\(fmt(Stats.percentile(seamGapRatios, 50))) p95=\(fmt(p95Ratio)) max=\(fmt(seamGapRatios.max() ?? .nan))")
    report.append("- GATE (p95 ratio ≤ 1.0): \(strictPass ? "PASS" : (pass ? "MARGINAL (within 2kHz poll resolution)" : "FAIL"))")
    env.report("seams-\(variant)", report)
}
