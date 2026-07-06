import AVFoundation
import CFMedia
import CFMediaTestSupport
import CoreMedia
import Foundation

/// Gate 3 — mixed-rotation render probe (D32): portrait + landscape in one
/// composition renders correctly via videoComposition; record what
/// passthrough export does with it.
///
/// Leg 1 always runs with the PORTRAIT FIXTURE (its self-identifying gray
/// is the only content the strict numeric checks can assert against);
/// leg 2 runs real-portrait + real-landscape whenever the inbox has both
/// orientations (content is arbitrary → pillars-black + content-present).
@MainActor
func runRotation(env: HarnessEnv) async throws {
    let probed = await env.probedRealFiles()
    var report: [String] = ["**rotation** — mixed-geometry render probe (D32: conditional videoComposition, pillarbox default)"]

    func isLandscape(_ meta: SourceMetadata) -> Bool {
        guard let video = meta.video else { return false }
        return video.orientedSize.width > video.orientedSize.height
    }
    let realLandscape = probed.filter { isLandscape($0.meta) }
        .max { $0.meta.duration.seconds < $1.meta.duration.seconds }
    let realPortrait = probed.filter { $0.meta.video != nil && !isLandscape($0.meta) }
        .max { $0.meta.duration.seconds < $1.meta.duration.seconds }

    // --- Leg 1: strict content assertions (portrait fixture) ---
    let landscapeURL: URL
    let landscapeStart: Double
    if let realLandscape {
        landscapeURL = realLandscape.url
        landscapeStart = realLandscape.meta.duration.seconds * 0.2 + 0.37
        report.append("- leg 1 material: real \(landscapeURL.lastPathComponent) + synthetic portrait fixture")
    } else {
        landscapeURL = try await env.ensureFixture(FixtureSet.h264A)
        landscapeStart = 10.0
        report.append("- leg 1 material: synthetic h264A + portrait fixture (no real landscape in inbox)")
    }
    let portraitFixture = try await env.ensureFixture(FixtureSet.portrait)
    let leg1 = try await rotationRenderProbe(
        env: env,
        ranges: [
            PlayableRange(url: landscapeURL, startSec: landscapeStart, endSec: landscapeStart + 2.0),
            PlayableRange(url: portraitFixture, startSec: 5.0, endSec: 7.0),
        ],
        expectFixtureGray: true,
        pngName: "rotation-leg1-portrait-rendered")
    report.append(contentsOf: leg1.lines)
    report.append("- GATE (renders correctly via videoComposition): \(leg1.pass ? "PASS" : "FAIL")")

    // --- Leg 2: real + real (both orientations from the inbox) ---
    var passthroughSource = leg1.built
    if let realLandscape, let realPortrait {
        report.append("- leg 2 material: real \(realLandscape.url.lastPathComponent) + real \(realPortrait.url.lastPathComponent)")
        let dl = realLandscape.meta.duration.seconds
        let dp = realPortrait.meta.duration.seconds
        let leg2 = try await rotationRenderProbe(
            env: env,
            ranges: [
                PlayableRange(url: realLandscape.url, startSec: dl * 0.3 + 0.11, endSec: dl * 0.3 + 2.11),
                PlayableRange(url: realPortrait.url, startSec: dp * 0.3 + 0.13, endSec: dp * 0.3 + 2.13),
            ],
            expectFixtureGray: false,
            pngName: "rotation-leg2-real-portrait-rendered")
        report.append(contentsOf: leg2.lines)
        report.append("- leg 2 verdict (pillars black + content present, informational): \(leg2.pass ? "PASS" : "FAIL")")
        passthroughSource = leg2.built
    } else {
        report.append("- leg 2 (real + real): SKIPPED — inbox lacks one orientation; re-run when both exist")
    }

    // Passthrough-export leg: record what passthrough does with mixed
    // rotation (it ignores videoComposition transforms — D32 rationale).
    let outURL = env.workdir.appendingPathComponent("export/rotation-passthrough.mov")
    try? FileManager.default.removeItem(at: outURL)
    if let session = AVAssetExportSession(
        asset: passthroughSource.composition, presetName: AVAssetExportPresetPassthrough) {
        do {
            try await session.export(to: outURL, as: .mov)
            let meta = try await MetadataProbe.probe(url: outURL)
            report.append("- passthrough export of the mixed comp: SUCCEEDED — output video track naturalSize=\(Int(meta.video?.naturalSize.width ?? 0))×\(Int(meta.video?.naturalSize.height ?? 0)), transform=\(meta.video.map { $0.preferredTransform == .identity ? "identity" : "non-identity" } ?? "?") → per-segment transforms are NOT representable; file left at export/rotation-passthrough.mov for the watch session")
        } catch {
            report.append("- passthrough export of the mixed comp: FAILED (\(error)) — recorded for N12's Lossless-eligibility rule (mixed geometry must route to re-encode)")
        }
    }
    env.report("rotation", report)
}

