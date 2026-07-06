import Foundation
import Subprocess
import System

/// `.mkv` → `.mp4` lossless remux at ingest (D15: AVFoundation cannot open
/// Matroska; `ffmpeg -c copy` is seconds and bit-identical streams).
///
/// Contract (plan §4/N3): the remuxed `.mp4` lands as a **sibling of the
/// original with the same stem**, skip-if-exists; the caller records the
/// `.mp4` as `sources.path` and keeps the `.mkv` as `original_path`
/// provenance. The write is temp-file + atomic rename so a killed ffmpeg
/// never leaves a half-written `.mp4` that skip-if-exists would later trust.
public enum MKVRemuxError: Error {
    /// No ffmpeg binary found (D16 locator returned nil). The user-facing
    /// resolution: install ffmpeg (Homebrew) or set its path in Settings.
    case ffmpegNotFound
    /// ffmpeg exited non-zero; `stderrTail` carries its last lines.
    case remuxFailed(exitDescription: String, stderrTail: String)
}

public enum MKVRemuxer {
    /// Remuxes `mkvURL` to a sibling `<stem>.mp4` and returns that URL.
    /// Skip-if-exists: an existing sibling `.mp4` is returned untouched
    /// (assumed to be an earlier remux — re-ingest stays idempotent).
    public static func remux(
        mkvURL: URL,
        locator: FFmpegLocator = FFmpegLocator()
    ) async throws -> URL {
        let destination = mkvURL.deletingPathExtension().appendingPathExtension("mp4")
        if FileManager.default.fileExists(atPath: destination.path) {
            return destination
        }
        guard let ffmpeg = locator.locate() else {
            throw MKVRemuxError.ffmpegNotFound
        }

        // Same-directory temp name so the final step is an atomic rename on
        // one filesystem. Dot-prefixed: never picked up by a folder scan.
        let temp = mkvURL.deletingLastPathComponent()
            .appendingPathComponent(".\(mkvURL.deletingPathExtension().lastPathComponent).remux-\(UUID().uuidString.prefix(8)).mp4")
        defer { try? FileManager.default.removeItem(at: temp) }

        // `-map 0:v? -map 0:a?`: copy every video and audio stream, tolerate
        // either being absent; subtitle/attachment streams (common in mkv,
        // unrepresentable in mp4) are deliberately not mapped.
        let arguments: [String] = [
            "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", mkvURL.path,
            "-map", "0:v?", "-map", "0:a?",
            "-c", "copy",
            "-f", "mp4",
            temp.path,
        ]

        // Streaming stderr (D16): collected-output modes throw past a byte
        // limit, and ffmpeg is chatty — drain the stream, keep the tail for
        // the error message.
        let configuration = Configuration(
            executable: .path(FilePath(ffmpeg.path)),
            arguments: Arguments(arguments)
        )
        let result = try await run(
            configuration,
            input: .none,
            output: .discarded,
            error: .sequence
        ) { execution in
            var tail: [String] = []
            for try await line in execution.standardError.strings() {
                let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { continue }
                tail.append(trimmed)
                if tail.count > 20 { tail.removeFirst() }
            }
            return tail
        }

        guard result.terminationStatus.isSuccess else {
            throw MKVRemuxError.remuxFailed(
                exitDescription: String(describing: result.terminationStatus),
                stderrTail: result.closureResult.joined(separator: "\n")
            )
        }
        // Atomic move into place (same directory, same filesystem). If the
        // destination appeared while ffmpeg ran (shouldn't — ingest is a
        // single flow), skip-if-exists semantics win: keep the existing file.
        if FileManager.default.fileExists(atPath: destination.path) {
            return destination
        }
        try FileManager.default.moveItem(at: temp, to: destination)
        return destination
    }
}
