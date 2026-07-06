import Accelerate
import AVFoundation
import Foundation

/// Per-source waveform generation (plan §4/N3): decode the FIRST audio
/// track (N2 lesson: iPhone files can carry a second spatial-audio track —
/// never mix tracks together), peak magnitude per bucket at 100 buckets/sec
/// (the plan's 50–100 range), persisted as a binary cache file. Every clip's
/// waveform strip (N11 trim HUD, N15 aggressiveness UI) is then a free
/// slice of its source's buckets.
///
/// Runs asynchronously post-ingest (WaveformService is kicked off by the
/// app after `ingestFolder` returns) — ingest never blocks on audio decode,
/// and N11 degrades gracefully while a strip isn't ready.
///
/// The bucket loop is vDSP-vectorized from the start (N2 delta: debug-build
/// scalar hot loops run 20–50x slower; release is the shipping config).

public struct Waveform: Equatable, Sendable {
    public var bucketsPerSecond: Int
    /// Peak magnitude (0…1 for full-scale sources) per bucket. Empty for
    /// audio-less sources — distinct from "not generated yet" (no file).
    public var peaks: [Float]

    public init(bucketsPerSecond: Int, peaks: [Float]) {
        self.bucketsPerSecond = bucketsPerSecond
        self.peaks = peaks
    }
}

public enum WaveformError: Error {
    case unreadable(url: URL, detail: String)
    case malformedCache(URL)
}

