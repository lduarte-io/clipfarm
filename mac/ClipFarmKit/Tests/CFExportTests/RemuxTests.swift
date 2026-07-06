import Foundation
import Testing
@testable import CFExport

/// FFmpegLocator + MKVRemuxer (D15/D16). The remux integration tests run
/// against the real ffmpeg binary and skip visibly (`.enabled(if:)`) on
/// machines without one — the locator tests always run.

private let ffmpegAvailable = FFmpegLocator().locate() != nil

@Test func locatorHonorsOverridePathFirst() throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-locator-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }

    let fake = scratch.appendingPathComponent("ffmpeg")
    FileManager.default.createFile(
        atPath: fake.path,
        contents: Data("#!/bin/sh\n".utf8),
        attributes: [.posixPermissions: 0o755]
    )
    let located = FFmpegLocator(overridePath: fake.path).locate(environment: [:])
    #expect(located == fake)

    // A bad override is a nil result, never a silent fallback — the user
    // pointed at something specific; falling back would hide their mistake.
    let bad = FFmpegLocator(overridePath: scratch.appendingPathComponent("nope").path)
    #expect(bad.locate(environment: ["PATH": scratch.path]) == nil)
}

@Test func locatorSearchesPATHThenWellKnownDirectories() throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-locator-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }

    let onPath = scratch.appendingPathComponent("ffmpeg")
    FileManager.default.createFile(
        atPath: onPath.path,
        contents: Data("#!/bin/sh\n".utf8),
        attributes: [.posixPermissions: 0o755]
    )
    let located = FFmpegLocator().locate(environment: ["PATH": "/nonexistent:\(scratch.path)"])
    #expect(located == onPath)

    // Empty PATH: either nil or a well-known-directory hit — never a crash.
    let fallback = FFmpegLocator().locate(environment: [:])
    if let fallback {
        #expect(FFmpegLocator.wellKnownDirectories.contains(fallback.deletingLastPathComponent().path))
    }
}

// MARK: - Real-ffmpeg integration

/// Renders a tiny real `.mkv` via ffmpeg's lavfi test source. Using ffmpeg
/// to create the fixture is deliberate: AVFoundation cannot write Matroska,
/// and these tests already require ffmpeg to be meaningful.
private func makeTinyMKV(in directory: URL, name: String) async throws -> URL {
    let ffmpeg = try #require(FFmpegLocator().locate())
    let out = directory.appendingPathComponent(name)
    let process = Process()
    process.executableURL = ffmpeg
    process.arguments = [
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.5:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
        "-c:v", "h264_videotoolbox", "-b:v", "200k",
        "-c:a", "aac",
        out.path,
    ]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    process.waitUntilExit()
    try #require(process.terminationStatus == 0, "fixture mkv render failed")
    return out
}

@Test(.enabled(if: ffmpegAvailable))
func remuxProducesSiblingMP4WithSameStem() async throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-remux-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }

    let mkv = try await makeTinyMKV(in: scratch, name: "cam.take.1.mkv")
    let mp4 = try await MKVRemuxer.remux(mkvURL: mkv)

    // Sibling, same (dotted) stem, .mp4 extension; original untouched.
    #expect(mp4 == scratch.appendingPathComponent("cam.take.1.mp4"))
    #expect(FileManager.default.fileExists(atPath: mp4.path))
    #expect(FileManager.default.fileExists(atPath: mkv.path))
    let size = try FileManager.default.attributesOfItem(atPath: mp4.path)[.size] as? Int ?? 0
    #expect(size > 0)
    // No temp litter left behind.
    let leftovers = try FileManager.default.contentsOfDirectory(atPath: scratch.path)
        .filter { $0.contains(".remux-") }
    #expect(leftovers.isEmpty)
}

@Test(.enabled(if: ffmpegAvailable))
func remuxSkipsWhenSiblingMP4AlreadyExists() async throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-remux-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }

    let mkv = try await makeTinyMKV(in: scratch, name: "take.mkv")
    let existing = scratch.appendingPathComponent("take.mp4")
    let marker = Data("pre-existing".utf8)
    try marker.write(to: existing)

    let mp4 = try await MKVRemuxer.remux(mkvURL: mkv)
    #expect(mp4 == existing)
    // Skip-if-exists means untouched, byte-for-byte.
    #expect(try Data(contentsOf: existing) == marker)
}

@Test(.enabled(if: ffmpegAvailable))
func failedRemuxThrowsWithStderrTailAndLeavesNoMP4() async throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-remux-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }

    // Not a real Matroska file — ffmpeg must fail.
    let bogus = scratch.appendingPathComponent("broken.mkv")
    try Data("this is not matroska".utf8).write(to: bogus)

    await #expect {
        _ = try await MKVRemuxer.remux(mkvURL: bogus)
    } throws: { error in
        guard case MKVRemuxError.remuxFailed = error else { return false }
        return true
    }
    #expect(!FileManager.default.fileExists(atPath: scratch.appendingPathComponent("broken.mp4").path))
    let leftovers = try FileManager.default.contentsOfDirectory(atPath: scratch.path)
        .filter { $0.contains(".remux-") }
    #expect(leftovers.isEmpty)
}

@Test func missingFFmpegThrowsTypedError() async throws {
    let scratch = FileManager.default.temporaryDirectory
        .appendingPathComponent("cfexport-remux-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: scratch) }
    let mkv = scratch.appendingPathComponent("orphan.mkv")
    try Data().write(to: mkv)

    // A locator whose override points nowhere resolves to nil.
    let noFFmpeg = FFmpegLocator(overridePath: scratch.appendingPathComponent("missing").path)
    await #expect {
        _ = try await MKVRemuxer.remux(mkvURL: mkv, locator: noFFmpeg)
    } throws: { error in
        guard case MKVRemuxError.ffmpegNotFound = error else { return false }
        return true
    }
}
