import AVFoundation
import CoreMedia
import Foundation

/// Native metadata probing (D17 — replaces ffprobe). Async property loading
/// only; nothing here blocks. Frame math downstream uses `minFrameDuration`
/// (real sample timing); `nominalFrameRate` is carried for display only.

public struct VideoTrackInfo: Sendable, Equatable {
    /// Encoded (pre-transform) pixel dimensions.
    public let naturalSize: CGSize
    /// Track-level display matrix (iPhone portrait = 90° rotation here).
    public let preferredTransform: CGAffineTransform
    /// Real per-sample timing — the ONLY sanctioned basis for frame math.
    public let minFrameDuration: CMTime
    /// Average rate. Display only — never frame math (VFR iPhone footage).
    public let nominalFrameRate: Float
    public let timeRange: CMTimeRange
    /// FourCC of the first format description ("avc1", "hvc1", "apcn", …).
    public let codec: String
    /// From format-description color tags ("ITU_R_709_2",
    /// "ITU_R_2100_HLG", "SMPTE_ST_2084_PQ", …); nil when untagged.
    public let colorPrimaries: String?
    public let transferFunction: String?
    /// HLG or PQ transfer function ⇒ HDR (D29's per-source flag).
    public let isHDR: Bool

    /// Size after applying `preferredTransform` — what the viewer sees
    /// (portrait iPhone: 1920×1080 encoded → 1080×1920 oriented).
    public var orientedSize: CGSize {
        let r = CGRect(origin: .zero, size: naturalSize).applying(preferredTransform)
        return CGSize(width: abs(r.width), height: abs(r.height))
    }
}

public struct SourceMetadata: Sendable, Equatable {
    public let url: URL
    public let duration: CMTime
    public let video: VideoTrackInfo?
    public let hasAudio: Bool
    public let audioTimeRange: CMTimeRange?
}

public enum MetadataProbeError: Error, Equatable {
    /// One typed failure shape for every load that can go wrong inside the
    /// probe (duration, track lists, per-track properties) — N3's ingest
    /// consumes this and shouldn't have to catch raw AVFoundation errors
    /// (cold-review finding 7). `detail` carries the underlying error text
    /// for diagnostics.
    case unreadable(url: URL, detail: String)
}

public enum MetadataProbe {
    public static func probe(url: URL) async throws -> SourceMetadata {
        try await probe(asset: AVURLAsset(url: url))
    }

    static func probe(asset: AVURLAsset) async throws -> SourceMetadata {
        do {
            return try await probeUnchecked(asset: asset)
        } catch let error as MetadataProbeError {
            throw error
        } catch {
            throw MetadataProbeError.unreadable(
                url: asset.url, detail: String(describing: error))
        }
    }

    private static func probeUnchecked(asset: AVURLAsset) async throws -> SourceMetadata {
        let url = asset.url
        let duration = try await asset.load(.duration)

        var videoInfo: VideoTrackInfo?
        if let track = try await asset.loadTracks(withMediaType: .video).first {
            let (naturalSize, preferredTransform, minFrameDuration, nominalFrameRate, timeRange, formats) =
                try await track.load(
                    .naturalSize, .preferredTransform, .minFrameDuration,
                    .nominalFrameRate, .timeRange, .formatDescriptions
                )
            let format = formats.first
            let codec = format.map { fourCC($0.mediaSubType.rawValue) } ?? ""
            let primaries = format.flatMap { colorExtension($0, kCMFormatDescriptionExtension_ColorPrimaries) }
            let transfer = format.flatMap { colorExtension($0, kCMFormatDescriptionExtension_TransferFunction) }
            let hdrTransfers: Set<String> = [
                String(kCMFormatDescriptionTransferFunction_ITU_R_2100_HLG),
                String(kCMFormatDescriptionTransferFunction_SMPTE_ST_2084_PQ),
            ]
            videoInfo = VideoTrackInfo(
                naturalSize: naturalSize,
                preferredTransform: preferredTransform,
                minFrameDuration: minFrameDuration,
                nominalFrameRate: nominalFrameRate,
                timeRange: timeRange,
                codec: codec,
                colorPrimaries: primaries,
                transferFunction: transfer,
                isHDR: transfer.map { hdrTransfers.contains($0) } ?? false
            )
        }

        let audioTrack = try await asset.loadTracks(withMediaType: .audio).first
        let audioTimeRange = try await audioTrack?.load(.timeRange)

        return SourceMetadata(
            url: url,
            duration: duration,
            video: videoInfo,
            hasAudio: audioTrack != nil,
            audioTimeRange: audioTimeRange
        )
    }

    private static func colorExtension(
        _ format: CMFormatDescription, _ key: CFString
    ) -> String? {
        CMFormatDescriptionGetExtension(format, extensionKey: key) as? String
    }

    private static func fourCC(_ value: FourCharCode) -> String {
        let bytes = [
            UInt8((value >> 24) & 0xFF), UInt8((value >> 16) & 0xFF),
            UInt8((value >> 8) & 0xFF), UInt8(value & 0xFF),
        ]
        return String(bytes: bytes, encoding: .ascii) ?? ""
    }
}
