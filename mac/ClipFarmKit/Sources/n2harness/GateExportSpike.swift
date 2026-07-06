import AVFoundation
import CFMedia
import CFMediaTestSupport
import CoreMedia
import Foundation

/// Gate 9 — the half-day export mini-spike (finding 4). Spike code, NOT
/// production CFExport: its deliverable is three answers that choose N12's
/// architecture.
///   (a) Does passthrough export of a two-file H.264 composition with
///       non-keyframe cuts succeed at all — and does it author edit lists
///       or snap to sync samples?
///   (b) Do sequential AVAssetWriter sample-writing sessions edit out
///       lead-in frames for segments 2..N, or only the first?
///   (c) How do non-Apple demuxers read the result? (ffprobe/ffmpeg =
///       libavformat, VLC's demux path; QuickTime/Chrome eyeball happens
///       at the watch session on the files left in export/.)
///
/// ffmpeg/ffprobe here are measurement instruments run BY THE HARNESS ONLY
/// (probing outputs the way libav-based players will); the app-side
/// `FFmpegLocator` seam arrives at N3 and nothing here ships.
@MainActor
func runExportSpike(env: HarnessEnv, experiment: String) async throws {
    switch experiment {
    case "a": try await passthroughExperiment(env: env)
    case "b": try await sequentialWriterExperiment(env: env)
    case "c": try await elstABExperiment(env: env)
    case "all":
        try await passthroughExperiment(env: env)
        try await elstABExperiment(env: env)
        // (b) last: an unsupported second startSession may raise an ObjC
        // exception (uncatchable in Swift) — a crash here IS an answer, and
        // ordering keeps it from eating (a)/(c).
        try await sequentialWriterExperiment(env: env)
    default:
        throw HarnessError.usage("exportspike experiment must be a|b|c|all")
    }
}

// The two-file H.264 splice used by all three experiments. Fixture content
// is self-identifying, so frame exactness is checkable; keyframes sit 3s
// apart (kf90 @ 30fps) and every cut is deliberately mid-GOP.
private func spikeRanges(_ env: HarnessEnv) async throws -> [PlayableRange] {
    let a = try await env.ensureFixture(FixtureSet.h264A)
    let b = try await env.ensureFixture(FixtureSet.h264B)
    return [
        PlayableRange(url: a, startSec: 4.777, endSec: 7.911),   // inside GOP 1
        PlayableRange(url: b, startSec: 10.313, endSec: 13.007), // inside GOP 3
    ]
}

// MARK: - (a) passthrough at non-keyframe cuts

@MainActor
private func passthroughExperiment(env: HarnessEnv) async throws {
    let ranges = try await spikeRanges(env)
    let builder = CompositionBuilder(assetCache: AssetCache())
    let built = try await builder.build(ranges: ranges, smoothCutAudio: false)
    let outURL = env.workdir.appendingPathComponent("export/spike-a-passthrough.mov")
    try? FileManager.default.removeItem(at: outURL)

    var report: [String] = ["**exportspike (a)** — passthrough, two-file H.264, non-keyframe cuts"]
    guard let session = AVAssetExportSession(
        asset: built.composition, presetName: AVAssetExportPresetPassthrough) else {
        report.append("- AVAssetExportSession(passthrough) REFUSED the composition (nil session)")
        env.report("exportspike", report)
        return
    }
    do {
        try await session.export(to: outURL, as: .mov)
        report.append("- export SUCCEEDED (rdar://10421720 not hit on macOS 26)")
    } catch {
        report.append("- export FAILED: \(error) → N12 passthrough tier must gate on keyframe alignment")
        env.report("exportspike", report)
        return
    }

    report.append(contentsOf: try await analyzeSplice(
        url: outURL, built: built, env: env, label: "passthrough"))
    env.report("exportspike", report)
}

