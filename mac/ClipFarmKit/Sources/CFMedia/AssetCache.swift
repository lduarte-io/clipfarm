import AVFoundation
import Foundation

/// §2.5 rule 3: one `AVURLAsset` per source, properties pre-loaded, so
/// rebuilding a 50-clip composition is pure edit-list manipulation
/// (single-digit milliseconds — an N2 gate).
///
/// `LoadedAsset` is `@unchecked Sendable` deliberately: after async
/// property loading completes, `AVURLAsset` / `AVAssetTrack` are safe for
/// concurrent *reads* (Apple's async-load model exists precisely so the
/// loaded object can be consumed anywhere); nothing in ClipFarm mutates
/// them.
public struct LoadedAsset: @unchecked Sendable {
    public let asset: AVURLAsset
    public let videoTrack: AVAssetTrack?
    public let audioTrack: AVAssetTrack?
    public let metadata: SourceMetadata
}

public actor AssetCache {
    private var cache: [URL: LoadedAsset] = [:]

    public init() {}

    public func loaded(for url: URL) async throws -> LoadedAsset {
        if let hit = cache[url] { return hit }
        let asset = AVURLAsset(url: url)
        let metadata = try await MetadataProbe.probe(asset: asset)
        let loaded = LoadedAsset(
            asset: asset,
            videoTrack: try await asset.loadTracks(withMediaType: .video).first,
            audioTrack: try await asset.loadTracks(withMediaType: .audio).first,
            metadata: metadata
        )
        cache[url] = loaded
        return loaded
    }

    /// Source file replaced / repointed (N3+): drop the stale entry.
    public func invalidate(_ url: URL) {
        cache[url] = nil
    }

    public func removeAll() {
        cache.removeAll()
    }

    public var count: Int { cache.count }
}
