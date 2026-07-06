import CoreMedia

/// The D12 time-policy seam. `Double` seconds live at rest (CFDomain,
/// CFStore, the resolver); they cross into media-land HERE, exactly once,
/// as `CMTime` — and every calculation past this point stays in `CMTime`.
/// The drift bug class only exists under iterative Double accumulation;
/// convert-once eliminates it.
///
/// Frame math elsewhere uses the track's real timing (`minFrameDuration`),
/// never `nominalFrameRate` (an average — wrong on VFR iPhone footage).
public enum MediaTime {
    /// 600 is the classic QuickTime timescale: exact for 24/25/30/60 fps.
    public static let timescale: CMTimeScale = 600

    /// The one sanctioned Double → CMTime conversion.
    public static func time(_ seconds: Double) -> CMTime {
        CMTime(seconds: seconds, preferredTimescale: timescale)
    }

    /// For display / persistence back to rest-land only — never for
    /// round-trip arithmetic.
    public static func seconds(_ time: CMTime) -> Double {
        CMTimeGetSeconds(time)
    }
}
