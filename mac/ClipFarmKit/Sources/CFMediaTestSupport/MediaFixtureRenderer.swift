import AVFoundation
import CoreMedia
import CoreVideo
import Foundation
import VideoToolbox

/// Deterministic synthetic media for CFMediaTests and the N2 gate harness
/// (PHASES.md → N2 PROVISIONAL 1: the dogfood folder is uniformly H.264
/// 720p SDR landscape; gates need ProRes / HEVC / 4K / HLG-HDR / portrait).
///
/// Every frame is self-identifying: the top eighth of the frame carries the
/// frame index as 16 black/white bit blocks (MSB leftmost), the bottom
/// eighth carries orientation markers (left half red, right half blue).
/// That makes frame accuracy, seam drops, stale frames, and rotation
/// programmatically checkable from delivered pixel buffers.
///
/// Never ships. Renders into caller-chosen directories only (fixtures are
/// regenerable — nothing here touches footage folders).

public enum FixtureCodec: String, Sendable, CaseIterable {
    case h264
    case hevc
    /// 10-bit HLG BT.2020 HEVC — iPhone-HDR-style (D29 gate material).
    case hevc10HLG
    /// All-intra (keyframe interval is ignored by the encoder).
    case proRes422
}

public enum FixtureAudio: Sendable, Hashable {
    case none
    /// Continuous 440 Hz sine, amplitude 0.4 — cutting mid-phase produces
    /// a step discontinuity (the pop the micro-fades must kill).
    case sine440
    /// 440 Hz bed at 0.15 plus a 1 kHz, 250 ms burst at amplitude 0.7
    /// starting at every whole 2 s — cut on a burst start to test that
    /// fades don't soften onsets.
    case sineWithBursts
}

public struct MediaFixtureSpec: Sendable, Hashable {
    public var name: String
    public var codec: FixtureCodec
    public var width: Int
    public var height: Int
    public var fps: Int32
    public var durationSec: Double
    /// Long-GOP control (AVVideoMaxKeyFrameIntervalKey), in frames.
    public var keyframeIntervalFrames: Int
    /// iPhone-portrait-style: encoded landscape + 90° track transform.
    public var rotated90: Bool
    /// Background gray (BGRA value) — the HDR seam probe uses matched
    /// levels across an SDR/HDR pair.
    public var grayLevel: UInt8
    public var averageBitRate: Int?
    public var audio: FixtureAudio

    public init(
        name: String, codec: FixtureCodec, width: Int, height: Int,
        fps: Int32 = 30, durationSec: Double, keyframeIntervalFrames: Int = 60,
        rotated90: Bool = false, grayLevel: UInt8 = 118,
        averageBitRate: Int? = nil, audio: FixtureAudio = .sine440
    ) {
        self.name = name
        self.codec = codec
        self.width = width
        self.height = height
        self.fps = fps
        self.durationSec = durationSec
        self.keyframeIntervalFrames = keyframeIntervalFrames
        self.rotated90 = rotated90
        self.grayLevel = grayLevel
        self.averageBitRate = averageBitRate
        self.audio = audio
    }

    public var frameCount: Int { Int(durationSec * Double(fps)) }
    public var fileName: String { "\(name).mov" }
}

public enum MediaFixtureError: Error {
    case writerFailed(String)
    case pixelBufferPoolUnavailable
}

