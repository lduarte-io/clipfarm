/// What ingest records from a media probe. Produced by CFMedia (an adapter
/// over `MetadataProbe` — D17) and consumed by CFStore's ingest orchestrator;
/// it lives in CFDomain so neither module needs to depend on the other, and
/// so store tests can stub probing exactly the way the reference tests
/// stubbed `probe_video`.
public struct ProbedSourceInfo: Equatable, Sendable {
    public var durationSec: Double?
    /// `nominalFrameRate` — **display only** (N2 delta: never frame math,
    /// never `1/minFrameDuration`, which reads absurdly high on VFR files).
    public var fps: Double?
    /// HLG/PQ transfer function per D29; `nil` when the file has no video
    /// track (or the probe failed).
    public var isHDR: Bool?
    /// Encoded (pre-transform) pixel dimensions.
    public var naturalWidth: Int?
    public var naturalHeight: Int?

    public init(
        durationSec: Double? = nil,
        fps: Double? = nil,
        isHDR: Bool? = nil,
        naturalWidth: Int? = nil,
        naturalHeight: Int? = nil
    ) {
        self.durationSec = durationSec
        self.fps = fps
        self.isHDR = isHDR
        self.naturalWidth = naturalWidth
        self.naturalHeight = naturalHeight
    }
}