/// Build → engine-load → probe one delivered frame mid-portrait-segment.
/// Canvas expectation = the first segment's oriented size (the builder's
/// D32 default; the engine exposes no renderSize override at N2).
@MainActor
private func rotationRenderProbe(
    env: HarnessEnv, ranges: [PlayableRange], expectFixtureGray: Bool, pngName: String
) async throws -> (lines: [String], pass: Bool, built: CompositionBuildResult) {
    var lines: [String] = []
    let cache = AssetCache()
    let built = try await CompositionBuilder(assetCache: cache).build(ranges: ranges)
    lines.append("  - videoComposition attached: \(built.videoComposition != nil ? "yes (D32 conditional path)" : "NO — BUG")")
    guard built.videoComposition != nil else { return (lines, false, built) }

    guard let firstVideo = (try await cache.loaded(for: ranges[0].url)).metadata.video,
          let portraitVideo = (try await cache.loaded(for: ranges[1].url)).metadata.video
    else {
        lines.append("  - probe failed: missing video track info")
        return (lines, false, built)
    }
    let canvas = firstVideo.orientedSize

    let engine = PlayerEngine()
    try await engine.load(ranges: ranges)
    engine.player.isMuted = true
    let portraitMid = MediaTime.seconds(built.segments[1].compositionStart)
        + MediaTime.seconds(built.segments[1].duration) / 2
    guard let pixelBuffer = try await capturePixelBuffer(engine: engine, at: portraitMid) else {
        lines.append("  - no frame delivered for probing")
        return (lines, false, built)
    }
    try? PixelProbe.writePNG(
        pixelBuffer, to: env.workdir.appendingPathComponent("frames/\(pngName).png"))

    let width = CVPixelBufferGetWidth(pixelBuffer)
    let height = CVPixelBufferGetHeight(pixelBuffer)
    let canvasOK = width == Int(canvas.width) && height == Int(canvas.height)
    lines.append("  - rendered canvas: \(width)×\(height) (expected \(Int(canvas.width))×\(Int(canvas.height)) — first segment's oriented size)")

    // Pillar geometry from the actual fit: portrait oriented size scaled
    // into the canvas, centered.
    let oriented = portraitVideo.orientedSize
    let scale = min(canvas.width / oriented.width, canvas.height / oriented.height)
    let contentLeft = (canvas.width - oriented.width * scale) / 2 / canvas.width  // normalized
    guard contentLeft > 0.05 else {
        lines.append("  - portrait content nearly fills the canvas (content left edge at \(fmt(contentLeft, 3))) — pillar probe not meaningful for this pair")
        return (lines, canvasOK, built)
    }
    let pillarWidth = contentLeft - 0.03
    let leftPillar = PixelProbe.meanRGB(
        in: pixelBuffer, rect: CGRect(x: 0.01, y: 0.3, width: pillarWidth, height: 0.4))
    let rightPillar = PixelProbe.meanRGB(
        in: pixelBuffer, rect: CGRect(x: 0.99 - pillarWidth, y: 0.3, width: pillarWidth, height: 0.4))
    let center = PixelProbe.meanRGB(
        in: pixelBuffer, rect: CGRect(x: 0.42, y: 0.4, width: 0.16, height: 0.2))
    let pillarsBlack = (leftPillar.r + leftPillar.g + leftPillar.b) / 3 < 25
        && (rightPillar.r + rightPillar.g + rightPillar.b) / 3 < 25
    let centerMean = (center.r + center.g + center.b) / 3
    let contentOK = expectFixtureGray
        ? abs(center.r - 118) < 25 && abs(center.g - 118) < 25
        : centerMean > 20
    lines.append("  - pillar bands black: \(pillarsBlack ? "yes" : "NO") (L=\(fmt(leftPillar.r, 0)) R=\(fmt(rightPillar.r, 0)); content left edge \(fmt(contentLeft, 3)))")
    lines.append(expectFixtureGray
        ? "  - pillarboxed content is fixture gray: \(contentOK ? "yes" : "NO") (center r=\(fmt(center.r, 0)) g=\(fmt(center.g, 0)) b=\(fmt(center.b, 0)))"
        : "  - pillarboxed content present (non-black): \(contentOK ? "yes" : "NO") (center mean=\(fmt(centerMean, 0)))")
    return (lines, canvasOK && pillarsBlack && contentOK, built)
}