public enum MediaFixtureRenderer {
    /// Render `spec` into `directory` (skip-if-exists: fixtures are
    /// deterministic, keyed by name).
    public static func render(
        _ spec: MediaFixtureSpec, in directory: URL, force: Bool = false
    ) async throws -> URL {
        let url = directory.appendingPathComponent(spec.fileName)
        if !force, FileManager.default.fileExists(atPath: url.path) { return url }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? FileManager.default.removeItem(at: url)

        let writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        let videoInput = AVAssetWriterInput(
            mediaType: .video, outputSettings: videoSettings(for: spec))
        videoInput.expectsMediaDataInRealTime = false
        if spec.rotated90 {
            videoInput.transform = CGAffineTransform(rotationAngle: .pi / 2)
        }
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: videoInput,
            sourcePixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                kCVPixelBufferWidthKey as String: spec.width,
                kCVPixelBufferHeightKey as String: spec.height,
            ]
        )
        writer.add(videoInput)

        var audioInput: AVAssetWriterInput?
        if spec.audio != .none {
            let input = AVAssetWriterInput(
                mediaType: .audio,
                outputSettings: [
                    AVFormatIDKey: kAudioFormatLinearPCM,
                    AVSampleRateKey: 48_000,
                    AVNumberOfChannelsKey: 1,
                    AVLinearPCMBitDepthKey: 16,
                    AVLinearPCMIsFloatKey: false,
                    AVLinearPCMIsBigEndianKey: false,
                    AVLinearPCMIsNonInterleaved: false,
                ]
            )
            input.expectsMediaDataInRealTime = false
            writer.add(input)
            audioInput = input
        }

        guard writer.startWriting() else {
            throw MediaFixtureError.writerFailed(String(describing: writer.error))
        }
        writer.startSession(atSourceTime: .zero)

        try await feedVideo(spec: spec, input: videoInput, adaptor: adaptor)
        if let audioInput {
            try await feedAudio(spec: spec, input: audioInput)
        }

        await writer.finishWriting()
        guard writer.status == .completed else {
            throw MediaFixtureError.writerFailed(String(describing: writer.error))
        }
        return url
    }

    // MARK: - Video

    private static func videoSettings(for spec: MediaFixtureSpec) -> [String: Any] {
        var compression: [String: Any] = [:]
        var settings: [String: Any] = [
            AVVideoWidthKey: spec.width,
            AVVideoHeightKey: spec.height,
        ]
        switch spec.codec {
        case .h264:
            settings[AVVideoCodecKey] = AVVideoCodecType.h264.rawValue
            compression[AVVideoMaxKeyFrameIntervalKey] = spec.keyframeIntervalFrames
            settings[AVVideoColorPropertiesKey] = sdrColorProperties
        case .hevc:
            settings[AVVideoCodecKey] = AVVideoCodecType.hevc.rawValue
            compression[AVVideoMaxKeyFrameIntervalKey] = spec.keyframeIntervalFrames
            settings[AVVideoColorPropertiesKey] = sdrColorProperties
        case .hevc10HLG:
            settings[AVVideoCodecKey] = AVVideoCodecType.hevc.rawValue
            compression[AVVideoMaxKeyFrameIntervalKey] = spec.keyframeIntervalFrames
            compression[AVVideoProfileLevelKey] = kVTProfileLevel_HEVC_Main10_AutoLevel as String
            // Input buffers are tagged BT.709; VideoToolbox performs the
            // documented conversion to the declared output color space, so
            // the HLG file's nominal scene matches its SDR sibling.
            settings[AVVideoColorPropertiesKey] = [
                AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_2020,
                AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_2100_HLG,
                AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_2020,
            ]
        case .proRes422:
            settings[AVVideoCodecKey] = AVVideoCodecType.proRes422.rawValue
            settings[AVVideoColorPropertiesKey] = sdrColorProperties
        }
        if let bitRate = spec.averageBitRate, spec.codec != .proRes422 {
            compression[AVVideoAverageBitRateKey] = bitRate
        }
        if !compression.isEmpty {
            settings[AVVideoCompressionPropertiesKey] = compression
        }
        return settings
    }

    private static var sdrColorProperties: [String: Any] {
        [
            AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
            AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
            AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
        ]
    }

    private static func feedVideo(
        spec: MediaFixtureSpec,
        input: AVAssetWriterInput,
        adaptor: AVAssetWriterInputPixelBufferAdaptor
    ) async throws {
        let queue = DispatchQueue(label: "fixture.video")
        // Safe: the callback runs on the one serial queue passed in — this
        // is AVFoundation's documented feeding pattern.
        nonisolated(unsafe) let input = input
        nonisolated(unsafe) let adaptor = adaptor
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            nonisolated(unsafe) var frame = 0
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    if frame >= spec.frameCount {
                        input.markAsFinished()
                        cont.resume()
                        return
                    }
                    guard let pool = adaptor.pixelBufferPool else {
                        input.markAsFinished()
                        cont.resume(throwing: MediaFixtureError.pixelBufferPoolUnavailable)
                        return
                    }
                    var pixelBuffer: CVPixelBuffer?
                    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pixelBuffer)
                    guard let pixelBuffer else {
                        input.markAsFinished()
                        cont.resume(throwing: MediaFixtureError.pixelBufferPoolUnavailable)
                        return
                    }
                    draw(frameIndex: frame, gray: spec.grayLevel, into: pixelBuffer)
                    adaptor.append(
                        pixelBuffer,
                        withPresentationTime: CMTime(value: CMTimeValue(frame), timescale: spec.fps)
                    )
                    frame += 1
                }
            }
        }
    }

    /// BGRA layout: top eighth = 16 frame-index bit blocks (MSB leftmost),
    /// bottom eighth = red left half / blue right half, gray elsewhere.
    private static func draw(frameIndex: Int, gray: UInt8, into pixelBuffer: CVPixelBuffer) {
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let bitRowEnd = height / 8
        let markerRowStart = height - height / 8

        for y in 0..<height {
            let row = base.advanced(by: y * bytesPerRow).assumingMemoryBound(to: UInt8.self)
            if y < bitRowEnd {
                for x in 0..<width {
                    let bit = 15 - min(15, x * 16 / width)
                    let on = (frameIndex >> bit) & 1 == 1
                    let v: UInt8 = on ? 235 : 16
                    row[x * 4] = v; row[x * 4 + 1] = v; row[x * 4 + 2] = v; row[x * 4 + 3] = 255
                }
            } else if y >= markerRowStart {
                for x in 0..<width {
                    if x < width / 2 {
                        row[x * 4] = 16; row[x * 4 + 1] = 16; row[x * 4 + 2] = 220  // red
                    } else {
                        row[x * 4] = 220; row[x * 4 + 1] = 16; row[x * 4 + 2] = 16  // blue
                    }
                    row[x * 4 + 3] = 255
                }
            } else {
                for x in 0..<width {
                    row[x * 4] = gray; row[x * 4 + 1] = gray; row[x * 4 + 2] = gray
                    row[x * 4 + 3] = 255
                }
            }
        }

        // Tag input as BT.709 so the HLG encode performs a real conversion.
        CVBufferSetAttachment(
            pixelBuffer, kCVImageBufferColorPrimariesKey,
            kCVImageBufferColorPrimaries_ITU_R_709_2, .shouldPropagate)
        CVBufferSetAttachment(
            pixelBuffer, kCVImageBufferTransferFunctionKey,
            kCVImageBufferTransferFunction_ITU_R_709_2, .shouldPropagate)
        CVBufferSetAttachment(
            pixelBuffer, kCVImageBufferYCbCrMatrixKey,
            kCVImageBufferYCbCrMatrix_ITU_R_709_2, .shouldPropagate)
    }

    // MARK: - Audio (LPCM — codec-artifact-free for fade analysis)

    private static func feedAudio(spec: MediaFixtureSpec, input: AVAssetWriterInput) async throws {
        let sampleRate = 48_000
        let totalSamples = Int(spec.durationSec * Double(sampleRate))
        let chunkSamples = sampleRate / 2

        var asbd = AudioStreamBasicDescription(
            mSampleRate: Float64(sampleRate),
            mFormatID: kAudioFormatLinearPCM,
            mFormatFlags: kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
            mBytesPerPacket: 2, mFramesPerPacket: 1, mBytesPerFrame: 2,
            mChannelsPerFrame: 1, mBitsPerChannel: 16, mReserved: 0
        )
        var formatDesc: CMAudioFormatDescription?
        CMAudioFormatDescriptionCreate(
            allocator: nil, asbd: &asbd, layoutSize: 0, layout: nil,
            magicCookieSize: 0, magicCookie: nil, extensions: nil,
            formatDescriptionOut: &formatDesc
        )
        guard let formatDesc else { throw MediaFixtureError.writerFailed("audio format") }

        let queue = DispatchQueue(label: "fixture.audio")
        // Safe: serial-queue callback — the documented feeding pattern.
        nonisolated(unsafe) let input = input
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            nonisolated(unsafe) var cursor = 0
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    if cursor >= totalSamples {
                        input.markAsFinished()
                        cont.resume()
                        return
                    }
                    let count = min(chunkSamples, totalSamples - cursor)
                    var samples = [Int16](repeating: 0, count: count)
                    for i in 0..<count {
                        let t = Double(cursor + i) / Double(sampleRate)
                        samples[i] = Int16(max(-1, min(1, sampleValue(spec.audio, at: t))) * 32000)
                    }
                    do {
                        let buffer = try makeAudioSampleBuffer(
                            samples: samples, formatDesc: formatDesc,
                            presentationSample: cursor, sampleRate: sampleRate)
                        input.append(buffer)
                    } catch {
                        input.markAsFinished()
                        cont.resume(throwing: error)
                        return
                    }
                    cursor += count
                }
            }
        }
    }

    private static func sampleValue(_ pattern: FixtureAudio, at t: Double) -> Double {
        switch pattern {
        case .none:
            return 0
        case .sine440:
            return 0.4 * sin(2 * .pi * 440 * t)
        case .sineWithBursts:
            let bed = 0.15 * sin(2 * .pi * 440 * t)
            let phase = t.truncatingRemainder(dividingBy: 2.0)
            let burst = phase < 0.25 ? 0.7 * sin(2 * .pi * 1000 * t) : 0
            return bed + burst
        }
    }

    private static func makeAudioSampleBuffer(
        samples: [Int16], formatDesc: CMAudioFormatDescription,
        presentationSample: Int, sampleRate: Int
    ) throws -> CMSampleBuffer {
        let byteCount = samples.count * 2
        var blockBuffer: CMBlockBuffer?
        var status = CMBlockBufferCreateWithMemoryBlock(
            allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: byteCount,
            blockAllocator: kCFAllocatorDefault, customBlockSource: nil,
            offsetToData: 0, dataLength: byteCount, flags: 0, blockBufferOut: &blockBuffer
        )
        guard status == noErr, let blockBuffer else {
            throw MediaFixtureError.writerFailed("block buffer: \(status)")
        }
        status = samples.withUnsafeBytes {
            CMBlockBufferReplaceDataBytes(
                with: $0.baseAddress!, blockBuffer: blockBuffer,
                offsetIntoDestination: 0, dataLength: byteCount)
        }
        guard status == noErr else {
            throw MediaFixtureError.writerFailed("block fill: \(status)")
        }
        var sampleBuffer: CMSampleBuffer?
        status = CMAudioSampleBufferCreateReadyWithPacketDescriptions(
            allocator: kCFAllocatorDefault, dataBuffer: blockBuffer,
            formatDescription: formatDesc, sampleCount: samples.count,
            presentationTimeStamp: CMTime(
                value: CMTimeValue(presentationSample), timescale: CMTimeScale(sampleRate)),
            packetDescriptions: nil, sampleBufferOut: &sampleBuffer
        )
        guard status == noErr, let sampleBuffer else {
            throw MediaFixtureError.writerFailed("sample buffer: \(status)")
        }
        return sampleBuffer
    }
}
