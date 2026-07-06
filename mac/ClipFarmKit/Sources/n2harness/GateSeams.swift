import AVFoundation
import CFMedia
import CoreMedia
import Foundation
import QuartzCore

/// Gate 1 — seam-drop instrumentation: 20+ deliberately non-keyframe cuts;
/// p95 inter-frame delivery gap at seams ≤ 1 frame duration of the
/// incoming segment.
///
/// Denominator note (VFR): `minFrameDuration` is the right basis for
/// frame-precision math (D17) but the wrong basis for *cadence* — on VFR
/// files (e.g. a "120 fps" timebase whose real durations are 2–5 ticks)
/// it makes seamless playback read as a FAIL by construction. Each seam
/// therefore reports TWO ratios: vs `minFrameDuration` (the gate list's
/// literal wording) and vs the incoming segment's measured median
/// delivery gap (decode-level, self-calibrating, VFR-immune). Both go to
/// Lillian; neither threshold is relaxed here.
///
/// Mitigation A/B (plan §4/N2: "if seams drop, A/B Apple's documented
/// mitigation — two alternating video tracks in one composition — before
/// anything custom"): every variant runs twice, single-track (the §2.5
/// rule-1 engine path) vs alternating-tracks (spike-local builder), so
/// the numbers decide whether rule 1 needs amending.
///
/// Variants: `uniform` (same-geometry synthetic trio, bare composition),
/// `real` (footage-inbox files, D34), `mixed` (real + HEVC-4K / ProRes /
/// HLG-HDR / portrait fixtures), `solo` (one self-splice leg per inbox
/// file — isolates per-file decoder behavior from cross-format seams).
@MainActor
func runSeams(env: HarnessEnv, variant: String) async throws {
    switch variant {
    case "uniform":
        let files = [
            try await env.ensureFixture(FixtureSet.h264A),
            try await env.ensureFixture(FixtureSet.h264B),
            try await env.ensureFixture(FixtureSet.hevc1080),
        ]
        let ranges = (0..<24).map { i in
            let file = files[i % files.count]
            let start = 7.37 + Double(i % 8) * 3.113
            return PlayableRange(url: file, startSec: start, endSec: start + 1.8)
        }
        try await seamAB(
            env: env, gate: "seams-uniform",
            material: "synthetic h264A/h264B/hevc1080 (uniform 1080p geometry, bare composition)",
            ranges: ranges)
    case "real":
        let probed = await env.probedRealFiles()
        guard !probed.isEmpty else {
            env.report("seams-real", [
                "**seams(real)** — DEFERRED: footage inbox (\(env.footage.path)) has no video files; drop files in (D34) and re-run `swift run n2harness seams real`",
            ])
            return
        }
        let material = "real inbox files — "
            + probed.map { "\($0.url.lastPathComponent) (\(fmt($0.meta.duration.seconds, 1))s)" }
                .joined(separator: ", ")
        let perFile = max(2, 25 / probed.count)
        let groups = probed.map { file in
            spreadRanges(
                url: file.url, durationSec: file.meta.duration.seconds,
                count: perFile, length: 1.8)
        }
        let ranges = roundRobin(groups)
        guard ranges.count >= 2 else {
            throw HarnessError.internalFailure("inbox files too short for seam ranges")
        }
        try await seamAB(env: env, gate: "seams-real", material: material, ranges: ranges)
    case "mixed":
        let probed = await env.probedRealFiles()
        let realLead = probed.max { $0.meta.duration.seconds < $1.meta.duration.seconds }
        let lead: URL
        let material: String
        if let realLead {
            lead = realLead.url
            material = "real \(lead.lastPathComponent) + hevc4K/proRes/hlg/portrait fixtures (HDR + ProRes legs synthetic — no such real material in the inbox)"
        } else {
            lead = try await env.ensureFixture(FixtureSet.h264A)
            material = "ALL synthetic (inbox empty) — re-run when populated"
        }
        let sources = [
            lead,
            try await env.ensureFixture(FixtureSet.hevc4K),
            try await env.ensureFixture(FixtureSet.proRes),
            try await env.ensureFixture(FixtureSet.hlg),
            try await env.ensureFixture(FixtureSet.portrait),
        ]
        let ranges = (0..<22).map { i in
            let source = sources[i % sources.count]
            let start = 1.777 + Double(i % 5) * 2.113
            return PlayableRange(url: source, startSec: start, endSec: start + 1.7)
        }
        try await seamAB(env: env, gate: "seams-mixed", material: material, ranges: ranges)
    case "solo":
        let probed = await env.probedRealFiles()
        guard !probed.isEmpty else {
            env.report("seams-solo", ["**seams(solo)** — DEFERRED: footage inbox empty"])
            return
        }
        for file in probed {
            let name = file.url.deletingPathExtension().lastPathComponent
            let ranges = spreadRanges(
                url: file.url, durationSec: file.meta.duration.seconds,
                count: 12, length: 1.3)
            guard ranges.count >= 2 else { continue }
            let result = try await measureSeamRun(ranges: ranges, twoTrack: false)
            env.report("seams-solo", [
                "**seams(solo · \(name))** — self-splice, \(ranges.count - 1) same-file seams",
            ] + result.lines)
        }
    default:
        throw HarnessError.usage("seams variant must be uniform|real|mixed|solo")
    }
}