/// Gate 4 — HDR↔SDR seam probe (D29): alternating HLG/SDR segments, with
/// and without explicit videoComposition color properties, pixel-probed in
/// preview AND a Standard-tier export. Gate: no visible shift; preview ==
/// export.
@MainActor
func runHDRSeam(env: HarnessEnv) async throws {
    let sdr = try await env.ensureFixture(FixtureSet.h264A)   // gray 118, BT.709
    let hdr = try await env.ensureFixture(FixtureSet.hlg)     // gray 118 nominal, HLG/BT.2020
    let ranges = [
        PlayableRange(url: sdr, startSec: 5.0, endSec: 7.0),
        PlayableRange(url: hdr, startSec: 5.0, endSec: 7.0),
        PlayableRange(url: sdr, startSec: 20.0, endSec: 22.0),
        PlayableRange(url: hdr, startSec: 20.0, endSec: 22.0),
    ]
    let builder = CompositionBuilder(assetCache: AssetCache())
    let managed = try await builder.build(ranges: ranges)  // color-managed (D29 enforced)
    var report: [String] = ["**hdrseam** — SDR(709) and HLG(2020) segments, matched nominal content (gray 118)"]
    report.append("- material: synthetic SDR + synthetic HLG (the inbox holds no HDR material — PROVISIONAL 1); re-run with a real iPhone HDR clip in the inbox at the watch session if one exists")
    report.append("- builder attached videoComposition with 709 color properties: \(managed.videoComposition?.colorPrimaries != nil ? "yes" : "NO — BUG")")

    // Preview probe, managed path.
    let managedMeans = try await segmentMeans(built: managed, label: "preview-managed", env: env)
    report.append("- preview WITH color properties (mean gray per segment): \(managedMeans.map { fmt($0, 1) }.joined(separator: " | "))")

    // Control: bare composition (no videoComposition) — what D29 warns about.
    let bare = try await bareComposition(ranges: ranges)
    let bareMeans = try await segmentMeans(built: bare, label: "preview-bare", env: env)
    report.append("- preview WITHOUT color properties (control): \(bareMeans.map { fmt($0, 1) }.joined(separator: " | "))")

    // Standard-tier export of the managed path (re-encode, SDR target).
    let outURL = env.workdir.appendingPathComponent("export/hdrseam-standard.mp4")
    try? FileManager.default.removeItem(at: outURL)
    guard let session = AVAssetExportSession(
        asset: managed.composition, presetName: AVAssetExportPreset1920x1080) else {
        throw HarnessError.internalFailure("no export session")
    }
    session.videoComposition = managed.videoComposition
    try await session.export(to: outURL, as: .mp4)
    let exportMeans = try await fileSegmentMeans(
        url: outURL, boundaries: managed.segments.map { MediaTime.seconds($0.compositionStart) })
    report.append("- Standard-tier export (same videoComposition): \(exportMeans.map { fmt($0, 1) }.joined(separator: " | "))")

    // Numbers → verdicts.
    func maxSeamShift(_ means: [Double]) -> Double {
        guard means.count >= 2 else { return .nan }
        return zip(means, means.dropFirst()).map { abs($0 - $1) }.max() ?? .nan
    }
    let managedShift = maxSeamShift(managedMeans)
    let bareShift = maxSeamShift(bareMeans)
    let previewExportDelta = zip(managedMeans, exportMeans).map { abs($0 - $1) }.max() ?? .nan
    report.append("- max cross-seam shift: managed=\(fmt(managedShift, 1))/255, bare control=\(fmt(bareShift, 1))/255")
    report.append("- max |preview − export| per segment: \(fmt(previewExportDelta, 1))/255")
    let pass = managedShift <= 8 && previewExportDelta <= 6
    report.append("- GATE (no visible shift ∧ preview == export): \(pass ? "PASS" : "FAIL") (thresholds 8/255 seam, 6/255 preview-vs-export; PNGs in frames/ for the watch session)")
    env.report("hdrseam", report)
}

// MARK: - shared probing helpers