/// Shared output analysis: duration, elst authorship, Apple-side frame
/// exactness (self-identifying fixture frames), libav-side view.
@MainActor
private func analyzeSplice(
    url: URL, built: CompositionBuildResult, env: HarnessEnv, label: String
) async throws -> [String] {
    var lines: [String] = []
    let expectedDuration = MediaTime.seconds(built.duration)

    // Apple-side read.
    let asset = AVURLAsset(url: url)
    let avfDuration = try await asset.load(.duration).seconds
    lines.append("- duration: expected \(fmt(expectedDuration, 3))s, AVFoundation reads \(fmt(avfDuration, 3))s")

    // Edit lists straight from the container bytes.
    let edits = try MP4BoxParser.editLists(in: url)
    if edits.isEmpty {
        lines.append("- container: NO elst boxes (cuts snapped or re-muxed clean)")
    } else {
        for (i, elst) in edits.enumerated() {
            let entries = elst.map { "(dur \(fmt($0.segmentDurationSec, 3))s, mediaTime \(fmt($0.mediaTimeSec, 3))s)" }
            lines.append("- container: track \(i + 1) elst ×\(elst.count): \(entries.joined(separator: " "))")
        }
    }

    // Frame exactness, Apple-side (player honors edit lists).
    let item = AVPlayerItem(asset: asset)
    let player = AVPlayer(playerItem: item)
    player.isMuted = true
    var frameChecks: [String] = []
    for segment in built.segments {
        let expected = Int((segment.sourceStart.seconds * 30.0 + 1e-6).rounded(.down))
        let probeTime = MediaTime.seconds(segment.compositionStart) + 0.001
        let pixelBuffer = try await capturePixelBufferDirect(item: item, at: probeTime)
        let got = pixelBuffer.flatMap { PixelProbe.frameIndex(in: $0) }
        frameChecks.append("seg@\(fmt(probeTime, 2)) expect \(expected) got \(got.map(String.init) ?? "?")\(got == expected ? " ✓" : " ✗")")
    }
    lines.append("- Apple-side cut frames: \(frameChecks.joined(separator: "; "))")

    // libav-side view (VLC's demux path).
    let ffprobe = ffprobeOutput([
        "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=nb_read_frames,start_time,duration",
        "-of", "default=noprint_wrappers=1", url.path,
    ])
    let expectedFrames = built.segments.reduce(0) {
        $0 + Int((MediaTime.seconds($1.duration) * 30.0).rounded())
    }
    lines.append("- libav view (expected ~\(expectedFrames) frames if edits honored): \(ffprobe.replacingOccurrences(of: "\n", with: " "))")
    lines.append("- file kept: export/\(url.lastPathComponent) (QuickTime/VLC/Chrome eyeball at the watch session)")
    return lines
}

// MARK: - (b) sequential writer sessions