public actor WaveformService {
    public static let defaultBucketsPerSecond = 100

    private let cacheDirectory: URL
    private let bucketsPerSecond: Int
    /// Memoized in-flight generation per source (the N2 fixture-actor
    /// reentrancy lesson: a bare check-then-generate suspends across the
    /// decode, and two callers would race the same cache path).
    private var inFlight: [String: Task<URL, Error>] = [:]

    /// `cacheDirectory`: `<library folder>/cache/waveforms` in the app
    /// (D28's `cache/`); any temp directory in tests.
    public init(cacheDirectory: URL, bucketsPerSecond: Int = WaveformService.defaultBucketsPerSecond) {
        self.cacheDirectory = cacheDirectory
        self.bucketsPerSecond = bucketsPerSecond
    }

    public func cacheFileURL(forSourceID sourceID: String) -> URL {
        cacheDirectory.appendingPathComponent("\(sourceID).waveform")
    }

    /// Generates (or returns the existing) waveform cache file for a source.
    /// Skip-if-exists: a valid cache file is never re-decoded; a truncated
    /// one (killed run) is regenerated.
    @discardableResult
    public func generateIfNeeded(forSourceID sourceID: String, sourceURL: URL) async throws -> URL {
        if let task = inFlight[sourceID] {
            return try await task.value
        }
        let destination = cacheFileURL(forSourceID: sourceID)
        if Self.cacheFileIsValid(at: destination) {
            return destination
        }
        let directory = cacheDirectory
        let buckets = bucketsPerSecond
        let task = Task {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let waveform = try await Self.computeWaveform(url: sourceURL, bucketsPerSecond: buckets)
            try Self.encode(waveform).write(to: destination, options: .atomic)
            return destination
        }
        inFlight[sourceID] = task
        do {
            return try await task.value
        } catch {
            // Failed generations don't poison the memo — a retry re-decodes.
            inFlight[sourceID] = nil
            throw error
        }
    }

    // MARK: - Decode (off-actor: @concurrent so the CPU loop never
    // serializes waveform requests behind each other)

    @concurrent
    static func computeWaveform(url: URL, bucketsPerSecond: Int) async throws -> Waveform {
        let asset = AVURLAsset(url: url)
        // FIRST audio track only.
        guard let track = try await asset.loadTracks(withMediaType: .audio).first else {
            return Waveform(bucketsPerSecond: bucketsPerSecond, peaks: [])
        }

        let reader: AVAssetReader
        do {
            reader = try AVAssetReader(asset: asset)
        } catch {
            throw WaveformError.unreadable(url: url, detail: String(describing: error))
        }
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ])
        output.alwaysCopiesSampleData = false
        guard reader.canAdd(output) else {
            throw WaveformError.unreadable(url: url, detail: "reader cannot add track output")
        }
        reader.add(output)
        guard reader.startReading() else {
            throw WaveformError.unreadable(
                url: url, detail: String(describing: reader.error))
        }

        var samplesPerBucket = 0
        var peaks: [Float] = []
        // Carry between sample buffers: buckets don't align to buffer edges.
        var carry: [Float] = []

        while let sampleBuffer = output.copyNextSampleBuffer() {
            guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { continue }

            if samplesPerBucket == 0,
               let format = CMSampleBufferGetFormatDescription(sampleBuffer),
               let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee {
                // Peak-per-bucket over INTERLEAVED samples: a bucket spans
                // frames*channels contiguous floats, so multichannel peaks
                // fold in naturally (display waveform, not a mixdown).
                let channels = max(1, Int(asbd.mChannelsPerFrame))
                samplesPerBucket = max(1, Int(asbd.mSampleRate) * channels / bucketsPerSecond)
            }
            guard samplesPerBucket > 0 else { continue }

            let byteCount = CMBlockBufferGetDataLength(blockBuffer)
            let sampleCount = byteCount / MemoryLayout<Float>.size
            guard sampleCount > 0 else { continue }
            let existing = carry.count
            carry.append(contentsOf: repeatElement(0, count: sampleCount))
            carry.withUnsafeMutableBytes { raw in
                _ = CMBlockBufferCopyDataBytes(
                    blockBuffer, atOffset: 0, dataLength: byteCount,
                    destination: raw.baseAddress!.advanced(by: existing * MemoryLayout<Float>.size)
                )
            }

            appendFullBuckets(from: &carry, samplesPerBucket: samplesPerBucket, into: &peaks)
        }

        if reader.status == .failed {
            throw WaveformError.unreadable(url: url, detail: String(describing: reader.error))
        }
        // Trailing partial bucket.
        if !carry.isEmpty, samplesPerBucket > 0 {
            var peak: Float = 0
            vDSP_maxmgv(carry, 1, &peak, vDSP_Length(carry.count))
            peaks.append(peak)
        }
        return Waveform(bucketsPerSecond: bucketsPerSecond, peaks: peaks)
    }

    private static func appendFullBuckets(
        from carry: inout [Float], samplesPerBucket: Int, into peaks: inout [Float]
    ) {
        let fullBuckets = carry.count / samplesPerBucket
        guard fullBuckets > 0 else { return }
        carry.withUnsafeBufferPointer { pointer in
            for bucket in 0..<fullBuckets {
                var peak: Float = 0
                vDSP_maxmgv(
                    pointer.baseAddress! + bucket * samplesPerBucket, 1,
                    &peak, vDSP_Length(samplesPerBucket))
                peaks.append(peak)
            }
        }
        carry.removeFirst(fullBuckets * samplesPerBucket)
    }

    // MARK: - Cache file format
    // "CFWV" | UInt32 version=1 | UInt32 bucketsPerSecond | UInt32 count |
    // count x Float32 — all little-endian.

    static let cacheMagic = Data("CFWV".utf8)
    static let cacheVersion: UInt32 = 1
    static let headerSize = 4 + 4 + 4 + 4

    static func encode(_ waveform: Waveform) -> Data {
        var data = cacheMagic
        appendUInt32(&data, cacheVersion)
        appendUInt32(&data, UInt32(waveform.bucketsPerSecond))
        appendUInt32(&data, UInt32(waveform.peaks.count))
        waveform.peaks.withUnsafeBufferPointer { pointer in
            data.append(UnsafeBufferPointer(
                start: UnsafeRawPointer(pointer.baseAddress!)
                    .assumingMemoryBound(to: UInt8.self),
                count: pointer.count * MemoryLayout<Float>.size
            ))
        }
        return data
    }

    public static func read(from url: URL) throws -> Waveform {
        let data = try Data(contentsOf: url)
        guard data.count >= headerSize,
              data.prefix(4) == cacheMagic,
              readUInt32(data, at: 4) == cacheVersion
        else {
            throw WaveformError.malformedCache(url)
        }
        let bucketsPerSecond = Int(readUInt32(data, at: 8))
        let count = Int(readUInt32(data, at: 12))
        guard data.count == headerSize + count * MemoryLayout<Float>.size else {
            throw WaveformError.malformedCache(url)
        }
        var peaks = [Float](repeating: 0, count: count)
        _ = peaks.withUnsafeMutableBytes { raw in
            data.copyBytes(to: raw, from: headerSize..<data.count)
        }
        return Waveform(bucketsPerSecond: bucketsPerSecond, peaks: peaks)
    }

    static func cacheFileIsValid(at url: URL) -> Bool {
        (try? read(from: url)) != nil
    }

    private static func appendUInt32(_ data: inout Data, _ value: UInt32) {
        withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
    }

    private static func readUInt32(_ data: Data, at offset: Int) -> UInt32 {
        var value: UInt32 = 0
        _ = withUnsafeMutableBytes(of: &value) { raw in
            data.copyBytes(to: raw, from: offset..<(offset + 4))
        }
        return UInt32(littleEndian: value)
    }
}