/// One variant, both strategies, one report.
@MainActor
private func seamAB(
    env: HarnessEnv, gate: String, material: String, ranges: [PlayableRange]
) async throws {
    let single = try await measureSeamRun(ranges: ranges, twoTrack: false)
    let double = try await measureSeamRun(ranges: ranges, twoTrack: true)
    var report: [String] = ["**\(gate)** — \(ranges.count - 1) seams"]
    report.append("- material: \(material)")
    report.append("- SINGLE track pair (§2.5 rule 1 — the engine path):")
    report.append(contentsOf: single.lines.map { "  \($0)" })
    report.append("- ALTERNATING track pairs (Apple's seam mitigation, spike-local):")
    report.append(contentsOf: double.lines.map { "  \($0)" })
    let winner = double.p95CadenceRatio < single.p95CadenceRatio ? "alternating" : "single"
    report.append("- lower p95 cadence-ratio: \(winner) track layout")
    env.report(gate, report)
}

private struct SeamRunResult {
    let lines: [String]
    let p95CadenceRatio: Double
}

/// Build (single or alternating tracks) → play through muted → FrameTap →
/// per-seam gap vs (a) minFrameDuration and (b) the incoming segment's
/// own measured median delivery gap.
@MainActor
private func measureSeamRun(
    ranges: [PlayableRange], twoTrack: Bool
) async throws -> SeamRunResult {
    let cache = AssetCache()
    let built: CompositionBuildResult
    if twoTrack {
        built = try await buildAlternatingTracks(ranges: ranges, cache: cache)
    } else {
        built = try await CompositionBuilder(assetCache: cache).build(ranges: ranges)
    }

    let player = AVPlayer()
    player.automaticallyWaitsToMinimizeStalling = false
    player.isMuted = true
    let item = built.makePlayerItem()
    let tap = FrameTap(item: item, decode: false)
    tap.start()
    player.replaceCurrentItem(with: item)
    player.play()
    let total = MediaTime.seconds(built.duration)
    _ = await awaitSample(tap, timeoutSec: total + 15) { $0.itemTimeSec >= total - 0.15 }
    player.pause()
    tap.stop()

    var incomingFrameDuration: [Double] = []
    for segment in built.segments.dropFirst() {
        let loaded = try await cache.loaded(for: segment.range.url)
        incomingFrameDuration.append(
            loaded.metadata.video.map { MediaTime.seconds($0.minFrameDuration) } ?? 1.0 / 30.0)
    }

    let samples = tap.snapshot().sorted { $0.itemTimeSec < $1.itemTimeSec }
    var gapsMs: [Double] = []
    var minDurRatios: [Double] = []
    var cadenceRatios: [Double] = []
    var holes = 0
    for (k, segment) in built.segments.dropFirst().enumerated() {
        let boundary = MediaTime.seconds(segment.compositionStart)
        let segmentEnd = MediaTime.seconds(segment.compositionEnd)
        guard
            let before = samples.last(where: { $0.itemTimeSec < boundary - 0.001 }),
            let after = samples.first(where: { $0.itemTimeSec >= boundary - 0.001 })
        else {
            holes += 1
            continue
        }
        let gap = after.hostTime - before.hostTime
        gapsMs.append(gap)
        minDurRatios.append(gap / incomingFrameDuration[k])
        // Reference cadence: median inter-delivery gap strictly inside the
        // incoming segment (excludes the seam itself).
        let inside = samples.filter {
            $0.itemTimeSec > boundary + 0.001 && $0.itemTimeSec < segmentEnd - 0.001
        }
        let interior = zip(inside, inside.dropFirst())
            .map { $1.hostTime - $0.hostTime }
            .filter { $0 > 0.0005 }
        let cadence = median(interior) ?? incomingFrameDuration[k]
        cadenceRatios.append(gap / cadence)
    }

    let p95MinDur = Stats.percentile(minDurRatios, 95)
    let p95Cadence = Stats.percentile(cadenceRatios, 95)
    var lines: [String] = []
    lines.append("- inter-frame delivery gap at seams: \(Stats.summary(gapsMs))\(holes > 0 ? "  (holes: \(holes))" : "")")
    lines.append("- ratio vs minFrameDuration (gate-list wording; unfair on VFR): p50=\(fmt(Stats.percentile(minDurRatios, 50))) p95=\(fmt(p95MinDur)) max=\(fmt(minDurRatios.max() ?? .nan)) → \(p95MinDur <= 1.0 ? "PASS" : "FAIL")")
    lines.append("- ratio vs measured segment cadence (VFR-fair): p50=\(fmt(Stats.percentile(cadenceRatios, 50))) p95=\(fmt(p95Cadence)) max=\(fmt(cadenceRatios.max() ?? .nan)) → \(p95Cadence <= 1.0 ? "PASS" : "FAIL")")
    return SeamRunResult(lines: lines, p95CadenceRatio: p95Cadence)
}