@MainActor
private func sequentialWriterExperiment(env: HarnessEnv) async throws {
    let ranges = try await spikeRanges(env)
    let outURL = env.workdir.appendingPathComponent("export/spike-b-hybridwriter.mov")
    try? FileManager.default.removeItem(at: outURL)
    var report: [String] = ["**exportspike (b)** — sequential AVAssetWriter sample-writing sessions (video passthrough)"]

    let cache = AssetCache()
    let loadedA = try await cache.loaded(for: ranges[0].url)
    guard let trackA = loadedA.videoTrack,
          let formatDesc = try await trackA.load(.formatDescriptions).first else {
        throw HarnessError.internalFailure("fixture A track/format missing")
    }

    let writer = try AVAssetWriter(outputURL: outURL, fileType: .mov)
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: nil, sourceFormatHint: formatDesc)
    input.expectsMediaDataInRealTime = false
    writer.add(input)
    guard writer.startWriting() else {
        throw HarnessError.internalFailure("writer refused to start: \(String(describing: writer.error))")
    }

    // Target timeline: segment 1 keeps source-A times; segment 2 is
    // shifted so its CUT lands exactly at segment 1's end.
    let seg1Start = MediaTime.time(ranges[0].startSec)
    let seg1End = MediaTime.time(ranges[0].endSec)
    let seg2CutStart = MediaTime.time(ranges[1].startSec)
    let seg2CutEnd = MediaTime.time(ranges[1].endSec)
    let seg2Shift = seg1End - seg2CutStart

    func copySegment(
        url: URL, from: CMTime, to: CMTime, shift: CMTime, sessionStart: CMTime, sessionEnd: CMTime
    ) async throws -> (appended: Int, leadIn: Int, firstWasSync: Bool?) {
        let loaded = try await cache.loaded(for: url)
        guard let track = loaded.videoTrack else {
            throw HarnessError.internalFailure("track missing")
        }
        let reader = try AVAssetReader(asset: loaded.asset)
        // Ask exactly for the cut range; whatever lead-in the reader
        // prepends (decode requirement) is part of the experiment.
        reader.timeRange = CMTimeRange(start: from, end: to)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
        reader.add(output)
        guard reader.startReading() else {
            throw HarnessError.internalFailure("reader: \(String(describing: reader.error))")
        }
        writer.startSession(atSourceTime: sessionStart)
        var appended = 0
        var leadIn = 0
        var firstWasSync: Bool?
        while let sample = output.copyNextSampleBuffer() {
            if firstWasSync == nil {
                firstWasSync = !sampleIsNotSync(sample)
            }
            let pts = CMSampleBufferGetPresentationTimeStamp(sample)
            if pts + shift < sessionStart { leadIn += 1 }
            let retimed = try retime(sample, by: shift)
            while !input.isReadyForMoreMediaData {
                try await Task.sleep(for: .milliseconds(2))
            }
            guard input.append(retimed) else {
                report.append("- append FAILED at sample \(appended) (writer status \(writer.status.rawValue), error: \(String(describing: writer.error)))")
                break
            }
            appended += 1
        }
        reader.cancelReading()
        writer.endSession(atSourceTime: sessionEnd)
        return (appended, leadIn, firstWasSync)
    }

    let s1 = try await copySegment(
        url: ranges[0].url, from: seg1Start, to: seg1End, shift: .zero,
        sessionStart: seg1Start, sessionEnd: seg1End)
    report.append("- segment 1: appended \(s1.appended) samples (\(s1.leadIn) lead-in before session start; reader's first sample sync=\(s1.firstWasSync.map(String.init) ?? "?"))")

    report.append("- attempting SECOND startSession (the question under test)…")
    let s2 = try await copySegment(
        url: ranges[1].url, from: seg2CutStart, to: seg2CutEnd, shift: seg2Shift,
        sessionStart: seg1End, sessionEnd: seg1End + (seg2CutEnd - seg2CutStart))
    report.append("- segment 2: appended \(s2.appended) samples (\(s2.leadIn) lead-in before session start; first sync=\(s2.firstWasSync.map(String.init) ?? "?"))")

    input.markAsFinished()
    await writer.finishWriting()
    guard writer.status == .completed else {
        report.append("- finishWriting FAILED: status=\(writer.status.rawValue) error=\(String(describing: writer.error)) → sequential sessions NOT viable; N12 fallback = per-segment writes + AVMutableMovie stitch")
        env.report("exportspike", report)
        return
    }
    report.append("- finishWriting: completed")

    // The verdict: is segment 2's lead-in edited out (Apple-side)?
    let asset = AVURLAsset(url: outURL)
    let duration = try await asset.load(.duration).seconds
    let expected = (ranges[0].endSec - ranges[0].startSec) + (ranges[1].endSec - ranges[1].startSec)
    report.append("- output duration: \(fmt(duration, 3))s (expected \(fmt(expected, 3))s if BOTH segments' lead-ins are edited out)")
    let item = AVPlayerItem(asset: asset)
    let player = AVPlayer(playerItem: item)
    player.isMuted = true
    let seg2StartInTarget = ranges[0].endSec - ranges[0].startSec  // presentation timeline starts at 0 via elst… probe both interpretations
    for probe in [0.001, seg2StartInTarget + 0.001] {
        let pixelBuffer = try await capturePixelBufferDirect(item: item, at: probe)
        let got = pixelBuffer.flatMap { PixelProbe.frameIndex(in: $0) }
        report.append("- frame at t=\(fmt(probe, 3)): index \(got.map(String.init) ?? "?")")
    }
    let edits = try MP4BoxParser.editLists(in: outURL)
    report.append("- elst boxes: \(edits.map { "×\($0.count)" }.joined(separator: ", ")) \(edits.flatMap { $0 }.map { "(dur \(fmt($0.segmentDurationSec, 3)) mt \(fmt($0.mediaTimeSec, 3)))" }.joined(separator: " "))")
    report.append("- file kept: export/spike-b-hybridwriter.mov")
    env.report("exportspike", report)
}

private func sampleIsNotSync(_ sample: CMSampleBuffer) -> Bool {
    guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false)
        as? [[CFString: Any]], let first = attachments.first else {
        return false  // no attachments array ⇒ all sync
    }
    return (first[kCMSampleAttachmentKey_NotSync] as? Bool) ?? false
}

private func retime(_ sample: CMSampleBuffer, by shift: CMTime) throws -> CMSampleBuffer {
    guard shift != .zero else { return sample }
    var count: CMItemCount = 0
    CMSampleBufferGetSampleTimingInfoArray(sample, entryCount: 0, arrayToFill: nil, entriesNeededOut: &count)
    var infos = [CMSampleTimingInfo](repeating: CMSampleTimingInfo(), count: count)
    CMSampleBufferGetSampleTimingInfoArray(sample, entryCount: count, arrayToFill: &infos, entriesNeededOut: &count)
    for i in 0..<count {
        if infos[i].presentationTimeStamp.isValid {
            infos[i].presentationTimeStamp = infos[i].presentationTimeStamp + shift
        }
        if infos[i].decodeTimeStamp.isValid {
            infos[i].decodeTimeStamp = infos[i].decodeTimeStamp + shift
        }
    }
    var retimed: CMSampleBuffer?
    let status = CMSampleBufferCreateCopyWithNewTiming(
        allocator: kCFAllocatorDefault, sampleBuffer: sample,
        sampleTimingEntryCount: count, sampleTimingArray: &infos,
        sampleBufferOut: &retimed)
    guard status == noErr, let retimed else {
        throw HarnessError.internalFailure("retime failed: \(status)")
    }
    return retimed
}