@MainActor
private func segmentMeans(
    built: CompositionBuildResult, label: String, env: HarnessEnv
) async throws -> [Double] {
    // Probe the built item directly (rebuilding through the engine would
    // re-decide the videoComposition — this gate A/Bs exactly that).
    let player = AVPlayer()
    player.automaticallyWaitsToMinimizeStalling = false
    player.isMuted = true
    let item = built.makePlayerItem()
    player.replaceCurrentItem(with: item)

    var means: [Double] = []
    for (i, segment) in built.segments.enumerated() {
        let mid = MediaTime.seconds(segment.compositionStart) + MediaTime.seconds(segment.duration) / 2
        guard let pixelBuffer = try await capturePixelBufferDirect(item: item, at: mid) else {
            means.append(.nan)
            continue
        }
        let mean = PixelProbe.meanRGB(in: pixelBuffer)
        means.append((mean.r + mean.g + mean.b) / 3)
        try? PixelProbe.writePNG(
            pixelBuffer,
            to: env.workdir.appendingPathComponent("frames/\(label)-seg\(i).png"))
    }
    return means
}

/// Decode segment-middle frames of an exported FILE via AVAssetReader.
private func fileSegmentMeans(url: URL, boundaries: [Double]) async throws -> [Double] {
    let asset = AVURLAsset(url: url)
    let duration = try await asset.load(.duration).seconds
    var ends = Array(boundaries.dropFirst())
    ends.append(duration)
    let mids = zip(boundaries, ends).map { ($0 + $1) / 2 }

    guard let track = try await asset.loadTracks(withMediaType: .video).first else {
        throw HarnessError.internalFailure("export has no video track")
    }
    var means: [Double] = []
    for mid in mids {
        let reader = try AVAssetReader(asset: asset)
        reader.timeRange = CMTimeRange(
            start: CMTime(seconds: mid, preferredTimescale: 600),
            duration: CMTime(seconds: 0.5, preferredTimescale: 600))
        let output = AVAssetReaderTrackOutput(
            track: track,
            outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
        reader.add(output)
        reader.startReading()
        if let sample = output.copyNextSampleBuffer(),
           let pixelBuffer = CMSampleBufferGetImageBuffer(sample) {
            let mean = PixelProbe.meanRGB(in: pixelBuffer)
            means.append((mean.r + mean.g + mean.b) / 3)
        } else {
            means.append(.nan)
        }
        reader.cancelReading()
    }
    return means
}

/// A bare (no videoComposition, no audio mix) control composition.
@MainActor
private func bareComposition(ranges: [PlayableRange]) async throws -> CompositionBuildResult {
    let cache = AssetCache()
    let composition = AVMutableComposition()
    let video = composition.addMutableTrack(
        withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
    let audio = composition.addMutableTrack(
        withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
    var cursor = CMTime.zero
    var segments: [BuiltSegment] = []
    for range in ranges {
        let loaded = try await cache.loaded(for: range.url)
        let tr = CMTimeRange(
            start: MediaTime.time(range.startSec), end: MediaTime.time(range.endSec))
        try video.insertTimeRange(tr, of: loaded.videoTrack!, at: cursor)
        if let audioTrack = loaded.audioTrack {
            try audio.insertTimeRange(tr, of: audioTrack, at: cursor)
        }
        segments.append(BuiltSegment(
            range: range, compositionStart: cursor, duration: tr.duration, sourceStart: tr.start))
        cursor = cursor + tr.duration
    }
    return CompositionBuildResult(
        composition: composition.copy() as! AVComposition,
        videoComposition: nil, audioMix: nil, segments: segments)
}

/// One-shot pixel-buffer capture helpers.
@MainActor
func capturePixelBuffer(engine: PlayerEngine, at seconds: Double) async throws -> CVPixelBuffer? {
    guard let item = engine.player.currentItem else { return nil }
    return try await capturePixelBufferDirect(item: item, at: seconds)
}

@MainActor
func capturePixelBufferDirect(item: AVPlayerItem, at seconds: Double) async throws -> CVPixelBuffer? {
    let output = AVPlayerItemVideoOutput(pixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
    ])
    item.add(output)
    defer { item.remove(output) }
    await item.seek(
        to: MediaTime.time(seconds), toleranceBefore: .zero, toleranceAfter: .zero)
    let deadline = Date().addingTimeInterval(3)
    let target = MediaTime.time(seconds)
    while Date() < deadline {
        if output.hasNewPixelBuffer(forItemTime: target),
           let pixelBuffer = output.copyPixelBuffer(forItemTime: target, itemTimeForDisplay: nil) {
            return pixelBuffer
        }
        let now = item.currentTime()
        if output.hasNewPixelBuffer(forItemTime: now),
           let pixelBuffer = output.copyPixelBuffer(forItemTime: now, itemTimeForDisplay: nil) {
            return pixelBuffer
        }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return nil
}
