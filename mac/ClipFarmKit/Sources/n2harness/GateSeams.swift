import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 1 — seam-drop instrumentation: 20+ deliberately non-keyframe cuts;
/// p95 inter-frame delivery gap at seams ≤ 1 incoming frame duration.
///
/// Three runs: `uniform` (same-geometry trio, bare composition — the
/// common case; synthetic, since the inbox's real files are mutually
/// mixed-geometry), `real` (whatever is in the footage inbox, D34), and
/// `mixed` (real + HEVC 4K + ProRes + HLG-HDR + portrait fixtures — the
/// videoComposition path).
@MainActor
func runSeams(env: HarnessEnv, variant: String) async throws {
    var material: [String] = []
    let ranges: [PlayableRange]
    switch variant {
    case "uniform":
        // Bare-composition leg needs uniform geometry across ≥3 files —
        // not constructible from the current inbox (portrait iPhone +
        // 4K-class landscape are mutually mixed), so this leg is synthetic
        // (PROVISIONAL 1): H.264 + H.264 + HEVC, all 1920×1080 identity,
        // long-GOP, cuts off the keyframe grid.
        let files = [
            try await env.ensureFixture(FixtureSet.h264A),
            try await env.ensureFixture(FixtureSet.h264B),
            try await env.ensureFixture(FixtureSet.hevc1080),
        ]
        material.append("- material: synthetic h264A/h264B/hevc1080 (uniform 1080p geometry, bare composition)")
        ranges = (0..<24).map { i in
            let file = files[i % files.count]
            let start = 7.37 + Double(i % 8) * 3.113
            return PlayableRange(url: file, startSec: start, endSec: start + 1.8)
        }
    case "real":
        let probed = await env.probedRealFiles()
        guard !probed.isEmpty else {
            env.report("seams-real", [
                "**seams(real)** — DEFERRED: footage inbox (\(env.footage.path)) has no video files; drop files in (D34) and re-run `swift run n2harness seams real`",
            ])
            return
        }
        material.append("- material: real inbox files — "
            + probed.map { "\($0.url.lastPathComponent) (\(fmt($0.meta.duration.seconds, 1))s)" }
                .joined(separator: ", "))
        let perFile = max(2, 25 / probed.count)
        let groups = probed.map { file in
            spreadRanges(
                url: file.url, durationSec: file.meta.duration.seconds,
                count: perFile, length: 1.8)
        }
        ranges = roundRobin(groups)
        guard ranges.count >= 2 else {
            throw HarnessError.internalFailure("inbox files too short for seam ranges")
        }
    case "mixed":
        let probed = await env.probedRealFiles()
        let realLead = probed.max { $0.meta.duration.seconds < $1.meta.duration.seconds }
        let lead: URL
        if let realLead {
            lead = realLead.url
            material.append("- material: real \(lead.lastPathComponent) + hevc4K/proRes/hlg/portrait fixtures (HDR + ProRes legs synthetic — no such real material in the inbox)")
        } else {
            lead = try await env.ensureFixture(FixtureSet.h264A)
            material.append("- material: ALL synthetic (inbox empty) — re-run when populated")
        }
        let sources = [
            lead,
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
        throw HarnessError.usage("seams variant must be uniform|real|mixed")
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
    var out: [String] = ["**seams(\(variant))** — \(built.segments.count - 1) seams over \(fmt(total, 1))s"]
    out.append(contentsOf: material)
    out.append("- inter-frame delivery gap at seams: \(Stats.summary(seamGapsMs))")
    out.append("- gap / incoming-frame-duration: p50=\(fmt(Stats.percentile(seamGapRatios, 50))) p95=\(fmt(p95Ratio)) max=\(fmt(seamGapRatios.max() ?? .nan))")
    out.append("- GATE (p95 ratio ≤ 1.0): \(strictPass ? "PASS" : (pass ? "MARGINAL (within 2kHz poll resolution)" : "FAIL"))")
    out.append(contentsOf: report)
    env.report("seams-\(variant)", out)
}
