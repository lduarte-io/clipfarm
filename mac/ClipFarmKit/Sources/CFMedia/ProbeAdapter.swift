import CFDomain
import CoreGraphics
import Foundation

/// Adapter from CFMedia's full probe (D17) to the slim value ingest records
/// on a `Source`. Persistence back to rest-land is the sanctioned direction
/// for `MediaTime.seconds`.
extension SourceMetadata {
    public var probedSourceInfo: ProbedSourceInfo {
        let durationSec = MediaTime.seconds(duration)
        return ProbedSourceInfo(
            durationSec: durationSec.isFinite && durationSec > 0 ? durationSec : nil,
            // Display fps only (N2 delta): nominalFrameRate, never anything
            // derived from minFrameDuration.
            fps: video.map { Double($0.nominalFrameRate) },
            isHDR: video?.isHDR,
            naturalWidth: video.map { Int($0.naturalSize.width.rounded()) },
            naturalHeight: video.map { Int($0.naturalSize.height.rounded()) }
        )
    }
}