private func median(_ values: [Double]) -> Double? {
    guard !values.isEmpty else { return nil }
    let sorted = values.sorted()
    return sorted[sorted.count / 2]
}

/// Apple's documented seam mitigation: segments alternate between two
/// video (and audio) composition tracks so the incoming segment's decoder
/// pre-rolls while the outgoing one plays. Spike-local — if the A/B says
/// this wins, §2.5 rule 1 gets a Lillian-adjudicated amendment; the
/// engine keeps the single-track contract until then.
@MainActor
private func buildAlternatingTracks(
    ranges: [PlayableRange], cache: AssetCache
) async throws -> CompositionBuildResult {
    guard !ranges.isEmpty else { throw CompositionBuildError.emptyRangeList }
    let composition = AVMutableComposition()
    let videoTracks = [
        composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!,
        composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!,
    ]
    let audioTracks = [
        composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!,
        composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!,
    ]

    var cursor = CMTime.zero
    var segments: [BuiltSegment] = []
    var infos: [VideoTrackInfo] = []
    var lanes: [Int] = []
    for (index, range) in ranges.enumerated() {
        let loaded = try await cache.loaded(for: range.url)
        guard let sourceVideo = loaded.videoTrack, let info = loaded.metadata.video else {
            throw CompositionBuildError.missingVideoTrack(url: range.url)
        }
        let requested = CMTimeRange(
            start: MediaTime.time(range.startSec), end: MediaTime.time(range.endSec))
        guard let clamped = CompositionPlanner.clampedInsertRange(
            requested: requested,
            videoRange: info.timeRange,
            audioRange: loaded.audioTrack == nil ? nil : loaded.metadata.audioTimeRange)
        else {
            throw CompositionBuildError.emptyClampedRange(
                url: range.url, startSec: range.startSec, endSec: range.endSec)
        }
        let lane = index % 2
        try videoTracks[lane].insertTimeRange(clamped, of: sourceVideo, at: cursor)
        if let sourceAudio = loaded.audioTrack {
            try audioTracks[lane].insertTimeRange(clamped, of: sourceAudio, at: cursor)
        }
        segments.append(BuiltSegment(
            range: range, compositionStart: cursor,
            duration: clamped.duration, sourceStart: clamped.start))
        infos.append(info)
        lanes.append(lane)
        cursor = cursor + clamped.duration
    }

    // Per-segment instructions selecting the active lane (required with
    // two tracks even for uniform geometry — otherwise both render).
    let canvas = infos[0].orientedSize
    let enforceSDR = CompositionPlanner.dynamicRangesMix(infos)
    let instructions: [AVVideoCompositionInstruction] = segments.indices.map { i in
        var layer = AVVideoCompositionLayerInstruction.Configuration(
            assetTrack: videoTracks[lanes[i]])
        layer.setTransform(
            CompositionPlanner.fitTransform(
                naturalSize: infos[i].naturalSize,
                preferredTransform: infos[i].preferredTransform,
                canvas: canvas),
            at: segments[i].compositionStart)
        return AVVideoCompositionInstruction(configuration: .init(
            layerInstructions: [AVVideoCompositionLayerInstruction(configuration: layer)],
            timeRange: CMTimeRange(
                start: segments[i].compositionStart, duration: segments[i].duration)
        ))
    }
    let videoComposition = AVVideoComposition(configuration: .init(
        colorPrimaries: enforceSDR ? String(kCMFormatDescriptionColorPrimaries_ITU_R_709_2) : nil,
        colorTransferFunction: enforceSDR ? String(kCMFormatDescriptionTransferFunction_ITU_R_709_2) : nil,
        colorYCbCrMatrix: enforceSDR ? String(kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2) : nil,
        frameDuration: infos
            .map(\.minFrameDuration)
            .filter { $0.isNumeric && $0 > .zero }
            .min() ?? CMTime(value: 1, timescale: 30),
        instructions: instructions,
        renderSize: canvas
    ))

    return CompositionBuildResult(
        composition: composition.copy() as! AVComposition,
        videoComposition: videoComposition,
        audioMix: nil,
        segments: segments)
}
