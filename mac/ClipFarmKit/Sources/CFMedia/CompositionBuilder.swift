import AVFoundation
import CFDomain
import CoreMedia
import Foundation

/// One playable span of one source file — CFMedia's range input.
/// Constructible from the resolver's `ResolvedRange` plus a source-URL
/// lookup (the resolver deals in source IDs; CFMedia deals in files).
/// Tombstones never reach the builder (§2.5 rule 7 — the resolver's
/// `.tombstone` items are UI chips, not playback placeholders).
public struct PlayableRange: Sendable, Equatable {
    public let url: URL
    public let startSec: Double
    public let endSec: Double

    public init(url: URL, startSec: Double, endSec: Double) {
        self.url = url
        self.startSec = startSec
        self.endSec = endSec
    }

    public init(resolved: ResolvedRange, url: URL) {
        self.init(
            url: url,
            startSec: resolved.effectiveStartSec,
            endSec: resolved.effectiveEndSec
        )
    }
}

/// Where a range landed on the composition timeline — the seek/seam map
/// the engine and the N2 instrumentation both consume.
public struct BuiltSegment: Sendable, Equatable {
    public let range: PlayableRange
    public let compositionStart: CMTime
    public let duration: CMTime
    /// Clamped source in-point actually inserted (rule 2 may pull the
    /// requested end in; the start only moves for degenerate requests).
    public let sourceStart: CMTime

    public var compositionEnd: CMTime { compositionStart + duration }
}

/// An immutable snapshot build (§2.5 rule 4): every edit produces a fresh
/// one of these; the engine swaps items, never mutates a live composition.
public struct CompositionBuildResult {
    public let composition: AVComposition
    public let videoComposition: AVVideoComposition?
    public let audioMix: AVAudioMix?
    public let segments: [BuiltSegment]

    public var duration: CMTime {
        segments.last?.compositionEnd ?? .zero
    }

    /// `@MainActor`: `AVPlayerItem(asset:)` is MainActor-isolated in the
    /// macOS 26 SDK (same platform shift N1 recorded for `UndoManager`).
    @MainActor
    public func makePlayerItem() -> AVPlayerItem {
        let item = AVPlayerItem(asset: composition)
        item.videoComposition = videoComposition
        item.audioMix = audioMix
        return item
    }
}

public enum CompositionBuildError: Error, Equatable {
    case emptyRangeList
    case missingVideoTrack(url: URL)
    /// Requested range clamped to nothing (start ≥ clamped end).
    case emptyClampedRange(url: URL, startSec: Double, endSec: Double)
}

public struct CompositionBuilder: Sendable {
    private let assetCache: AssetCache

    public init(assetCache: AssetCache) {
        self.assetCache = assetCache
    }

