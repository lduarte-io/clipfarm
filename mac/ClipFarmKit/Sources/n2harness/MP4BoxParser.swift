import Foundation

/// Minimal MP4/QuickTime box walker — just enough to answer "did the
/// export author edit lists, and what do they say" without external tools.
/// Walks moov → trak → edts → elst; returns one entry array per track.
enum MP4BoxParser {
    struct EditEntry {
        /// In movie-timescale seconds.
        let segmentDurationSec: Double
        /// In media-timescale seconds; -1 = empty edit.
        let mediaTimeSec: Double
        let rate: Double
    }

    static func editLists(in url: URL) throws -> [[EditEntry]] {
        let data = try Data(contentsOf: url, options: .mappedIfSafe)
        guard let moov = findBox(type: "moov", in: data, range: 0..<data.count) else {
            return []
        }
        let movieTimescale = parseMvhdTimescale(in: data, moov: moov) ?? 600

        var results: [[EditEntry]] = []
        var cursor = moov.contentRange.lowerBound
        while let trak = findBox(type: "trak", in: data, range: cursor..<moov.contentRange.upperBound) {
            let mediaTimescale = parseMdhdTimescale(in: data, trak: trak) ?? movieTimescale
            if let edts = findBox(type: "edts", in: data, range: trak.contentRange),
               let elst = findBox(type: "elst", in: data, range: edts.contentRange) {
                results.append(parseElst(
                    data: data, box: elst,
                    movieTimescale: Double(movieTimescale),
                    mediaTimescale: Double(mediaTimescale)))
            } else {
                results.append([])
            }
            cursor = trak.range.upperBound
        }
        return results
    }

    private struct Box {
        let type: String
        let range: Range<Int>         // header included
        let contentRange: Range<Int>  // header excluded
    }

    private static func findBox(type: String, in data: Data, range: Range<Int>) -> Box? {
        var offset = range.lowerBound
        while offset + 8 <= range.upperBound {
            let size = Int(readUInt32(data, offset))
            let boxType = String(
                bytes: data[data.startIndex + offset + 4..<data.startIndex + offset + 8],
                encoding: .ascii) ?? ""
            var boxSize = size
            var headerSize = 8
            if size == 1 {  // 64-bit size
                guard offset + 16 <= range.upperBound else { return nil }
                boxSize = Int(readUInt64(data, offset + 8))
                headerSize = 16
            } else if size == 0 {
                boxSize = range.upperBound - offset
            }
            guard boxSize >= headerSize, offset + boxSize <= range.upperBound else { return nil }
            if boxType == type {
                return Box(
                    type: boxType,
                    range: offset..<(offset + boxSize),
                    contentRange: (offset + headerSize)..<(offset + boxSize))
            }
            offset += boxSize
        }
        return nil
    }

    private static func parseMvhdTimescale(in data: Data, moov: Box) -> UInt32? {
        guard let mvhd = findBox(type: "mvhd", in: data, range: moov.contentRange) else { return nil }
        let start = mvhd.contentRange.lowerBound
        let version = data[data.startIndex + start]
        return readUInt32(data, start + (version == 1 ? 20 : 12))
    }

    private static func parseMdhdTimescale(in data: Data, trak: Box) -> UInt32? {
        guard let mdia = findBox(type: "mdia", in: data, range: trak.contentRange),
              let mdhd = findBox(type: "mdhd", in: data, range: mdia.contentRange) else { return nil }
        let start = mdhd.contentRange.lowerBound
        let version = data[data.startIndex + start]
        return readUInt32(data, start + (version == 1 ? 20 : 12))
    }

    private static func parseElst(
        data: Data, box: Box, movieTimescale: Double, mediaTimescale: Double
    ) -> [EditEntry] {
        let start = box.contentRange.lowerBound
        let version = data[data.startIndex + start]
        let count = Int(readUInt32(data, start + 4))
        var entries: [EditEntry] = []
        var offset = start + 8
        for _ in 0..<count {
            if version == 1 {
                guard offset + 20 <= box.contentRange.upperBound else { break }
                let duration = Double(readUInt64(data, offset))
                let mediaTime = Double(Int64(bitPattern: readUInt64(data, offset + 8)))
                let rate = Double(Int32(bitPattern: readUInt32(data, offset + 16))) / 65536.0
                entries.append(EditEntry(
                    segmentDurationSec: duration / movieTimescale,
                    mediaTimeSec: mediaTime < 0 ? -1 : mediaTime / mediaTimescale,
                    rate: rate))
                offset += 20
            } else {
                guard offset + 12 <= box.contentRange.upperBound else { break }
                let duration = Double(readUInt32(data, offset))
                let mediaTime = Double(Int32(bitPattern: readUInt32(data, offset + 4)))
                let rate = Double(Int32(bitPattern: readUInt32(data, offset + 8))) / 65536.0
                entries.append(EditEntry(
                    segmentDurationSec: duration / movieTimescale,
                    mediaTimeSec: mediaTime < 0 ? -1 : mediaTime / mediaTimescale,
                    rate: rate))
                offset += 12
            }
        }
        return entries
    }

    private static func readUInt32(_ data: Data, _ offset: Int) -> UInt32 {
        let i = data.startIndex + offset
        return UInt32(data[i]) << 24 | UInt32(data[i + 1]) << 16
            | UInt32(data[i + 2]) << 8 | UInt32(data[i + 3])
    }

    private static func readUInt64(_ data: Data, _ offset: Int) -> UInt64 {
        UInt64(readUInt32(data, offset)) << 32 | UInt64(readUInt32(data, offset + 4))
    }
}
