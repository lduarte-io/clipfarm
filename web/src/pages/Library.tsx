import { useEffect, useState } from "react";

// Spec → "first launch": Phase 2 ships the absolute-path text input. Browser
// sandbox can't surface real filesystem paths from a folder picker, so
// typing/pasting the path is the pragmatic v0 affordance.

type Source = {
  filename: string;
  path: string;
  duration_sec: number | null;
  fps: number | null;
  transcript_path: string | null;
  added_at: string;
  unavailable: boolean;
};

type IngestRejection = {
  filename: string;
  reason: string;
  sanitized_rename: string | null;
  detail: string;
};

type IngestResult = {
  sources_added: string[];
  sources_skipped: string[];
  sources_updated: string[];
  rejected: IngestRejection[];
  warnings: string[];
  clips_detected: number;
};

type State = {
  version: number;
  sources: Record<string, Source>;
  clips: Record<string, { source_id: string }>;
};

function formatDuration(sec: number | null): string {
  if (sec == null) return "—";
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}h${String(m).padStart(2, "0")}m`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export default function Library() {
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<State | null>(null);

  async function refreshState() {
    const r = await fetch("/api/state");
    if (r.ok) setState(await r.json());
  }

  useEffect(() => {
    refreshState();
  }, []);

  async function runIngest() {
    if (!folder.trim()) {
      setError("Enter an absolute folder path.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ folder }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({ detail: r.statusText }));
        setError(typeof body.detail === "string" ? body.detail : JSON.stringify(body));
        return;
      }
      const result: IngestResult = await r.json();
      setLastResult(result);
      await refreshState();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const sources = state ? Object.entries(state.sources) : [];
  const clipsBySource: Record<string, number> = {};
  if (state) {
    for (const c of Object.values(state.clips)) {
      clipsBySource[c.source_id] = (clipsBySource[c.source_id] ?? 0) + 1;
    }
  }

  return (
    <section className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Library</h1>
        <p className="text-neutral-400 text-sm mt-1">
          Drop in a folder of <code>.mov</code> files (each paired with its{" "}
          <code>&lt;name&gt;.whisper.json</code> sidecar). The raw-transcript
          browser lands in Phase 3 — for now you can ingest and see what
          ClipFarm picked up.
        </p>
      </header>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 p-4 space-y-3">
        <label className="block text-sm font-medium">
          Folder (absolute path)
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            className="flex-1 rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm font-mono"
            placeholder="/Users/you/Desktop/.../mp4files/05.19.26"
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            disabled={busy}
            spellCheck={false}
          />
          <button
            onClick={runIngest}
            disabled={busy}
            className="rounded-md bg-white text-neutral-950 font-medium px-4 py-2 text-sm hover:bg-neutral-200 disabled:opacity-50"
          >
            {busy ? "Ingesting…" : "Ingest"}
          </button>
        </div>
        {error && (
          <div className="text-red-400 text-sm whitespace-pre-wrap">{error}</div>
        )}
        {lastResult && (
          <div className="text-sm space-y-1 pt-2 border-t border-neutral-800">
            <div className="text-neutral-300">
              <span className="text-green-400">+{lastResult.sources_added.length} added</span>
              {" · "}
              <span className="text-blue-400">↑{lastResult.sources_updated.length} updated</span>
              {" · "}
              <span className="text-neutral-500">={lastResult.sources_skipped.length} skipped</span>
              {" · "}
              <span className="text-neutral-300">{lastResult.clips_detected} clips detected</span>
            </div>
            {lastResult.rejected.length > 0 && (
              <details className="mt-2">
                <summary className="text-amber-400 cursor-pointer">
                  {lastResult.rejected.length} rejected — click to expand
                </summary>
                <ul className="mt-2 ml-4 list-disc text-amber-200 space-y-1">
                  {lastResult.rejected.map((r) => (
                    <li key={r.filename}>
                      <code>{r.filename}</code> — {r.reason}
                      {r.sanitized_rename && (
                        <span>
                          {" "}
                          → suggest <code>{r.sanitized_rename}</code>
                        </span>
                      )}
                      {r.detail && (
                        <div className="text-xs text-amber-300/70 ml-2">{r.detail}</div>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {lastResult.warnings.length > 0 && (
              <details className="mt-1">
                <summary className="text-neutral-400 cursor-pointer">
                  {lastResult.warnings.length} warning
                  {lastResult.warnings.length === 1 ? "" : "s"}
                </summary>
                <ul className="mt-1 ml-4 list-disc text-neutral-400 space-y-1 text-xs">
                  {lastResult.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">
          Sources{" "}
          <span className="text-neutral-500 text-sm font-normal">
            ({sources.length})
          </span>
        </h2>
        {sources.length === 0 ? (
          <div className="text-neutral-500 text-sm">
            No sources yet. Ingest a folder above to populate the library.
          </div>
        ) : (
          <div className="rounded-md border border-neutral-800 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-neutral-900 text-neutral-400">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">Filename</th>
                  <th className="text-right px-3 py-2 font-medium">Duration</th>
                  <th className="text-right px-3 py-2 font-medium">fps</th>
                  <th className="text-right px-3 py-2 font-medium">Clips</th>
                  <th className="text-left px-3 py-2 font-medium">Transcript</th>
                </tr>
              </thead>
              <tbody>
                {sources.map(([sid, src]) => (
                  <tr
                    key={sid}
                    className={`border-t border-neutral-800 ${
                      src.unavailable ? "opacity-50" : ""
                    }`}
                  >
                    <td className="px-3 py-2 font-mono">
                      {src.filename}
                      {src.unavailable && (
                        <span className="ml-2 text-xs text-amber-400">unavailable</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">{formatDuration(src.duration_sec)}</td>
                    <td className="px-3 py-2 text-right">
                      {src.fps != null ? src.fps.toFixed(2) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">{clipsBySource[sid] ?? 0}</td>
                    <td className="px-3 py-2 text-neutral-400 text-xs">
                      {src.transcript_path ? "ok" : "footage-only"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