    /// Build a fresh composition for `ranges`, back-to-back.
    ///
    /// - Parameters:
    ///   - smoothCutAudio: D31 — ~10ms audio volume ramps at every internal
    ///     cut boundary. The caller reads it from `LibrarySettings`; the
    ///     same value must govern export (WYSIWYG).
    ///   - renderSize: canvas for the mixed-geometry videoComposition
    ///     (D32). Defaults to the first segment's oriented size.
    public func build(
        ranges: [PlayableRange],
        smoothCutAudio: Bool = true,
        renderSize: CGSize? = nil
    ) async throws -> CompositionBuildResult {
        guard !ranges.isEmpty else { throw CompositionBuildError.emptyRangeList }

        let composition = AVMutableComposition()
        // Rule 1: exactly one video track and one audio track, everything
        // inserted back-to-back (per-clip tracks cause decoder churn and
        // inter-track flicker).
        guard
            let videoTrack = composition.addMutableTrack(
                withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
            let audioTrack = composition.addMutableTrack(
                withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
        else {
            // addMutableTrack only fails on invalid media types; unreachable.
            throw CompositionBuildError.emptyRangeList
        }

        var cursor = CMTime.zero
        var segments: [BuiltSegment] = []
        var segmentVideoInfos: [VideoTrackInfo] = []

        for range in ranges {
            let loaded = try await assetCache.loaded(for: range.url)
            guard let sourceVideo = loaded.videoTrack, let info = loaded.metadata.video else {
                throw CompositionBuildError.missingVideoTrack(url: range.url)
            }

            // D12: Double crosses into CMTime here, once per boundary.
            let requested = CMTimeRange(
                start: MediaTime.time(range.startSec),
                end: MediaTime.time(range.endSec)
            )
            // Rule 2: both tracks are inserted from the SAME clamped range —
            // the min of the video/audio track durations — so the two
            // composition tracks can never drift (the classic tail-pop bug).
            let clamped = CompositionPlanner.clampedInsertRange(
                requested: requested,
                videoRange: info.timeRange,
                audioRange: loaded.audioTrack == nil ? nil : loaded.metadata.audioTimeRange
            )
            guard let clamped else {
                throw CompositionBuildError.emptyClampedRange(
                    url: range.url, startSec: range.startSec, endSec: range.endSec)
            }

            try videoTrack.insertTimeRange(clamped, of: sourceVideo, at: cursor)
            if let sourceAudio = loaded.audioTrack {
                try audioTrack.insertTimeRange(clamped, of: sourceAudio, at: cursor)
            } else {
                // Footage-only sources keep A/V alignment via silence.
                audioTrack.insertEmptyTimeRange(
                    CMTimeRange(start: cursor, duration: clamped.duration))
            }

            segments.append(BuiltSegment(
                range: range,
                compositionStart: cursor,
                duration: clamped.duration,
                sourceStart: clamped.start
            ))
            segmentVideoInfos.append(info)
            cursor = cursor + clamped.duration
        }

        // Rules 5 (D32 geometry) + 8 (D29 color): a videoComposition is
        // attached only when geometry or dynamic range mixes — uniform
        // sources keep the bare composition (and the Lossless door open).
        let geometryUniform = CompositionPlanner.isGeometryUniform(segmentVideoInfos)
        let rangesMix = CompositionPlanner.dynamicRangesMix(segmentVideoInfos)
        var videoComposition: AVVideoComposition?
        if geometryUniform {
            videoTrack.preferredTransform = segmentVideoInfos[0].preferredTransform
        }
        if !geometryUniform || rangesMix {
            videoComposition = Self.makeVideoComposition(
                track: videoTrack,
                segments: segments,
                infos: segmentVideoInfos,
                renderSize: renderSize,
                enforceSDR: rangesMix
            )
        }

        // Rule 6 (D31): ~10ms volume ramps at internal boundaries.
        var audioMix: AVAudioMix?
        if smoothCutAudio {
            let ramps = CompositionPlanner.fadeRamps(segments: segments)
            if !ramps.isEmpty {
                let parameters = AVMutableAudioMixInputParameters(track: audioTrack)
                for ramp in ramps {
                    parameters.setVolumeRamp(
                        fromStartVolume: ramp.fromVolume,
                        toEndVolume: ramp.toVolume,
                        timeRange: ramp.timeRange
                    )
                }
                let mix = AVMutableAudioMix()
                mix.inputParameters = [parameters]
                audioMix = (mix.copy() as! AVAudioMix)
            }
        }

        // Rule 4: hand back an immutable snapshot.
        return CompositionBuildResult(
            composition: composition.copy() as! AVComposition,
            videoComposition: videoComposition,
            audioMix: audioMix,
            segments: segments
        )
    }

    // MARK: - Mixed geometry / mixed color videoComposition (D32, D29)

    private static func makeVideoComposition(
        track: AVMutableCompositionTrack,
        segments: [BuiltSegment],
        infos: [VideoTrackInfo],
        renderSize: CGSize?,
        enforceSDR: Bool
    ) -> AVVideoComposition {
        let canvas = renderSize ?? infos[0].orientedSize

        let instructions: [AVVideoCompositionInstruction] = zip(segments, infos).map { segment, info in
            var layer = AVVideoCompositionLayerInstruction.Configuration(assetTrack: track)
            layer.setTransform(
                CompositionPlanner.fitTransform(
                    naturalSize: info.naturalSize,
                    preferredTransform: info.preferredTransform,
                    canvas: canvas
                ),
                at: segment.compositionStart
            )
            return AVVideoCompositionInstruction(configuration: .init(
                layerInstructions: [AVVideoCompositionLayerInstruction(configuration: layer)],
                timeRange: CMTimeRange(start: segment.compositionStart, duration: segment.duration)
            ))
        }

        // D29: with mixed dynamic ranges, the SDR default target is
        // ENFORCED, never assumed — a bare pipeline would convert SDR
        // segments UP to HDR on export, the opposite of the default. The
        // same properties ride this same object on the export path
        // (WYSIWYG).
        return AVVideoComposition(configuration: .init(
            colorPrimaries: enforceSDR ? String(kCMFormatDescriptionColorPrimaries_ITU_R_709_2) : nil,
            colorTransferFunction: enforceSDR ? String(kCMFormatDescriptionTransferFunction_ITU_R_709_2) : nil,
            colorYCbCrMatrix: enforceSDR ? String(kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2) : nil,
            // Highest segment rate wins so no segment judders.
            frameDuration: infos
                .map(\.minFrameDuration)
                .filter { $0.isNumeric && $0 > .zero }
                .min() ?? CMTime(value: 1, timescale: 30),
            instructions: instructions,
            renderSize: canvas
        ))
    }
}

// MARK: - Pure planning rules (unit-tested without media)

public enum CompositionPlanner {
    /// Rule 2. Returns nil when the clamp leaves nothing to insert.
    /// `audioRange == nil` means "no audio track" — video-only clamp.
    public static func clampedInsertRange(
        requested: CMTimeRange,
        videoRange: CMTimeRange,
        audioRange: CMTimeRange?
    ) -> CMTimeRange? {
        var start = max(requested.start, videoRange.start)
        var end = min(requested.end, videoRange.end)
        if let audioRange {
            start = max(start, audioRange.start)
            end = min(end, audioRange.end)
        }
        guard end > start else { return nil }
        return CMTimeRange(start: start, end: end)
    }

    /// D32: uniform ⇔ every segment shares one transform and one encoded
    /// size. Uniform → bare composition (Lossless-tier eligible); mixed →
    /// videoComposition with per-segment transforms.
    public static func isGeometryUniform(_ infos: [VideoTrackInfo]) -> Bool {
        guard let first = infos.first else { return true }
        return infos.allSatisfy {
            $0.naturalSize == first.naturalSize
                && $0.preferredTransform == first.preferredTransform
        }
    }

    /// D29: any HDR segment alongside any SDR segment.
    public static func dynamicRangesMix(_ infos: [VideoTrackInfo]) -> Bool {
        let flags = Set(infos.map(\.isHDR))
        return flags.count > 1
    }

    /// Orientation + aspect-fit + centering (pillarbox/letterbox default
    /// per D32; fill-crop is a later option).
    public static func fitTransform(
        naturalSize: CGSize,
        preferredTransform: CGAffineTransform,
        canvas: CGSize
    ) -> CGAffineTransform {
        let oriented = CGRect(origin: .zero, size: naturalSize).applying(preferredTransform)
        // Normalize so the oriented image's top-left sits at the origin.
        var transform = preferredTransform.concatenating(
            CGAffineTransform(translationX: -oriented.minX, y: -oriented.minY))
        let scale = min(canvas.width / abs(oriented.width), canvas.height / abs(oriented.height))
        transform = transform.concatenating(CGAffineTransform(scaleX: scale, y: scale))
        let scaledWidth = abs(oriented.width) * scale
        let scaledHeight = abs(oriented.height) * scale
        return transform.concatenating(CGAffineTransform(
            translationX: (canvas.width - scaledWidth) / 2,
            y: (canvas.height - scaledHeight) / 2
        ))
    }

    public struct FadeRamp: Equatable, Sendable {
        public let fromVolume: Float
        public let toVolume: Float
        public let timeRange: CMTimeRange
    }

    /// D31: ~10ms down-then-up ramps at each INTERNAL boundary (never at
    /// the assembly's own start/end). Short segments shrink the fade to
    /// half the segment so ramps can't overlap.
    public static func fadeRamps(
        segments: [BuiltSegment],
        fadeDuration: CMTime = CMTime(value: 6, timescale: 600)  // 10ms
    ) -> [FadeRamp] {
        guard segments.count > 1 else { return [] }
        var ramps: [FadeRamp] = []
        for (outgoing, incoming) in zip(segments, segments.dropFirst()) {
            let boundary = incoming.compositionStart
            let outFade = min(fadeDuration, CMTimeMultiplyByRatio(outgoing.duration, multiplier: 1, divisor: 2))
            let inFade = min(fadeDuration, CMTimeMultiplyByRatio(incoming.duration, multiplier: 1, divisor: 2))
            ramps.append(FadeRamp(
                fromVolume: 1, toVolume: 0,
                timeRange: CMTimeRange(start: boundary - outFade, end: boundary)
            ))
            ramps.append(FadeRamp(
                fromVolume: 0, toVolume: 1,
                timeRange: CMTimeRange(start: boundary, duration: inFade)
            ))
        }
        return ramps
    }
}