// MARK: - (c) elst A/B across demuxers

@MainActor
private func elstABExperiment(env: HarnessEnv) async throws {
    var report: [String] = ["**exportspike (c)** — elst A/B: Apple vs libav readings; files for QuickTime/VLC/Chrome eyeball"]
    // Standard-tier control (re-encode: no edit-list tricks, the universal
    // path) from the same splice.
    let ranges = try await spikeRanges(env)
    let builder = CompositionBuilder(assetCache: AssetCache())
    let built = try await builder.build(ranges: ranges, smoothCutAudio: false)
    let outURL = env.workdir.appendingPathComponent("export/spike-c-standard-reencode.mp4")
    try? FileManager.default.removeItem(at: outURL)
    guard let session = AVAssetExportSession(
        asset: built.composition, presetName: AVAssetExportPreset1920x1080) else {
        throw HarnessError.internalFailure("no re-encode session")
    }
    try await session.export(to: outURL, as: .mp4)
    report.append(contentsOf: try await analyzeSplice(
        url: outURL, built: built, env: env, label: "standard"))

    // Real-footage passthrough variants for ecosystem eyeballing: one
    // self-splice per inbox file (uniform geometry per file — the clean
    // elst question; non-keyframe cuts by odd-phase construction).
    let probed = await env.probedRealFiles()
    if probed.isEmpty {
        report.append("- real-footage passthrough: DEFERRED — footage inbox empty; re-run `exportspike c` when populated")
    }
    for real in probed.prefix(2) {
        let d = real.meta.duration.seconds
        let stem = real.url.deletingPathExtension().lastPathComponent
        let realRanges = [
            PlayableRange(url: real.url, startSec: d * 0.2 + 0.37, endSec: d * 0.2 + 3.87),
            PlayableRange(url: real.url, startSec: d * 0.6 + 0.13, endSec: d * 0.6 + 3.63),
        ]
        let expected = realRanges.reduce(0.0) { $0 + ($1.endSec - $1.startSec) }
        let realBuilt = try await builder.build(ranges: realRanges, smoothCutAudio: false)
        let realURL = env.workdir.appendingPathComponent("export/spike-c-real-\(stem).mov")
        try? FileManager.default.removeItem(at: realURL)
        guard let realSession = AVAssetExportSession(
            asset: realBuilt.composition, presetName: AVAssetExportPresetPassthrough) else {
            report.append("- real passthrough (\(stem)): AVAssetExportSession(passthrough) REFUSED the composition")
            continue
        }
        do {
            try await realSession.export(to: realURL, as: .mov)
            let duration = try await AVURLAsset(url: realURL).load(.duration).seconds
            let edits = try MP4BoxParser.editLists(in: realURL)
            let ffprobe = ffprobeOutput([
                "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=start_time,duration,nb_frames",
                "-of", "default=noprint_wrappers=1", realURL.path,
            ])
            report.append("- real passthrough (\(stem), self-splice): AVF duration \(fmt(duration, 3))s (expected \(fmt(expected, 3))s); elst \(edits.map { "×\($0.count)" }.joined(separator: ",")); libav: \(ffprobe.replacingOccurrences(of: "\n", with: " "))")
            report.append("- file kept: export/\(realURL.lastPathComponent)")
        } catch {
            report.append("- real passthrough (\(stem)) FAILED: \(error)")
        }
    }
    env.report("exportspike", report)
}

// MARK: - shell helper (harness measurement only)

/// Locate a measurement tool without assuming Homebrew-on-Apple-Silicon
/// paths (finding 14). Harness-only — the app-side ffmpeg path goes
/// through FFmpegLocator at N3.
func locateTool(_ name: String) -> String? {
    let candidates = [
        "/opt/homebrew/bin/\(name)", "/usr/local/bin/\(name)", "/usr/bin/\(name)",
    ]
    return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
}

func ffprobeOutput(_ arguments: [String]) -> String {
    guard let ffprobe = locateTool("ffprobe") else {
        return "ffprobe not found (looked in /opt/homebrew/bin, /usr/local/bin, /usr/bin) — libav leg skipped"
    }
    return shell(ffprobe, arguments)
}

/// Small-output helper only (reads the pipe after exit — fine for
/// ffprobe/ps one-liners, would deadlock on large output; noted at
/// review, accepted for harness use).
func shell(_ launchPath: String, _ arguments: [String]) -> String {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: launchPath)
    process.arguments = arguments
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    do {
        try process.run()
        process.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    } catch {
        return "shell error: \(error)"
    }
}
