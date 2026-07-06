import Foundation

/// N2 gate harness (PHASES.md → N2). One subcommand per exit gate; every
/// run appends numbers to `<workdir>/reports/`. Real material comes from
/// the footage inbox `~/ClipFarm/Footage/` (D34) — read-only here, adapts
/// to whatever Lillian dropped in, falls back to synthetic fixtures (with
/// a report flag) when no qualifying file exists. Everything the harness
/// produces is regenerable.
///
///   swift run n2harness fixtures                 # render the synthetic set
///   swift run n2harness seams uniform|real|mixed|solo # seam instrumentation (each = single-track vs alternating-tracks A/B)
///   swift run n2harness blink [--cycles N]       # swap-blink A/B
///   swift run n2harness rotation                 # D32 mixed-rotation probe
///   swift run n2harness hdrseam                  # D29 HDR↔SDR probe + export
///   swift run n2harness rebuild                  # <10ms rebuild + edit→frame
///   swift run n2harness frameacc                 # frame accuracy + stepping
///   swift run n2harness looptest [--loops N]     # trim-loop restart, 4K HEVC
///   swift run n2harness fades                    # micro-fade pop/onset math
///   swift run n2harness exportspike a|b|c|all    # the half-day export spike
///   swift run n2harness demo [--real] [--selfcheck]  # watch window (--selfcheck auto-quits ~6s)
///   swift run n2harness all                      # every headless gate
///
/// Options: --workdir <dir> (default ~/ClipFarm/outputs — Lillian's one-visible-place rule),
/// --footage <dir> (default ~/ClipFarm/Footage — the D34 inbox).
enum HarnessError: Error {
    case usage(String)
    case internalFailure(String)
}

@main
struct N2Harness {
    static func main() async {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard let command = arguments.first else {
            print("usage: n2harness <fixtures|seams|blink|rotation|hdrseam|rebuild|frameacc|looptest|fades|exportspike|demo|all> [options]")
            exit(2)
        }
        do {
            let env = try HarnessEnv(arguments: Array(arguments.dropFirst()))
            try await run(command: command, arguments: Array(arguments.dropFirst()), env: env)
        } catch let HarnessError.usage(message) {
            print("usage error: \(message)")
            exit(2)
        } catch {
            print("n2harness \(command) FAILED: \(error)")
            exit(1)
        }
        exit(0)
    }

    static func run(command: String, arguments: [String], env: HarnessEnv) async throws {
        func intOption(_ name: String, default defaultValue: Int) -> Int {
            guard let i = arguments.firstIndex(of: name), i + 1 < arguments.count,
                  let value = Int(arguments[i + 1]) else { return defaultValue }
            return value
        }

        switch command {
        case "fixtures":
            for spec in FixtureSet.all {
                let start = Date()
                let url = try await env.ensureFixture(spec)
                print("fixture \(spec.name): \(url.lastPathComponent) (\(fmt(Date().timeIntervalSince(start), 1))s)")
            }
        case "seams":
            try await runSeams(env: env, variant: arguments.first ?? "uniform")
        case "blink":
            try await runBlink(
                env: env, cycles: intOption("--cycles", default: 100),
                forceFixture: arguments.contains("--fixture"))
        case "rotation":
            try await runRotation(env: env)
        case "hdrseam":
            try await runHDRSeam(env: env)
        case "rebuild":
            try await runRebuild(env: env)
        case "frameacc":
            try await runFrameAccuracy(env: env)
        case "looptest":
            try await runLoop(
                env: env, loops: intOption("--loops", default: 50),
                escalationOnly: arguments.contains("--escalation-only"))
        case "fades":
            try await runFades(env: env)
        case "exportspike":
            try await runExportSpike(env: env, experiment: arguments.first ?? "all")
        case "demo":
            try await runDemo(
                env: env, realOnly: arguments.contains("--real"),
                selfCheck: arguments.contains("--selfcheck"))
        case "all":
            try await runSeams(env: env, variant: "uniform")
            try await runSeams(env: env, variant: "real")
            try await runSeams(env: env, variant: "mixed")
            try await runSeams(env: env, variant: "solo")
            try await runBlink(env: env, cycles: 100)
            try await runRotation(env: env)
            try await runHDRSeam(env: env)
            try await runRebuild(env: env)
            try await runFrameAccuracy(env: env)
            try await runLoop(env: env, loops: 50)
            try await runFades(env: env)
            try await runExportSpike(env: env, experiment: "all")
        default:
            throw HarnessError.usage("unknown command \(command)")
        }
    }
}
